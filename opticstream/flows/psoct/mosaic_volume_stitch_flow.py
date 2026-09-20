"""
Event-driven volume stitching flow.

This flow is triggered by the `MOSAIC_ENFACE_STITCHED` / `linc.oct.mosaic.stitched`
event after enface modalities
are stitched. It handles:
1. Focus finding for first slice (if needed)
2. 3D volume stitching for all volume modalities
3. Emits MOSAIC_VOLUME_STITCHED event
"""

from pathlib import Path
import shutil
from typing import Any, Dict, Optional, Sequence

from prefect import flow, task
from prefect.logging import get_run_logger

from niizarr import ZarrConfig
from opticstream.hooks.publish_hooks import (
    publish_oct_mosaic_hook,
    publish_oct_project_hook,
)
from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.scripts import find_tile_plane
from opticstream.scripts.filter_tiles_by_signal import filter_tiles_by_signal
from opticstream.events import (
    MOSAIC_ENFACE_STITCHED,
    MOSAIC_VOLUME_STITCHED,
    get_event_trigger,
)
from opticstream.events.psoct_event_emitters import emit_mosaic_psoct_event
from opticstream.flows.psoct.mosaic_coordinate_flow import generate_tile_info_file
from opticstream.state.state_guards import (
    enter_milestone_stage,
    force_rerun_from_payload,
    should_skip_run,
)
from opticstream.flows.psoct.utils import (
    get_dandi_slice_path,
    get_mosaic_nifti_path,
    get_mosaic_tile_info_path,
    get_slice_paths,
    load_scan_config_for_payload,
    mosaic_context_from_ident,
    mosaic_ident_from_payload,
    mosaic_prefix,
    normalize_float_sequence,
)
from opticstream.state.oct_project_state import OCT_STATE_SERVICE, OCTMosaicId
from opticstream.hooks.slack_notification_hook import slack_notification_hook
from opticstream.utils.zarr_validation import validate_zarr


@task
def stitch_mosaic_volume_task(
    tile_info_path: Path,
    output_zarr_path: Path,
    zarr_config: Optional[ZarrConfig],
    *,
    focus_plane_path: Optional[Path] = None,
    mask_path: Optional[Path] = None,
    normalize_focus_plane: bool = True,
    crop_focus_plane_depth: int = 500,
    crop_focus_plane_offset: int = 30,
    voxel_size_xyz: Sequence[float] = (0.01, 0.01, 0.0025),
    circular_mean: bool = False,
    zarr_size_threshold: int = 10**9,
    validate_zarr_output: bool = True,
) -> Path:
    """
    Stitch a volume modality mosaic into an OME-Zarr.

    Parameters
    ----------
    tile_info_path : Path
        Path to the tile-info YAML describing the mosaic tiles.
    output_zarr_path : Path
        Output OME-Zarr path.
    zarr_config : Optional[ZarrConfig]
        Zarr configuration object.
    focus_plane_path : Optional[Path]
        Optional focus-plane NIfTI to guide stitching.
    mask_path : Optional[Path]
        Optional mask NIfTI to apply during stitching.
    normalize_focus_plane : bool
        Whether to normalize the focus plane values before applying.
    crop_focus_plane_depth : int
        Depth (pixels) to crop around the focus plane.
    crop_focus_plane_offset : int
        Offset (pixels) applied when cropping around the focus plane.
    voxel_size_xyz : Sequence[float]
        Voxel size in \(x, y, z\).
    circular_mean : bool
        When True, use circular mean for phase-like modalities.
    zarr_size_threshold: int
        Minimum total on-disk size of the zarr tree (bytes).
        Use ``<= 0`` to only require a non-empty tree.
    validate_zarr_output: bool
        When True, run :func:`~opticstream.utils.zarr_validation.validate_zarr` after stitching.
    """
    from linc_convert.modalities.psoct.mosaic import mosaic2d

    logger = get_run_logger()
    logger.info(f"Stitching volume mosaic from {tile_info_path}")

    mosaic2d(
        tile_info_file=str(tile_info_path),
        out=str(output_zarr_path),
        zarr_config=zarr_config,
        focus_plane=focus_plane_path,
        mask=mask_path,
        normalize_focus_plane=normalize_focus_plane,
        crop_focus_plane_depth=crop_focus_plane_depth,
        crop_focus_plane_offset=crop_focus_plane_offset,
        voxel_size_xyz=voxel_size_xyz,
        circular_mean=circular_mean,
    )
    logger.info(f"Stitched volume mosaic saved to {output_zarr_path}")

    if validate_zarr_output:
        ctx = f"mosaic_volume {output_zarr_path.name} (from {tile_info_path.name})"
        vr = validate_zarr(
            output_zarr_path,
            zarr_size_threshold,
            context=ctx,
            logger=logger,
        )
        if not vr.ok:
            raise RuntimeError(
                f"Mosaic zarr validation failed for {output_zarr_path}: {vr.reason} "
                f"(size_bytes={vr.size_bytes})"
            )
        logger.info(
            f"Mosaic zarr validation passed for {output_zarr_path} ({vr.size_bytes} bytes)"
        )

    return output_zarr_path


