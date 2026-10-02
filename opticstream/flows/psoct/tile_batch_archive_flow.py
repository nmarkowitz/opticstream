import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from prefect import flow, get_run_logger, task

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.hooks.publish_hooks import (
    publish_oct_mosaic_hook,
    publish_oct_project_hook,
)
from opticstream.events import BATCH_READY, get_event_trigger
from opticstream.events.psoct_event_emitters import emit_batch_psoct_event
from opticstream.events.psoct_events import BATCH_ARCHIVED, BATCH_COMPLEX_ARCHIVED
from opticstream.flows.psoct.tile_file_reference import (
    TileFileReference,
    build_tile_file_reference_list,
)
from opticstream.flows.psoct.utils import (
    batch_ident_from_payload,
    load_scan_config_for_payload,
    mosaic_context_from_ids,
    path_list_from_payload,
    processed_output_prefix,
)
from opticstream.state.milestone_wrappers_psoct import oct_batch_processing_milestone
from opticstream.state.oct_project_state import OCT_STATE_SERVICE, OCTBatchId
from opticstream.state.state_guards import force_rerun_from_payload
from opticstream.tasks.archive_file import archive_file
from opticstream.hooks.slack_notification_hook import slack_notification_hook


@task(
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook],
    on_failure=[slack_notification_hook],
)
@oct_batch_processing_milestone(field_name="archived")
def archive_tile_batch(
    batch_id: OCTBatchId,
    file_reference_list: list[TileFileReference],
    *,
    acquisition_label: str,
    archive_path: Path,
    archive_tile_name_format: str,
    force_rerun: bool = False,
) -> list[str]:
    """Archive the batch's raw tiles, emit BATCH_ARCHIVED; returns the archived paths."""
    logger = get_run_logger()
    archive_path.mkdir(parents=True, exist_ok=True)
    with OCT_STATE_SERVICE.open_batch(batch_ident=batch_id) as batch:
        batch.reset_archived()

    archived_file_paths: list[str] = []
    futures = []
    for ref in file_reference_list:
        if ref.spectral_file_path:
            input_path = ref.spectral_file_path
        elif ref.complex_file_path:
            input_path = ref.complex_file_path
        else:
            raise ValueError(f"No input path found for tile {ref.tile_number}, {ref}")
        output_name = archive_tile_name_format.format(
            project_name=batch_id.project_name,
            slice_id=batch_id.slice_id,
            tile_id=ref.tile_number,
            acq=acquisition_label,
        )
        output_path = archive_path / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        futures.append(archive_file.submit(input_path, output_path))
        archived_file_paths.append(str(output_path))

    for future in futures:
        future.wait()

    logger.info("Archived %d files for %s", len(archived_file_paths), batch_id)
    files_with_issue = check_archive_result(batch_id, archived_file_paths)
    if files_with_issue:
        raise RuntimeError(
            "Archive validation failed for "
            f"{len(files_with_issue)} file(s): " + " | ".join(files_with_issue)
        )

    emit_batch_psoct_event(
        BATCH_ARCHIVED,
        batch_id,
        extra_payload={"file_list": archived_file_paths},
    )
    return archived_file_paths


@task(on_failure=[slack_notification_hook])
def archive_complex_tiles(
    batch_id: OCTBatchId,
    file_reference_list: list[TileFileReference],
    *,
    acquisition_label: str,
    archive_path: Path,
    complex_tile_name_format: str,
    complex_dir: Path,
) -> list[str]:
    """gzip each tile's saved complex volume into the archive; returns the archived paths.

    Each uncompressed ``<prefix>_complex.nii`` is deleted once the whole batch's
    gzipped archive copies are validated; the archive copies are never deleted.
    The caller emits BATCH_COMPLEX_ARCHIVED (see ``emit_batch_complex_archived``).
    """
    logger = get_run_logger()
    with OCT_STATE_SERVICE.open_batch(batch_ident=batch_id) as batch:
        batch.reset_complex_uploaded()
    local_paths = []
    futures = []
    for ref in file_reference_list:
        local = complex_dir / f"{processed_output_prefix(batch_id.mosaic_id, ref.tile_number)}_complex.nii"
        if not local.is_file():
            raise FileNotFoundError(f"Complex output missing for tile {ref.tile_number}: {local}")
        output_name = complex_tile_name_format.format(
            project_name=batch_id.project_name,
            slice_id=batch_id.slice_id,
            tile_id=ref.tile_number,
            acq=acquisition_label,
        )
        local_paths.append(local)
        futures.append(archive_file.submit(local, archive_path / output_name))
    archived = [str(future.result()) for future in futures]
    files_with_issue = check_archive_result(batch_id, archived)
    if files_with_issue:
        raise RuntimeError(
            "Complex archive validation failed for "
            f"{len(files_with_issue)} file(s): " + " | ".join(files_with_issue)
        )
    for local in local_paths:
        local.unlink()
    logger.info("Archived %d complex tiles for %s", len(archived), batch_id)
    return archived


