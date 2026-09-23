"""
PSOCT event-driven mosaic processing flow.

Triggered by ``mosaic.ready`` when all batches in a mosaic are processed.
Handles coordinate determination (Fiji stitch + process_tile_coord),
template generation (Jinja2), and stitching for all modalities.

Event payloads must include ``mosaic_ident``. Stitching parameters come from the
project config block; the payload may override any of the keys listed in
:obj:`opticstream.flows.psoct.utils.PROCESS_MOSAIC_FLOW_KWARGS_KEYS` (same
pattern as LSM strip flows).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from prefect import flow, task
from prefect.logging import get_run_logger

from opticstream.hooks.check_slice_ready_hook import check_slice_ready_hook
from opticstream.hooks.publish_hooks import (
    publish_oct_mosaic_hook,
    publish_oct_project_hook,
)
from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.events.utils import get_event_trigger
from opticstream.flows.psoct.utils import (
    MosaicContext,
    get_dandi_slice_path,
    get_mosaic_nifti_path,
    get_mosaic_tile_info_path,
    get_slice_paths,
    load_scan_config_for_payload,
    mosaic_context_from_ident,
    mosaic_ident_from_payload,
)
from opticstream.state.state_guards import (
    enter_flow_stage,
    force_rerun_from_payload,
    should_skip_run,
)
from opticstream.state.oct_project_state import OCTMosaicId, OCT_STATE_SERVICE
from opticstream.data_processing.qc import (
    generate_mask,
)
from opticstream.flows.psoct.mosaic_coordinate_flow import (
    generate_tile_info_file,
    process_stitching_coordinates,
)
from opticstream.events import (
    MOSAIC_READY,
    MOSAIC_ENFACE_STITCHED,
)
from opticstream.events.psoct_event_emitters import emit_mosaic_psoct_event
from opticstream.flows.psoct.tile_batch_processed_validation import (
    validate_output_files_exist_and_min_size,
)
from opticstream.hooks.slack_notification_hook import slack_notification_hook


@task
def generate_mask_task(
    input_image: Path,
    output_mask: Path,
    threshold: float = 60.0,
) -> None:
    """
    Generate mask from input image.

    Parameters
    ----------
    input_image : str
        Path to input image (e.g., AIP or MIP)
    output_mask : str
        Path to output mask file
    threshold : float
        Threshold for mask generation

    """
    logger = get_run_logger()
    logger.info(f"Generating mask from {input_image} with threshold {threshold}")
    generate_mask.main(
        input=input_image,
        output=output_mask,
        threshold=threshold,
    )

    logger.info(f"Generated mask: {output_mask}")


@task
def validate_mosaic_enface_outputs_task(
    enface_outputs: Dict[str, Path],
    *,
    min_file_size_bytes: int = 1 * 1024 * 1024,
) -> None:
    outputs = [
        (f"modality={modality}", path)
        for modality, path in enface_outputs.items()
        if modality != "mask"
    ]
    validate_output_files_exist_and_min_size(
        outputs,
        min_file_size_bytes=min_file_size_bytes,
        context="Mosaic enface outputs",
    )


@task(task_run_name="symlink-enface-to-dandi-{mosaic_ident}")
def symlink_enface_to_dandi(
    enface_outputs: Dict[str, Path],
    dandi_slice_path: Path,
    mosaic_ident: OCTMosaicId,
    ctx: MosaicContext,
    mosaic_enface_format: str,
) -> Dict[str, Path]:
    """
    Create symlinks from stitched enface nifti files to DANDI slice directory.

    Uses mosaic_enface_format template to generate target filenames.
    Target directory: {dandiset_path}/sample-slice{slice_id:03d}/

    Parameters
    ----------
    enface_outputs : Dict[str, str]
        Modality name to stitched NIfTI path
    dandi_slice_path : Path
        Path to DANDI slice directory
    project_name : str
        Project identifier
    mosaic_id : int
        Mosaic identifier
    mosaic_enface_format : str
        Template string for enface filename format

    Returns
    -------
    Dict[str, Path]
        Dictionary mapping modality to symlink target path
    """
    logger = get_run_logger()
    logger.info(
        f"Creating symlinks for enface files to DANDI directory: {dandi_slice_path}"
    )

    # Ensure DANDI slice directory exists
    dandi_slice_path.mkdir(parents=True, exist_ok=True)

    symlink_targets = {}
    errors = []
    for modality, nifti_path in enface_outputs.items():
        if not nifti_path:
            logger.warning(f"Skipping modality {modality}: empty path")
            continue

        source_path = Path(nifti_path)
        if not source_path.exists():
            logger.warning(
                f"Skipping modality {modality}: source file does not exist: {source_path}"
            )
            continue

        try:
            target_filename = mosaic_enface_format.format(
                project_name=mosaic_ident.project_name,
                slice_id=mosaic_ident.slice_id,
                acq=ctx.acquisition_label,
                modality=modality,
            )
        except KeyError as e:
            logger.error(f"Error formatting template for modality {modality}: {e}")
            errors.append(f"Error formatting template for modality {modality}: {e}")
            continue

        target_path = dandi_slice_path / target_filename

        # Create symlink
        try:
            if target_path.exists() or target_path.is_symlink():
                if target_path.is_symlink():
                    target_path.unlink()
                else:
                    logger.warning(
                        f"Target already exists (not a symlink), skipping: {target_path}"
                    )
                    continue

            target_path.symlink_to(source_path)
            symlink_targets[modality] = target_path
            logger.info(f"Created symlink: {target_path} -> {source_path}")
        except OSError as e:
            logger.error(f"Failed to create symlink for {modality}: {e}")
            errors.append(f"Failed to create symlink for modality {modality}: {e}")
            continue
    if errors:
        raise RuntimeError(f"Errors occurred during symlink creation: {errors}")
    logger.info(f"Created {len(symlink_targets)} symlinks for enface modalities")
    return symlink_targets


@task(task_run_name="stitch-enface-modality-{modality}")
def stitch_enface_modality(
    modality: str,
    mosaic_id: int,
    template_path: Path,
    processed_path: Path,
    stitched_path: Path,
    scan_resolution_2d: List[float],
    mask_path: Optional[Path] = None,
    circular_mean: Optional[bool] = None,
) -> Path:
    """
    Generate tile info file and stitch a 2D modality (NIfTI).

    This helper function encapsulates the common pattern of generating a tile info
    file from a template and running mosaic2d. It handles:
    - Tile info file generation with optional mask
    - Automatic circular_mean detection for "ori" modality

    Parameters
    ----------
    modality : str
        Modality name (e.g., "aip", "mip", "ret", "ori", "biref", "surf")
    mosaic_id : int
        Mosaic identifier
    template_path : Path
        Path to Jinja2 template for tile info files
    processed_path : Path
        Path to processed tiles directory
    stitched_path : Path
        Path to stitched output directory
    scan_resolution_2d : List[float]
        Scan resolution for 2D [x, y]
    mask_path : Path, optional
        Path to mask file (None for modalities without mask like AIP/MIP)
    circular_mean : bool, optional
        Whether to use circular mean for orientation data.
        If None, automatically determined from modality name ("ori" -> True)

    Returns
    -------
    Path
        Path to output NIfTI file.
    """
    logger = get_run_logger()

    # Determine circular_mean if not provided
    if circular_mean is None:
        circular_mean = modality == "ori"

    tile_info_path = get_mosaic_tile_info_path(stitched_path, mosaic_id, modality)
    nifti_path = get_mosaic_nifti_path(stitched_path, mosaic_id, modality)

    # Generate tile_info_file using template
    generate_tile_info_file(
        template_path=template_path,
        output_path=tile_info_path,
        base_dir=processed_path,
        modality=modality,
        mosaic_id=mosaic_id,
        mask=mask_path,
        scan_resolution=scan_resolution_2d,
    )

    logger.info(f"Stitching 2D modality '{modality}' from {tile_info_path}")
    from linc_convert.modalities.psoct.mosaic import mosaic2d

    mosaic2d(
        tile_info_file=str(tile_info_path),
        nifti_output=str(nifti_path),
        circular_mean=bool(circular_mean),
    )
    logger.info(f"Stitched 2D modality '{modality}' saved to {nifti_path}")

    return nifti_path


@flow(flow_run_name="stitch-enface-{mosaic_ident}")
def stitch_enface_modalities(
    mosaic_ident: OCTMosaicId,
    config: PSOCTScanConfigModel,
    template_path: Path,
    processed_path: Path,
    stitched_path: Path,
    scan_resolution_2d: List[float],
	mask_path: Optional[Path] = None,
) -> Dict[str, Path]:
    """
    Subflow to stitch all 2D enface modalities.

    Stitches all enface modalities (ret, ori, biref, surf) asynchronously.
    MIP and AIP are excluded as they are already stitched without mask.

    Parameters
    ----------
    mosaic_ident : OCTMosaicId
        Mosaic identifier
    config : PSOCTScanConfigModel
        Configuration object
    template_path : Path
        Path to Jinja2 template for tile info files
    processed_path : Path
        Path to processed tiles directory
    stitched_path : Path
        Path to stitched output directory
    mask_path : Path
        Path to mask file
    scan_resolution_2d : List[float]
        Scan resolution for 2D [x, y]

    Returns
    -------
    Dict[str, str]
        Modality name to stitched NIfTI path
    """
    logger = get_run_logger()
    mosaic_id = mosaic_ident.mosaic_id
    logger.info(f"Stitching enface modalities for mosaic {mosaic_id}")

    enface_futures = {}
    for modality in (m.value for m in config.enface_modalities):
        if modality in ["mip", "aip"]:
            continue  # Skip MIP and AIP, already stitched

        # Use unified helper function for tile info generation and stitching
        future = stitch_enface_modality.submit(
            modality=modality,
            mosaic_id=mosaic_id,
            template_path=template_path,
            processed_path=processed_path,
            stitched_path=stitched_path,
            scan_resolution_2d=scan_resolution_2d,
            mask_path=mask_path,
            circular_mean=None,  # Auto-determined from modality name
        )
        enface_futures[modality] = future

    # Wait for all enface stitching to complete
    enface_outputs: Dict[str, Path] = {}
    for modality, future in enface_futures.items():
        enface_outputs[modality] = future.result()

    logger.info(f"All enface modalities stitched for mosaic {mosaic_id}")

    return enface_outputs


@flow(
    flow_run_name="process-{mosaic_ident}",
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook, check_slice_ready_hook],
    on_failure=[slack_notification_hook],
)
def process_mosaic(
    mosaic_ident: OCTMosaicId,
    config: PSOCTScanConfigModel,
    *,
    apply_mask: bool = False,
    force_refresh_coords: bool = False,
    force_rerun: bool = False,
) -> Dict[str, Path]:
    """
    Event-driven flow triggered by 'mosaic.processed' event.
    Processes a complete mosaic: determines coordinates, generates template,
    and stitches all modalities.

    Note:
    - This flow expects all parameters to be provided. Event flows handle loading
      config blocks and providing defaults.
    - Coordinate determination (Fiji stitch + process_tile_coord) only runs for
      mosaic_001 (normal) and mosaic_002 (tilted) unless force_refresh_coords=True.
    - Jinja2 template is generated once per illumination type and reused for all
      mosaics of that type.

    Parameters
    ----------
    Returns
    -------
    Dict[str, Path]
        Dictionary mapping modality to output path
    """
    logger = get_run_logger()

    if should_skip_run(
        enter_flow_stage(
            OCT_STATE_SERVICE.peek_mosaic(mosaic_ident=mosaic_ident),
            force_rerun=force_rerun,
            skip_if_running=False,
            item_ident=mosaic_ident,
        )
    ):
        return

    ctx = mosaic_context_from_ident(mosaic_ident, config)
    mosaic_id = mosaic_ident.mosaic_id
    project_base_path = config.project_base_path
    mosaic_enface_format = config.mosaic_enface_format
    dandiset_path = config.dandiset_path
    _, processed_path, stitched_path, _ = get_slice_paths(
        project_base_path=project_base_path,
        slice_id=mosaic_ident.slice_id,
    )
    scan_resolution_2d = config.acquisition.scan_resolution_3d[:2]
    stitched_path.mkdir(parents=True, exist_ok=True)

    base_mosaic_id = ctx.base_mosaic_id
    template_path = Path(project_base_path) / ctx.template_name

    if ctx.is_first_slice or force_refresh_coords:
        template_path, _ = process_stitching_coordinates(
            mosaic_ident=mosaic_ident,
            config=config,
            ctx=ctx,
        )
    else:
        logger.info(
            f"Skipping coordinate determination for mosaic {mosaic_id}. "
            f"Using template from mosaic {base_mosaic_id})"
        )

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template not found: {template_path}. "
            f"Coordinate determination must be run for mosaic {base_mosaic_id} first."
        )

    # Step 4: Stitch AIP and MIP in parallel (both without mask) - asynchronous
    # Use unified task for both modalities; compute output paths here for downstream tasks.
    aip_nifti = get_mosaic_nifti_path(stitched_path, mosaic_id, "aip")
    mip_nifti = get_mosaic_nifti_path(stitched_path, mosaic_id, "mip")

    aip_future = stitch_enface_modality.submit(
        modality="aip",
        mosaic_id=mosaic_id,
        template_path=template_path,
        processed_path=processed_path,
        stitched_path=stitched_path,
        scan_resolution_2d=scan_resolution_2d,
        mask_path=None,  # AIP doesn't use mask
        circular_mean=False,
    )

    mip_future = stitch_enface_modality.submit(
        modality="mip",
        mosaic_id=mosaic_id,
        template_path=template_path,
        processed_path=processed_path,
        stitched_path=stitched_path,
        scan_resolution_2d=scan_resolution_2d,
        mask_path=None,  # MIP doesn't use mask
        circular_mean=False,
    )

    # Step 5: Generate mask
    mask_path = get_mosaic_nifti_path(stitched_path, mosaic_id, "mask")

    mask_future = generate_mask_task.submit(
        input_image=aip_nifti,
        output_mask=mask_path,
        threshold=ctx.mask_threshold(config),
        wait_for=[aip_future, mip_future],
    )
    mask_future.wait()

    # Step 6: Stitch all enface modalities using subflow (MIP excluded, already stitched)
    enface_outputs = stitch_enface_modalities(
        mosaic_ident=mosaic_ident,
        config=config,
        template_path=template_path,
        processed_path=processed_path,
        stitched_path=stitched_path,
        mask_path=mask_path if apply_mask else None,
        scan_resolution_2d=scan_resolution_2d,
    )
    enface_outputs["aip"] = aip_nifti
    enface_outputs["mip"] = mip_nifti
    enface_outputs["mask"] = mask_path

    validate_mosaic_enface_outputs_task(enface_outputs=enface_outputs)

    # Create symlinks to DANDI directory if configured
    if dandiset_path and mosaic_enface_format:
        dandi_slice_path = get_dandi_slice_path(
            str(dandiset_path), mosaic_ident.slice_id
        )
        symlink_targets = symlink_enface_to_dandi(
            enface_outputs=enface_outputs,
            dandi_slice_path=dandi_slice_path,
            mosaic_ident=mosaic_ident,
            ctx=ctx,
            mosaic_enface_format=mosaic_enface_format,
        )
        logger.info(
            f"Created {len(symlink_targets)} symlinks for enface files to DANDI"
        )
    else:
        if dandiset_path is None:
            logger.debug("dandiset_path not configured, skipping enface symlinking")
        if mosaic_enface_format is None:
            logger.debug(
                "mosaic_enface_format not provided, skipping enface symlinking"
            )
        symlink_targets = {}
    emit_mosaic_psoct_event(
        MOSAIC_ENFACE_STITCHED,
        mosaic_ident,
        extra_payload={
            "enface_outputs": enface_outputs,
            "symlink_targets": {k: str(v) for k, v in symlink_targets.items()}
            if symlink_targets
            else {},
        },
    )

    # Update OCT project state for this mosaic
    with OCT_STATE_SERVICE.open_mosaic(mosaic_ident=mosaic_ident) as mosaic_state:
        mosaic_state.set_enface_stitched(True)

    logger.info(f"Mosaic {mosaic_id} enface stitching complete")

    return enface_outputs


@flow
def process_mosaic_event_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Event wrapper for :func:`process_mosaic`.

    Expects ``mosaic_ident`` and optional parameter overrides in ``payload`` (see
    :data:`opticstream.flows.psoct.utils.PROCESS_MOSAIC_FLOW_KWARGS_KEYS`).
    """
    mosaic_ident = mosaic_ident_from_payload(payload)
    cfg = load_scan_config_for_payload(payload)
    return process_mosaic(
        mosaic_ident,
        cfg,
        force_refresh_coords=bool(payload.get("force_refresh_coords", False)),
        force_rerun=force_rerun_from_payload(payload),
    )


def to_deployment(
    *,
    project_name: Optional[str] = None,
    deployment_name: str = "local",
    extra_tags: Sequence[str] = (),
):
    """
    Create both deployments:
    - manual `process_mosaic` (ad-hoc reruns)
    - event-driven `process_mosaic_event_flow` (triggered by MOSAIC_READY)
    """
    manual = process_mosaic.to_deployment(
        name=deployment_name,
        tags=["mosaic-processing", "stitching", *list(extra_tags)],
        concurrency_limit=1,
    )
    event = process_mosaic_event_flow.to_deployment(
        name=deployment_name,
        tags=["event-driven", "mosaic-processing", "stitching", *list(extra_tags)],
        triggers=[get_event_trigger(MOSAIC_READY, project_name=project_name)],
        concurrency_limit=1,
    )
    return [manual, event]