@task(task_run_name="mosaic-{mosaic_ident}-find-focus-plane-{config_illumination}")
def find_focus_plane_task(
    project_base_path: Path,
    mosaic_ident: OCTMosaicId,
    config_illumination: str,
    signal_threshold: float = 60,
) -> Path:
    """
    Find optimal focus plane for 3D volume stitching (Section 3.3).

    Per design document Section 3.3, focus finding:
    - Determines optimal focus plane for 3D volume stitching
    - Uses unfiltered surface data
    - Generates QC validation: verify focus finding overlap with intensity images
    - Output saved as focus-{config_illumination}.nii in project base path

    Parameters
    ----------
    project_base_path : Path
        Base path for the project.
    mosaic_ident : OCTMosaicId
        Mosaic identifier (includes slice and mosaic id).
    config_illumination : str
        Config illumination bucket ("normal" or "tilted"); used for output filenames

    Returns
    -------
    Path
        Path to generated focus plane file (focus-{config_illumination}.nii)

    Notes
    -----
    Detailed algorithms are described in Section 15 of design document.
    This is a placeholder implementation that can be expanded with actual
    focus finding algorithms.
    """
    logger = get_run_logger()
    logger.info(
        f"Finding focus plane for {config_illumination} illumination (mosaic {mosaic_ident.mosaic_id})"
    )
    slice_path, _, stitched_path, _ = get_slice_paths(
        project_base_path, mosaic_ident.slice_id
    )
    slice_focus_dir = slice_path / "focus_finding"
    slice_focus_dir.mkdir(parents=True, exist_ok=True)

    surface_tile_info_path = get_mosaic_tile_info_path(
        stitched_path, mosaic_ident.mosaic_id, "surf"
    )
    if not surface_tile_info_path.exists():
        raise FileNotFoundError(
            f"surface info file not found: {surface_tile_info_path}"
        )
    filtered_tile_info_path = slice_focus_dir / f"filtered_{config_illumination}.yaml"
    focus_plane_path_slice = slice_focus_dir / f"focus-{config_illumination}.nii"
    focus_plane_path_base = Path(project_base_path) / f"focus-{config_illumination}.nii"
    # find_tile_region(
    #     input_yaml=str(input_yaml),
    #     output_yaml=str(output_yaml),
    #     signal_threshold=signal_threshold,
    #     grid_size=grid_size,
    # )
    filter_tiles_by_signal(
        input_yaml=str(surface_tile_info_path),
        output_yaml=str(filtered_tile_info_path),
        signal_threshold=signal_threshold,
    )
    # find_volume_surface.batch(
    #     yaml_path=str(output_yaml),
    #     output_dir=str(project_base_path / "focus_finding"),
    #     output_yaml=str(
    #         project_base_path / "focus_finding" / f"surface_{config_illumination}.yaml"
    #     ),
    #     postfix_old="_dBI",
    #     postfix_new="_surf",
    # )
    # surface_yaml = slice_focus_dir / f"surface_{config_illumination}.yaml"
    find_tile_plane.main(
        yaml_path=str(filtered_tile_info_path),
        output=str(focus_plane_path_slice),
        base_dir=str(slice_focus_dir),
        subsample=1,
        avg_signal_threshold=signal_threshold,
        plot=str(slice_focus_dir / f"plane_{config_illumination}.png"),
        outlier_method="iqr",
        outlier_iqr_factor=1.5,
        outlier_z_threshold=3.0,
        output_corrected_dir=None,
        crop_x=15,
        normalize_min=0,
        degree=2,
    )

    if not focus_plane_path_slice.exists():
        raise FileNotFoundError(f"Focus plane not found: {focus_plane_path_slice}")
    shutil.copy2(focus_plane_path_slice, focus_plane_path_base)
    logger.info(f"Copied focus plane to project base: {focus_plane_path_base}")
    return focus_plane_path_base


