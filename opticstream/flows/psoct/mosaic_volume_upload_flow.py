from typing import Any, Dict, Optional, Sequence

from prefect import flow, get_run_logger
from prefect.blocks.system import Secret

from opticstream.hooks.publish_hooks import (
    publish_oct_mosaic_hook,
    publish_oct_project_hook,
)
from opticstream.events import (
    MOSAIC_VOLUME_STITCHED,
    MOSAIC_VOLUME_UPLOADED,
    get_event_trigger,
)
from opticstream.events.psoct_event_emitters import emit_mosaic_psoct_event
from opticstream.flows.psoct.utils import (
    load_scan_config_for_payload,
    mosaic_ident_from_payload,
    non_empty_paths_from_mapping,
)
from opticstream.state.milestone_wrappers_psoct import oct_mosaic_processing_milestone
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.state.state_guards import force_rerun_from_payload
from opticstream.tasks.dandi_upload import upload_to_dandi_batch
from opticstream.hooks.slack_notification_hook import slack_notification_hook
from opticstream.utils.upload_settings import upload_flow_enabled
from opticstream.utils.volume_settings import volume_output_upload_enabled


@flow(
    flow_run_name="upload-mosaic-volume-{mosaic_ident}",
    on_completion=[publish_oct_mosaic_hook, publish_oct_project_hook],
    on_failure=[slack_notification_hook],
)
@volume_output_upload_enabled
@upload_flow_enabled
@oct_mosaic_processing_milestone(
    field_name="volume_uploaded", success_event=MOSAIC_VOLUME_UPLOADED
)
def upload_mosaic_volume_to_dandi_flow(
    mosaic_ident: OCTMosaicId,
    volume_outputs: Dict[str, str],
    *,
    dandi_instance: str = "linc",
    dandi_api_key: Secret | None = None,
    force_rerun: bool = False,
) -> Dict[str, Any]:
    logger = get_run_logger()
    file_list = non_empty_paths_from_mapping(volume_outputs)
    if not file_list:
        logger.warning("No volume files found for %s", mosaic_ident)
        return {"uploaded": 0}
    upload_to_dandi_batch(
        file_list=file_list,
        dandi_instance=dandi_instance,
        realpath=False,
        dandi_api_key=dandi_api_key,
    )
    emit_mosaic_psoct_event(MOSAIC_VOLUME_UPLOADED, mosaic_ident)
    return {"uploaded": len(file_list)}


@flow
def upload_mosaic_volume_to_dandi_event_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    mosaic_ident = mosaic_ident_from_payload(payload)
    cfg = load_scan_config_for_payload(payload)
    return upload_mosaic_volume_to_dandi_flow(
        mosaic_ident=mosaic_ident,
        volume_outputs=payload.get("volume_outputs", {}),
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
    - manual `upload_mosaic_volume_to_dandi_flow` (ad-hoc reruns)
    - event-driven `upload_mosaic_volume_to_dandi_event_flow` (triggered by MOSAIC_VOLUME_STITCHED)
    """
    manual = upload_mosaic_volume_to_dandi_flow.to_deployment(
        name=deployment_name,
        tags=["upload", "dandi", "volume", *list(extra_tags)],
    )
    event = upload_mosaic_volume_to_dandi_event_flow.to_deployment(
        name=deployment_name,
        tags=["event-driven", "upload", "dandi", "volume", *list(extra_tags)],
        triggers=[get_event_trigger(MOSAIC_VOLUME_STITCHED, project_name=project_name)],
    )
    return [manual, event]
