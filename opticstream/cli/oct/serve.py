from typing import List, Optional

from cyclopts import App
from prefect import serve

from opticstream.cli.oct import oct_cli
from opticstream.utils.runtime_paths import chdir_to_opticstream_install_root
from opticstream.flows.psoct.acquisition_preview_flow import to_deployment as acquisition_preview_deployments

from opticstream.flows.psoct.mosaic_enface_qc_flow import (
    to_deployment as mosaic_enface_qc_deployments,
)
from opticstream.flows.psoct.mosaic_process_flow import (
    to_deployment as mosaic_process_deployments,
)
from opticstream.flows.psoct.mosaic_upload_flow import (
    to_deployment as mosaic_upload_deployments,
)
from opticstream.flows.psoct.mosaic_volume_stitch_flow import (
    to_deployment as mosaic_volume_stitch_deployments,
)
from opticstream.flows.psoct.mosaic_volume_upload_flow import (
    to_deployment as mosaic_volume_upload_deployments,
)
from opticstream.flows.psoct.slice_process_flow import (
    to_deployment as slice_process_deployments,
)
from opticstream.flows.psoct.tile_batch_process_flow import (
    to_deployment as tile_batch_process_deployments,
)
from opticstream.flows.psoct.tile_batch_upload_flow import (
    to_deployment as tile_batch_upload_deployments,
)

serve_cli = oct_cli.command(App(name="serve"))

COMMON_TAGS = ["linc", "psoct"]


def _normalize_project_name(project_name: str) -> Optional[str]:
    """
    Force project_name to be provided.
    If project_name == "all", return None so triggers apply to all projects.
    """
    if not project_name:
        raise ValueError("project_name is required. Use 'all' to target all projects.")
    return None if project_name == "all" else project_name


def build_deployments(
    *,
    project_name: str,
    deployment_name: str = "local",
    concurrency_limit: int = 1,
) -> List:
    """
    Build all standard deployments.

    project_name is required.
    Use project_name='all' to make it None internally.
    """
    normalized_project_name = _normalize_project_name(project_name)

    deployments: list = []

    # ============================================================================
    # Tile Batch Flow Deployments
    # ============================================================================
    deployments.extend(
        tile_batch_process_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
            concurrency_limit=concurrency_limit,
        )
    )
    deployments.extend(
        tile_batch_upload_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )

    # ============================================================================
    # Mosaic Flow Deployments
    # ============================================================================
    deployments.extend(
        mosaic_process_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )
    deployments.extend(
        mosaic_volume_stitch_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )
    deployments.extend(
        mosaic_upload_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )
    deployments.extend(
        mosaic_volume_upload_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )

    # ============================================================================
    # Slice Flow Deployments
    # ============================================================================
    deployments.extend(
        slice_process_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )

    # ============================================================================
    # Cross-cutting QC/Notification Deployments
    # (mosaic-level consumers kept separate from core hierarchy)
    # ============================================================================
    deployments.extend(
        mosaic_enface_qc_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )

    deployments.extend(acquisition_preview_deployments(
        deployment_name=deployment_name, extra_tags=COMMON_TAGS,
    ))
    return deployments


def build_register_deployments(
    *,
    project_name: str,
    deployment_name: str = "local",
) -> List:
    """
    Build only the two register deployments.
    project_name is required.
    Use project_name='all' to make it None internally.
    """
    normalized_project_name = _normalize_project_name(project_name)

    return list(
        slice_process_deployments(
            project_name=normalized_project_name,
            deployment_name=deployment_name,
            extra_tags=COMMON_TAGS,
        )
    )


@serve_cli.command
def register(
    project_name: str = "all",
    deployment_name: str = "local",
):
    chdir_to_opticstream_install_root()
    serve(
        *build_register_deployments(
            project_name=project_name,
            deployment_name=deployment_name,
        )
    )


@serve_cli.command
def all(
    project_name: str,
    deployment_name: str = "local",
    concurrency_limit: int = 1,
    exclude: Optional[list[str]] = None,
):
    chdir_to_opticstream_install_root()
    services = build_deployments(
        project_name=project_name,
        deployment_name=deployment_name,
        concurrency_limit=concurrency_limit,
    )

    if exclude:
        exclude_set = set(exclude)
        services = [
            deployment
            for deployment in services
            if getattr(deployment, "flow_name", None) not in exclude_set
        ]

    if not services:
        raise ValueError("No deployments to serve (all services excluded).")

    serve(*services)