@flow(
    flow_run_name="stitch-volume-{mosaic_ident}",
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook],
    on_failure=[slack_notification_hook],
)
def stitch_volume_flow(
    mosaic_ident: OCTMosaicId,
    config: PSOCTScanConfigModel,
    *,
    apply_mask: bool = False,
    apply_focus_plane: bool = True,
    force_refresh_focus: bool = False,
    force_rerun: bool = False,
) -> Dict[str, Path]:
    """
    Flow to stitch 3D volume modalities, triggered by the `MOSAIC_ENFACE_STITCHED` event.

    This flow handles:
    1. Focus finding for first slice (if needed)
    2. 3D volume stitching for all volume modalities
    3. Emits MOSAIC_VOLUME_STITCHED event

    Parameters
    ----------
    mosaic_ident : OCTMosaicId
        Mosaic identifier
    config : PSOCTScanConfigModel
        Configuration object
    apply_mask : bool, optional
        Apply mask to the volume
    apply_focus_plane : bool, optional
        When True, require a focus plane and pass it to the stitcher.
    force_refresh_focus : bool, optional
        Force refresh focus
    force_rerun : bool, optional
        Force rerun

    Returns
    -------
    Dict[str, Path]
        Dictionary mapping modality to output file paths
    """
    logger = get_run_logger()
    if not config.processing.save_volume_outputs:
        logger.info("Volume output disabled for this project; skipping volume stitching")
        with OCT_STATE_SERVICE.open_mosaic(mosaic_ident=mosaic_ident) as mosaic_state:
            mosaic_state.mark_completed()
        return {}
    if should_skip_run(
        enter_milestone_stage(
            item_state_view=OCT_STATE_SERVICE.peek_mosaic(mosaic_ident=mosaic_ident),
            item_ident=mosaic_ident,
            field_name="volume_stitched",
            force_rerun=force_rerun,
        )
    ):
        with OCT_STATE_SERVICE.open_mosaic(mosaic_ident=mosaic_ident) as mosaic_state:
            mosaic_state.mark_completed()
        return {}
    if not config.stitch_3d_volumes:
        logger.info(
            f"Skipping volume stitching for mosaic {mosaic_ident} as stitch_3d_volumes is disabled"
        )
        with OCT_STATE_SERVICE.open_mosaic(mosaic_ident=mosaic_ident) as mosaic_state:
            mosaic_state.mark_completed()
        return {}
    ctx = mosaic_context_from_ident(mosaic_ident, config)
    mosaic_id = mosaic_ident.mosaic_id
    project_base_path = config.project_base_path
    dandiset_path = str(config.dandiset_path) if config.dandiset_path else None
    mosaic_volume_format = config.mosaic_volume_format
    scan_resolution_3d = normalize_float_sequence(config.acquisition.scan_resolution_3d)

    _, processed_path, stitched_path, _ = get_slice_paths(
        project_base_path, mosaic_ident.slice_id
    )
    template_path = project_base_path / ctx.template_name

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template not found: {template_path}. "
            f"Coordinate determination must be run for mosaic {ctx.base_mosaic_id} first."
        )

    focus_plane_path = None
    if apply_focus_plane:
        focus_plane_path = project_base_path / f"focus-{ctx.config_illumination}.nii"

        if ctx.is_first_slice or force_refresh_focus:
            logger.info(
                f"Finding focus plane for first slice (mosaic {mosaic_id}, "
                f"{ctx.config_illumination} illumination)"
            )
            find_focus_plane_task(
                project_base_path=project_base_path,
                mosaic_ident=mosaic_ident,
                config_illumination=ctx.config_illumination,
            )
            logger.info(
                f"Focus plane determined for {ctx.config_illumination} illumination: {focus_plane_path}"
            )
        if not focus_plane_path.exists():
            raise FileNotFoundError(
                f"Focus plane not found for {ctx.config_illumination} illumination: {focus_plane_path}"
            )

    # Step 2: Stitch volume modalities
    logger.info(f"Stitching volume modalities for mosaic {mosaic_id}")
    volume_futures = {}
    volume_outputs = {}
    slice_id = mosaic_ident.slice_id
    acquisition_label = ctx.acquisition_label

    if dandiset_path:
        output_dir = get_dandi_slice_path(dandiset_path, slice_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Writing volumes to DANDI directory: {output_dir}")
    else:
        output_dir = processed_path
        if dandiset_path is None:
            logger.warning(
                f"dandiset_path not configured, writing volumes to processed directory: {processed_path}"
            )

    for modality in (m.value for m in config.volume_modalities):
        modality_tile_info_path = get_mosaic_tile_info_path(
            stitched_path, mosaic_id, modality
        )

        generate_tile_info_file(
            template_path=template_path,
            output_path=modality_tile_info_path,
            base_dir=processed_path,
            modality=modality,
            mosaic_id=mosaic_id,
            scan_resolution=scan_resolution_3d,
        )

        # Generate filename using template if provided, otherwise use default format
        if mosaic_volume_format:
            filename = mosaic_volume_format.format(
                project_name=mosaic_ident.project_name,
                slice_id=mosaic_ident.slice_id,
                acq=acquisition_label,
                modality=modality,
            )
        else:
            # Fallback to default format
            filename = f"{mosaic_prefix(mosaic_id)}_{modality}.ome.zarr"

        output_zarr_path = output_dir / filename
        future = stitch_mosaic_volume_task.submit(
            modality_tile_info_path,
            output_zarr_path,
            zarr_config=config.zarr_config,
            focus_plane_path=focus_plane_path,
            mask_path=get_mosaic_nifti_path(stitched_path, mosaic_id, "mask")
            if apply_mask
            else None,
            normalize_focus_plane=True,
            crop_focus_plane_depth=config.crop_focus_plane_depth,
            crop_focus_plane_offset=config.crop_focus_plane_offset,
            voxel_size_xyz=scan_resolution_3d,
            circular_mean=(modality == "O3D"),
        )
        volume_futures[modality] = future
        volume_outputs[modality] = output_zarr_path

    # Wait for all volume stitching to complete
    errors = []
    for modality, future in volume_futures.items():
        future.wait()
        if future.exception():
            errors.append(future.exception())

    if errors:
        raise RuntimeError(f"Errors occurred during volume stitching: {errors}")

    logger.info(f"All volume modalities stitched for mosaic {mosaic_id}")

    # Emit MOSAIC_VOLUME_STITCHED event
    emit_mosaic_psoct_event(
        MOSAIC_VOLUME_STITCHED,
        mosaic_ident,
        extra_payload={
            "volume_outputs": {mod: str(path) for mod, path in volume_outputs.items()},
        },
    )

    # Update OCT project state for this mosaic
    with OCT_STATE_SERVICE.open_mosaic(mosaic_ident=mosaic_ident) as mosaic_state:
        mosaic_state.set_volume_stitched(True)
        mosaic_state.mark_completed()
    return volume_outputs


@flow
def stitch_volume_event_flow(payload: Dict[str, Any]) -> Dict[str, Path]:
    """Event wrapper for :func:`stitch_volume_flow` (expects ``mosaic_ident`` in payload)."""
    mosaic_ident = mosaic_ident_from_payload(payload)
    cfg = load_scan_config_for_payload(payload)
    return stitch_volume_flow(
        mosaic_ident,
        cfg,
        apply_mask=bool(payload.get("apply_mask", False)),
        force_refresh_focus=bool(payload.get("force_refresh_focus", False)),
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
    - manual `stitch_volume_flow` (ad-hoc reruns)
    - event-driven `stitch_volume_event_flow` (triggered by MOSAIC_ENFACE_STITCHED)
    """
    manual = stitch_volume_flow.to_deployment(
        name=deployment_name,
        tags=["mosaic-processing", "volume-stitching", *list(extra_tags)],
        concurrency_limit=1,
    )
    event = stitch_volume_event_flow.to_deployment(
        name=deployment_name,
        tags=[
            "event-driven",
            "mosaic-processing",
            "volume-stitching",
            *list(extra_tags),
        ],
        triggers=[get_event_trigger(MOSAIC_ENFACE_STITCHED, project_name=project_name)],
        concurrency_limit=1,
    )
    return [manual, event]
