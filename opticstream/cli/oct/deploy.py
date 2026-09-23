from pathlib import Path


from opticstream.cli.oct import oct_cli
from opticstream.events import (
    BATCH_ARCHIVED,
    BATCH_READY,
    MOSAIC_READY,
    MOSAIC_ENFACE_STITCHED,
    MOSAIC_VOLUME_STITCHED,
    SLICE_READY,
    get_event_trigger,
)

COMMON_TAGS = ["linc", "psoct"]


@oct_cli.command
def deploy(
    *,
    project_name: str = "all",
    deployment_name: str = "dynamic",
    work_pool_name: str = "psoct",
):
    if project_name == "all":
        project_name = None

    register_slice_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="slice_registration_flow.py:register_slice_event_flow",
    ).deploy(
        name="dynamic",
        work_pool_name=work_pool_name,
        work_queue_name="low-io",
        tags=["event-driven", "slice-registration", *COMMON_TAGS],
        triggers=[get_event_trigger(SLICE_READY, project_name=project_name)],
        build=False,
        push=False,
    )

    register_slice_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="slice_registration_flow.py:register_slice_flow",
    ).deploy(
        name="dynamic",
        work_pool_name=work_pool_name,
        work_queue_name="low-io",
        tags=["slice-registration", *COMMON_TAGS],
    )

    # ============================================================================
    # Tile Batch Flow Deployments
    # ============================================================================

    process_tile_batch_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="process_tile_batch_flow.py:process_tile_batch_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["tile-batch", "process-tile-batch", *COMMON_TAGS],
        build=False,
        push=False,
    )

    process_tile_batch_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="process_tile_batch_flow.py:process_tile_batch_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "tile-batch", "process-tile-batch", *COMMON_TAGS],
        triggers=[get_event_trigger(BATCH_READY, project_name=project_name)],
        build=False,
        push=False,
    )

    complex_to_processed_batch_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint=(
            "process_tile_batch_complex2processed_flow.py:"
            "complex_to_processed_batch_flow"
        ),
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["tile-batch", "complex-to-processed", *COMMON_TAGS],
        build=False,
        push=False,
    )

    # ============================================================================
    # Upload Flow Deployments
    # ============================================================================

    upload_mosaic_enface_to_dandi_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="psoct/mosaic_upload_flow.py:upload_mosaic_enface_to_dandi_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "upload", "dandi", "enface", *COMMON_TAGS],
        triggers=[get_event_trigger(MOSAIC_ENFACE_STITCHED, project_name=project_name)],
        build=False,
        push=False,
    )

    upload_mosaic_volume_to_dandi_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="psoct/mosaic_volume_upload_flow.py:upload_mosaic_volume_to_dandi_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "upload", "dandi", "volume", *COMMON_TAGS],
        triggers=[get_event_trigger(MOSAIC_VOLUME_STITCHED, project_name=project_name)],
        build=False,
        push=False,
    )

    # ============================================================================
    # Mosaic Processing Flow Deployments
    # ============================================================================

    process_mosaic_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="psoct/mosaic_process_flow.py:process_mosaic_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "mosaic-processing", "stitching", *COMMON_TAGS],
        triggers=[get_event_trigger(MOSAIC_READY, project_name=project_name)],
        build=False,
        push=False,
    )

    stitch_volume_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="psoct/mosaic_volume_stitch_flow.py:stitch_volume_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "mosaic-processing", "volume-stitching", *COMMON_TAGS],
        triggers=[get_event_trigger(MOSAIC_ENFACE_STITCHED, project_name=project_name)],
        build=False,
        push=False,
    )

    # ============================================================================
    # Slice Registration Flow Deployments
    # ============================================================================
    # These go to the low-io queue instead of the pool default queue.

    register_slice_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="slice_registration_flow.py:register_slice_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        work_queue_name="low-io",
        tags=["event-driven", "slice-registration", *COMMON_TAGS],
        triggers=[get_event_trigger(SLICE_READY, project_name=project_name)],
        build=False,
        push=False,
    )

    register_slice_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="slice_registration_flow.py:register_slice_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        work_queue_name="low-io",
        tags=["slice-registration", *COMMON_TAGS],
        build=False,
        push=False,
    )

    # ============================================================================
    # State Management Flow Deployments
    # ============================================================================

    unified_state_management_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="state_management_flow.py:unified_state_management_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "state-management", "unified", *COMMON_TAGS],
        triggers=[
            get_event_trigger(
                MOSAIC_ENFACE_STITCHED,
                parameters={
                    "event": {
                        "__prefect_kind": "json",
                        "value": {
                            "__prefect_kind": "jinja",
                            "template": "{{ event.event }}",
                        },
                    },
                },
                project_name=project_name,
            ),
        ],
        concurrency_limit=1,
        build=False,
        push=False,
    )

    # ============================================================================
    # Slack Notification Flow Deployments
    # ============================================================================

    slack_enface_notification_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="slack_notification_flow.py:slack_enface_notification_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=["event-driven", "slack-notifications", "enface-stitched", *COMMON_TAGS],
        triggers=[get_event_trigger(MOSAIC_ENFACE_STITCHED, project_name=project_name)],
        build=False,
        push=False,
    )

    mosaic_enface_qc_slack_event_flow.from_source(
        source=Path(__file__).parent.parent.parent / "flows",
        entrypoint="psoct/mosaic_enface_qc_flow.py:mosaic_enface_qc_slack_event_flow",
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        tags=[
            "event-driven",
            "slack-notifications",
            "mosaic-qc",
            "enface",
            *COMMON_TAGS,
        ],
        triggers=[get_event_trigger(MOSAIC_ENFACE_STITCHED, project_name=project_name)],
        build=False,
        push=False,
    )
