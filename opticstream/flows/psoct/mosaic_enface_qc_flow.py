"""
Event-driven enface QC Slack upload flow.

Triggered by MOSAIC_ENFACE_STITCHED event. Converts stitched enface NIfTI outputs
to JPEG previews and uploads them to Slack in a single message.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from prefect import flow, task
from prefect.logging import get_run_logger

from opticstream.config.psoct_scan_config import get_psoct_scan_config
from opticstream.data_processing.qc.convert_image import convert_image
from opticstream.events import MOSAIC_ENFACE_STITCHED
from opticstream.events.utils import get_event_trigger
from opticstream.flows.psoct.utils import mosaic_ident_from_payload
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.tasks.slack_notification import (
    send_slack_message,
    upload_multiple_files_to_slack,
)
from opticstream.hooks.slack_notification_hook import slack_notification_hook


@task(task_run_name="convert-enface-nifti-to-jpeg-{mosaic_ident}-{modality}")
def convert_enface_nifti_to_jpeg_task(
    *,
    mosaic_ident: OCTMosaicId,
    modality: str,
    nifti_path: Path,
) -> Optional[Path]:
    logger = get_run_logger()

    if modality == "mask":
        return None

    if nifti_path is None:
        logger.warning(f"Skipping QC convert for modality={modality}: empty path")
        return None

    input_path = Path(nifti_path)
    if not input_path.exists():
        logger.warning(
            f"Skipping QC convert for modality={modality}: file not found: {input_path}"
        )
        return None

    input_str = str(input_path)
    if input_str.endswith(".nii.gz"):
        output_str = input_str[: -len(".nii.gz")] + ".jpg"
    elif input_str.endswith(".nii"):
        output_str = input_str[: -len(".nii")] + ".jpg"
    else:
        output_str = input_str + ".jpg"

    output_path = Path(output_str)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    angle_to_rgb = modality == "ori"

    logger.info(
        f"Converting QC image for mosaic={mosaic_ident.mosaic_id} modality={modality}: "
        f"{input_path.name} -> {output_path.name}"
    )
    convert_image(
        input=input_path,
        output=output_path,
        angle_to_rgb=angle_to_rgb,
        output_format="jpg",
    )
    return output_path


@flow(
    flow_run_name="mosaic-enface-qc-slack-upload-{mosaic_ident}",
    on_failure=[slack_notification_hook],
)
def mosaic_enface_qc_slack_upload_flow(
    *,
    mosaic_ident: OCTMosaicId,
    enface_outputs: Dict[str, Path],
    upload_to_slack: Optional[bool] = None,
) -> Dict[str, bool]:
    """
    Convert stitched enface NIfTIs to JPEG previews and optionally upload to Slack.

    ``upload_to_slack`` defaults to the block's ``stitched_enface_slack_upload``;
    pass True for a manual rerun that should post regardless.
    Best-effort: any Slack failure is logged and returns False results.
    """
    logger = get_run_logger()

    mosaic_id = mosaic_ident.mosaic_id
    expected_modalities = [m for m in enface_outputs.keys() if m != "mask"]

    jpeg_futures: Dict[str, Any] = {}
    for modality, nifti_path in enface_outputs.items():
        if modality == "mask":
            continue
        jpeg_futures[modality] = convert_enface_nifti_to_jpeg_task.submit(
            mosaic_ident=mosaic_ident,
            modality=modality,
            nifti_path=nifti_path,
        )

    modality_to_jpeg: Dict[str, Path] = {}
    for modality, fut in jpeg_futures.items():
        try:
            jpeg_path = fut.result()
        except Exception as e:
            logger.error(f"QC conversion failed for modality={modality}: {e}")
            continue
        if jpeg_path is not None and Path(jpeg_path).exists():
            modality_to_jpeg[modality] = Path(jpeg_path)
        else:
            logger.warning(f"QC JPEG missing for modality={modality}: {jpeg_path}")

    if not modality_to_jpeg:
        logger.warning(f"No QC JPEGs to upload for mosaic {mosaic_id}")
        return {m: False for m in expected_modalities}

    if upload_to_slack is None:
        upload_to_slack = get_psoct_scan_config(
            mosaic_ident.project_name
        ).stitched_enface_slack_upload
    if not upload_to_slack:
        logger.info(
            f"Wrote {len(modality_to_jpeg)} QC JPEGs for mosaic {mosaic_id}; Slack upload "
            "off (stitched_enface_slack_upload=false)"
        )
        return {m: False for m in expected_modalities}

    filepaths = [str(p) for p in modality_to_jpeg.values()]
    titles = [f"Mosaic {mosaic_id} - {m.upper()}" for m in modality_to_jpeg.keys()]
    initial_comment = f"Enface QC previews for mosaic {mosaic_id}"

    results: Dict[str, bool] = {m: False for m in expected_modalities}
    try:
        send_slack_message(
            message=f"✅ Enface stitching complete for mosaic {mosaic_id}. Uploading QC previews…"
        )
        file_results = upload_multiple_files_to_slack(
            filepaths=filepaths,
            titles=titles,
            initial_comment=initial_comment,
        )
        for modality, jpeg_path in modality_to_jpeg.items():
            results[modality] = bool(file_results.get(str(jpeg_path), False))
    except Exception as e:
        logger.error(f"QC Slack upload failed for mosaic {mosaic_id}: {e}")

    return results


# TODO: surface overlay QC
# TODO: focus plane overlay QC
# TODO: tile alignment QC


@flow
def mosaic_enface_qc_slack_event_flow(payload: Dict[str, Any]) -> Dict[str, bool]:
    """
    Event wrapper triggered by MOSAIC_ENFACE_STITCHED.

    Expects payload to contain:
    - mosaic_ident
    - enface_outputs (modality -> path-like)
    """
    logger = get_run_logger()
    mosaic_ident = mosaic_ident_from_payload(payload)

    enface_outputs = payload.get("enface_outputs", None)
    if enface_outputs is None:
        logger.warning(f"No enface_outputs found in event payload for {mosaic_ident}")

    return mosaic_enface_qc_slack_upload_flow(
        mosaic_ident=mosaic_ident,
        enface_outputs=enface_outputs,
    )


def to_deployment(
    *,
    project_name: str | None = None,
    deployment_name: str = "local",
    extra_tags: Sequence[str] = (),
):
    """
    Create both deployments:
    - manual `mosaic_enface_qc_slack_upload_flow` (ad-hoc reruns)
    - event-driven `mosaic_enface_qc_slack_event_flow` (triggered by MOSAIC_ENFACE_STITCHED)
    """
    manual = mosaic_enface_qc_slack_upload_flow.to_deployment(
        name=deployment_name,
        tags=["mosaic-qc", "slack", "enface", *list(extra_tags)],
    )
    event = mosaic_enface_qc_slack_event_flow.to_deployment(
        name=deployment_name,
        tags=["event-driven", "mosaic-qc", "slack", "enface", *list(extra_tags)],
        triggers=[get_event_trigger(MOSAIC_ENFACE_STITCHED, project_name=project_name)],
    )
    return [manual, event]