def emit_batch_complex_archived(batch_id: OCTBatchId, file_list: list[str]) -> None:
    emit_batch_psoct_event(
        BATCH_COMPLEX_ARCHIVED, batch_id, extra_payload={"file_list": file_list}
    )


def check_archive_result(
    batch_id: OCTBatchId,
    archived_file_paths: list[str],
    min_file_size_bytes: int = 1 * 1024 * 1024, # 1MB file size
) -> list[str]:
    logger = get_run_logger()
    logger.info("Checking if the archived files are valid for %s", batch_id)
    files_with_issue: list[str] = []
    for archived_file_path in archived_file_paths:
        if not os.path.exists(archived_file_path):
            logger.error("Archived file %s does not exist", archived_file_path)
            files_with_issue.append(f"{archived_file_path} (missing)")
            continue

        file_size = os.path.getsize(archived_file_path)
        if file_size <= min_file_size_bytes:
            logger.error(
                "Archived file %s is too small: %d bytes (threshold: %d bytes)",
                archived_file_path,
                file_size,
                min_file_size_bytes,
            )
            files_with_issue.append(
                f"{archived_file_path} (size={file_size}B, min={min_file_size_bytes}B)"
            )
    return files_with_issue


@flow(
    flow_run_name="archive-tile-batch-{batch_id}",
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook],
    on_failure=[slack_notification_hook],
)
def archive_tile_batch_flow(
    batch_id: OCTBatchId,
    config: PSOCTScanConfigModel,
    file_list: list[Path],
    *,
    force_rerun: bool = False,
) -> None:
    """Archive a batch's raw tiles, independent of (and concurrent with) MATLAB processing.

    Runs regardless of ``matlab_processing_enabled``; a no-op when ``archive_path``
    is unset. BATCH_ARCHIVED then triggers the DANDI upload.
    """
    if not config.archive_path:
        get_run_logger().info("archive_path not configured; skipping archive of %s", batch_id)
        return
    mosaic_context = mosaic_context_from_ids(
        slice_id=batch_id.slice_id,
        mosaic_id=batch_id.mosaic_id,
        mosaics_per_slice=config.mosaics_per_slice,
    )
    file_reference_list = build_tile_file_reference_list(
        file_list,
        config=config,
        mosaic_context=mosaic_context,
        batch_id=batch_id.batch_id,
    )
    archive_tile_batch(
        batch_id=batch_id,
        file_reference_list=list(file_reference_list.values()),
        acquisition_label=mosaic_context.acquisition_label,
        archive_path=config.archive_path,
        archive_tile_name_format=config.archive_tile_name_format,
        force_rerun=force_rerun,
    )


@flow
def archive_tile_batch_event_flow(payload: Dict[str, Any]) -> None:
    """Event-driven wrapper for ``archive_tile_batch_flow``, triggered by BATCH_READY."""
    archive_tile_batch_flow(
        batch_id=batch_ident_from_payload(payload),
        config=load_scan_config_for_payload(payload),
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
    - manual `archive_tile_batch_flow` (ad-hoc reruns)
    - event-driven `archive_tile_batch_event_flow` (triggered by BATCH_READY,
      alongside but independent of `process_tile_batch_event_flow`)
    """
    manual = archive_tile_batch_flow.to_deployment(
        name=deployment_name,
        tags=["tile-batch", "archive-tile-batch", *list(extra_tags)],
        concurrency_limit=concurrency_limit,
    )
    event = archive_tile_batch_event_flow.to_deployment(
        name=deployment_name,
        tags=["event-driven", "tile-batch", "archive-tile-batch", *list(extra_tags)],
        triggers=[get_event_trigger(BATCH_READY, project_name=project_name)],
        concurrency_limit=concurrency_limit,
    )
    return [manual, event]
