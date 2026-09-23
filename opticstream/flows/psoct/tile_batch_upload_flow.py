from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from prefect import flow
from prefect.blocks.system import Secret
from prefect.logging import get_run_logger

from opticstream.hooks.publish_hooks import (
    publish_oct_mosaic_hook,
    publish_oct_project_hook,
)
from opticstream.events import BATCH_ARCHIVED, BATCH_UPLOADED, get_event_trigger
from opticstream.flows.psoct.utils import (
    batch_ident_from_payload,
    load_scan_config_for_payload,
    path_list_from_payload,
)
from opticstream.state.milestone_wrappers_psoct import oct_batch_processing_milestone
from opticstream.state.oct_project_state import OCTBatchId
from opticstream.state.state_guards import force_rerun_from_payload
from opticstream.tasks.dandi_upload import upload_to_dandi_batch
from opticstream.hooks.slack_notification_hook import slack_notification_hook
from opticstream.utils.upload_settings import upload_flow_enabled


@flow(
    flow_run_name="upload-to-dandi-batch-{batch_id}",
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook],
    on_failure=[slack_notification_hook],
)
@upload_flow_enabled
@oct_batch_processing_milestone(field_name="uploaded", success_event=BATCH_UPLOADED)
def upload_to_dandi_tile_batch(
    batch_id: OCTBatchId,
    file_list: list[Path],
    *,
    dandi_instance: str = "dandi",
    realpath: bool = True,
    dandi_api_key: Secret | None = None,
    force_rerun: bool = False,
) -> None:
    logger = get_run_logger()
    if not file_list:
        logger.warning("No files provided for %s", batch_id)
        return
    logger.info("Uploading %d files for %s", len(file_list), batch_id)
    upload_to_dandi_batch(
        file_list=[str(path) for path in file_list],
        dandi_instance=dandi_instance,
        realpath=realpath,
        dandi_api_key=dandi_api_key,
    )


@flow
def upload_to_dandi_batch_event_flow(payload: Dict[str, Any]) -> None:
    batch_ident = batch_ident_from_payload(payload)
    cfg = load_scan_config_for_payload(payload)
    return upload_to_dandi_tile_batch(
        batch_id=batch_ident,
        file_list=path_list_from_payload(payload),
        dandi_api_key=cfg.dandi_api_key,
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
    - manual `upload_to_dandi_tile_batch` (ad-hoc reruns)
    - event-driven DANDI upload (triggered by BATCH_ARCHIVED)
    """
    manual = upload_to_dandi_tile_batch.to_deployment(
        name=deployment_name,
        tags=["tile-batch", "upload-to-dandi", *list(extra_tags)],
    )
    event = upload_to_dandi_batch_event_flow.to_deployment(
        name=deployment_name,
        tags=["event-driven", "tile-batch", "upload-to-dandi", *list(extra_tags)],
        triggers=[get_event_trigger(BATCH_ARCHIVED, project_name=project_name)],
    )
    return [manual, event]
