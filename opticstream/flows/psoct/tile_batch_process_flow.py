from pathlib import Path
from typing import Any, Dict, Literal, Optional, Sequence

from prefect import flow, get_run_logger, task

from opticstream.config.pipeline_opts_builder import build_pipeline_opts
from opticstream.hooks import slack_notification_hook
from opticstream.hooks.check_mosaic_ready_hook import check_mosaic_ready_hook
from opticstream.hooks.publish_hooks import (
    publish_oct_mosaic_hook,
    publish_oct_project_hook,
)
from opticstream.config.psoct_scan_config import PSOCTScanConfigModel, TileSavingType
from opticstream.events import BATCH_PROCESSED, BATCH_READY, get_event_trigger
from opticstream.flows.psoct.tile_batch_archive_flow import archive_tile_batch
from opticstream.flows.psoct.tile_batch_processed_validation import (
    validate_processed_batch_outputs,
)
from opticstream.flows.psoct.tile_file_reference import (
    TileFileReference,
    build_tile_file_reference_list,
)
from opticstream.flows.psoct.utils import (
    MosaicContext,
    batch_ident_from_payload,
    get_processed_tile_path,
    get_slice_paths,
    load_scan_config_for_payload,
    mosaic_context_from_ids,
    path_list_from_payload,
)
from opticstream.state.milestone_wrappers_psoct import oct_batch_processing_milestone
from opticstream.state.oct_project_state import OCT_STATE_SERVICE, OCTBatchId
from opticstream.state.state_guards import (
    enter_flow_stage,
    force_rerun_from_payload,
    should_skip_run,
)
from opticstream.utils.matlab_execution import run_matlab_batch_command_or_cli
from opticstream.utils.matlab_settings import matlab_flow_enabled

from psoct_toolbox.matlab_bridge import (
    build_complex2processed_batch_indexed_command,
    build_spectral2processed_batch_indexed_command,
)


def _determine_processing_mode(
    *,
    tile_saving_type: TileSavingType,
) -> Literal["spectral", "complex"]:
    if tile_saving_type in (TileSavingType.SPECTRAL, TileSavingType.SPECTRAL_12bit):
        return "spectral"
    if tile_saving_type in (
        TileSavingType.COMPLEX, TileSavingType.COMPLEX_WITH_SPECTRAL
    ):
        return "complex"
    if tile_saving_type in (TileSavingType.PROCESSED_WITH_SPECTRAL):
        return "processed"
    raise ValueError(f"Invalid tile saving type: {tile_saving_type}")


@task(task_run_name="spectral-to-processed-{batch_id}")
def spectral_to_processed_tile_batch(
    batch_id: OCTBatchId,
    file_reference_list: list[TileFileReference],
    *,
    config: PSOCTScanConfigModel,
    mosaic_context: MosaicContext,
) -> Path:
    logger = get_run_logger()

    _, processed_path, _, _ = get_slice_paths(str(config.project_base_path), batch_id.slice_id)
    processed_path.mkdir(parents=True, exist_ok=True)

    pipeline_opts = build_pipeline_opts(
        config=config,
        illumination=mosaic_context.config_illumination,
    )
    cmd = build_spectral2processed_batch_indexed_command(
        [str(ref.spectral_file_path) for ref in file_reference_list],
        output_dir=str(processed_path),
        mosaic_id=batch_id.mosaic_id,
        tile_indices=[ref.tile_number for ref in file_reference_list],
        pipeline_opts=pipeline_opts,
        num_workers=config.processing.matlab_num_workers,
        pool_type=config.processing.matlab_pool_type,
    )
    logger.info("Running MATLAB command: %s", cmd)
    run_matlab_batch_command_or_cli(
        cmd,
        matlab_script_path=str(config.matlab_root) if config.matlab_root else None,
    )
    validate_processed_batch_outputs(
        processed_path,
        file_reference_list,
        mosaic_id=batch_id.mosaic_id,
        config=config,
    )
    return processed_path


@task(task_run_name="complex-to-processed-{batch_id}")
def complex_to_processed_tile_batch(
    batch_id: OCTBatchId,
    file_reference_list: list[TileFileReference],
    *,
    config: PSOCTScanConfigModel,
    mosaic_context: MosaicContext,
) -> Path:
    logger = get_run_logger()

    _, processed_path, _, _ = get_slice_paths(str(config.project_base_path), batch_id.slice_id)
    processed_path.mkdir(parents=True, exist_ok=True)

    pipeline_opts = build_pipeline_opts(
        config=config,
        illumination=mosaic_context.config_illumination,
    )
    cmd = build_complex2processed_batch_indexed_command(
        [str(ref.complex_file_path) for ref in file_reference_list],
        output_dir=str(processed_path),
        mosaic_id=batch_id.mosaic_id,
        tile_indices=[ref.tile_number for ref in file_reference_list],
        pipeline_opts=pipeline_opts,
        num_workers=config.processing.matlab_num_workers,
        pool_type=config.processing.matlab_pool_type,
    )
    logger.info("Running MATLAB command: %s", cmd)
    run_matlab_batch_command_or_cli(
        cmd,
        matlab_script_path=str(config.matlab_root),
    )
    validate_processed_batch_outputs(
        processed_path,
        file_reference_list,
        mosaic_id=batch_id.mosaic_id,
        config=config,
    )
    return processed_path



@task
def processed_to_processed_tile_batch(
    batch_id: OCTBatchId,
    file_reference_list: list[TileFileReference],
    *,
    config: PSOCTScanConfigModel,
    mosaic_context: MosaicContext,
) -> Path:
    logger = get_run_logger()
    __, processed_path, _, _ = get_slice_paths(str(config.project_base_path), batch_id.slice_id)
    processed_path.mkdir(parents=True, exist_ok=True)
    for ref in file_reference_list:
        if ref.aip_file_path:
            aip_path = get_processed_tile_path(processed_path, batch_id.mosaic_id, ref.tile_number, "aip", compressed=False)
            if aip_path.exists() or aip_path.is_symlink():
                aip_path.unlink()
            aip_path.symlink_to(ref.aip_file_path)
        if ref.mip_file_path:
            mip_path = get_processed_tile_path(processed_path, batch_id.mosaic_id, ref.tile_number, "mip", compressed=False)
            if mip_path.exists() or mip_path.is_symlink():
                mip_path.unlink()
            mip_path.symlink_to(ref.mip_file_path)
        if ref.ori_file_path:
            ori_path = get_processed_tile_path(processed_path, batch_id.mosaic_id, ref.tile_number, "ori", compressed=False)
            if ori_path.exists() or ori_path.is_symlink():
                ori_path.unlink()
            ori_path.symlink_to(ref.ori_file_path)
        if ref.ret_file_path:
            ret_path = get_processed_tile_path(processed_path, batch_id.mosaic_id, ref.tile_number, "ret", compressed=False)
            if ret_path.exists() or ret_path.is_symlink():
                ret_path.unlink()
            ret_path.symlink_to(ref.ret_file_path)
        if ref.surf_file_path:
            surf_path = get_processed_tile_path(processed_path, batch_id.mosaic_id, ref.tile_number, "surf", compressed=False)
            if surf_path.exists() or surf_path.is_symlink():
                surf_path.unlink()
            surf_path.symlink_to(ref.surf_file_path)
        if ref.dbi_file_path:
            dbi_path = get_processed_tile_path(processed_path, batch_id.mosaic_id, ref.tile_number, "dBI", compressed=False)
            if dbi_path.exists() or dbi_path.is_symlink():
                dbi_path.unlink()
            dbi_path.symlink_to(ref.dbi_file_path)

def split_channel_data(
    input_path: Path,
    output_paths: list[Path]
) -> None:
    logger = get_run_logger()
    data = nib.load(input_path)
    # interleave the second dimension to three channels
    for i in range(3):
        data_slice = data[:, i::3, ...]
        nib.save(data_slice, output_path[i])
    

@flow(
    flow_run_name="process-tile-batch-{batch_id}",
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook, check_mosaic_ready_hook],
    on_failure=[slack_notification_hook],
)
@matlab_flow_enabled
@oct_batch_processing_milestone(
    field_name="enface_processed", success_event=BATCH_PROCESSED
)
def process_tile_batch(
    batch_id: OCTBatchId,
    config: PSOCTScanConfigModel,
    file_list: list[Path],
    *,
    force_rerun: bool = False,
) -> None:
    
    logger = get_run_logger()
    if should_skip_run(
        enter_flow_stage(
            OCT_STATE_SERVICE.peek_batch(batch_ident=batch_id),
            force_rerun=force_rerun,
            skip_if_running=False,
            item_ident=batch_id,
        )
    ):
        return None
    with OCT_STATE_SERVICE.open_batch(batch_ident=batch_id) as batch_state:
        batch_state.mark_started()

    mosaic_context = mosaic_context_from_ids(
        slice_id=batch_id.slice_id,
        mosaic_id=batch_id.mosaic_id,
        mosaics_per_slice=config.mosaics_per_slice,
    )

    file_reference_list = build_tile_file_reference_list(
        file_list,
        config=config,
        mosaic_context=mosaic_context,
    )
    file_reference_list = list(file_reference_list.values())

    logger.info("File reference list: %s", file_reference_list)
    archive_future = None
    if config.archive_path:
        archive_future = archive_tile_batch.submit(
            batch_id=batch_id,
            file_reference_list=file_reference_list,
            acquisition_label=mosaic_context.acquisition_label,
            archive_path=config.archive_path,
            archive_tile_name_format=config.archive_tile_name_format,
            force_rerun=force_rerun,
        )

    mode = _determine_processing_mode(
        tile_saving_type=config.acquisition.tile_saving_type,
    )
    processed_path: Path | None = None
    if mode == "spectral":
        processed_path = spectral_to_processed_tile_batch(
            batch_id=batch_id,
            file_reference_list=file_reference_list,
            config=config,
            mosaic_context=mosaic_context,
        )
    elif mode == "complex":
        processed_path = complex_to_processed_tile_batch(
            batch_id=batch_id,
            file_reference_list=file_reference_list,
            config=config,
            mosaic_context=mosaic_context,
        )
    elif mode == "processed":
        processed_path = processed_to_processed_tile_batch(
            batch_id=batch_id,
            file_reference_list=file_reference_list,
            config=config,
            mosaic_context=mosaic_context,
        )
    

    if archive_future:
        archive_future.wait()
    with OCT_STATE_SERVICE.open_batch(batch_ident=batch_id) as batch_state:
        batch_state.mark_completed()

    logger.info("Processed batch to %s for %s", processed_path, batch_id)


@flow
def process_tile_batch_event_flow(payload: Dict[str, Any]) -> dict[str, Any]:
    batch_ident = batch_ident_from_payload(payload)
    cfg = load_scan_config_for_payload(payload)
    return process_tile_batch(
        batch_id=batch_ident,
        config=cfg,
        file_list=path_list_from_payload(payload),
        force_rerun=force_rerun_from_payload(payload),
    )


def to_deployment(
    *,
    project_name: Optional[str] = None,
    deployment_name: str = "local",
    extra_tags: Sequence[str] = (),
    concurrency_limit: int = 1,
):
    """
    Create both deployments:
    - manual `process_tile_batch` (ad-hoc reruns)
    - event-driven `process_tile_batch_event_flow` (triggered by BATCH_READY)
    """
    manual = process_tile_batch.to_deployment(
        name=deployment_name,
        tags=["tile-batch", "process-tile-batch", *list(extra_tags)],
        concurrency_limit=concurrency_limit,
    )
    event = process_tile_batch_event_flow.to_deployment(
        name=deployment_name,
        tags=["event-driven", "tile-batch", "process-tile-batch", *list(extra_tags)],
        triggers=[get_event_trigger(BATCH_READY, project_name=project_name)],
        concurrency_limit=concurrency_limit,
    )
    return [manual, event]
