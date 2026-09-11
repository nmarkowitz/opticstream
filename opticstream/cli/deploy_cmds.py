from __future__ import annotations

"""
Deployment-related commands for the opticstream CLI.

Provides the `opticstream deploy` command, which orchestrates Prefect
deployments backing the opticstream workflows.
"""

from importlib import import_module
import inspect
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .root import app


@dataclass(frozen=True)
class FlowDeploymentSpec:
    """
    Specification for deploying one or more Prefect deployments for a logical flow.

    Attributes
    ----------
    module_path:
        Python import path to the module containing the deployment configuration.
        This is used only to locate the backing file on disk for `prefect deploy`.
    deployment_names:
        One or more deployment variable names defined in the module's
        `if __name__ == "__main__":` block. Each will be passed to
        `prefect deploy`, e.g. `prefect deploy path/to/file.py:deployment_name`.
    description:
        Human-readable description shown in CLI help / error messages.
    """

    module_path: str
    deployment_names: Tuple[str, ...]
    description: str


# Mapping from logical flow name to one or more Prefect deployment specs.
#
# These entries intentionally mirror the deployment configuration snippets and
# comments in the individual flow modules.
FLOW_DEPLOYMENTS: Dict[str, FlowDeploymentSpec] = {
    "mosaic": FlowDeploymentSpec(
        module_path="opticstream.flows.psoct.mosaic_process_flow",
        deployment_names=("process_mosaic_event_flow_deployment",),
        description="Event-driven mosaic processing flow (MOSAIC_READY -> process_mosaic_flow).",
    ),
    "volume-stitching": FlowDeploymentSpec(
        module_path="opticstream.flows.volume_stitching_flow",
        deployment_names=("stitch_volume_event_flow_deployment",),
        description="Event-driven 3D volume stitching flow (MOSAIC_ENFACE_STITCHED -> stitch_volume_flow).",
    ),
    "state-management": FlowDeploymentSpec(
        module_path="opticstream.flows.state_management_flow",
        deployment_names=("unified_state_management_event_flow_deployment",),
        description="Unified state management flow handling batch and mosaic events.",
    ),
    "upload": FlowDeploymentSpec(
        module_path="opticstream.flows.psoct.tile_batch_upload_flow",
        deployment_names=(
            # Note: mosaic enface/volume uploads are deployed via `opticstream cli oct deploy`.
            # This CLI entry covers only the tile-batch upload flow.
            # Add mosaic upload deployments here only if their modules define `__main__` deployments.
            "upload_to_dandi_batch_event_flow_deployment",
        ),
        description="Upload flow for tile batches to DANDI.",
    ),
    "slice-registration": FlowDeploymentSpec(
        module_path="opticstream.flows.slice_registration_flow",
        deployment_names=("register_slice_event_flow_deployment",),
        description="Slice registration flow triggered by SLICE_READY events.",
    ),
    "slack-notification": FlowDeploymentSpec(
        module_path="opticstream.flows.slack_notification_flow",
        deployment_names=("slack_enface_notification_deployment",),
        description="Slack notification flow for stitched enface mosaics.",
    ),
}


def _build_prefect_deploy_commands(
    spec: FlowDeploymentSpec,
    extra_prefect_args: Sequence[str] | None = None,
) -> List[List[str]]:
    """
    Build `prefect deploy` commands for a given flow deployment specification.

    This resolves the backing file path for the module and constructs one
    `prefect deploy` command per deployment name.
    """
    if extra_prefect_args is None:
        extra_prefect_args = ()

    module = import_module(spec.module_path)
    file_path = Path(inspect.getfile(module)).resolve()

    commands: List[List[str]] = []
    for deployment_name in spec.deployment_names:
        target = f"{file_path}:{deployment_name}"
        cmd = ["prefect", "deploy", target, *extra_prefect_args]
        commands.append(cmd)
    return commands


@app.command
def deploy(
    flow_name: str,
    *,
    dry_run: bool = False,
    prefect_args: Sequence[str] | None = None,
) -> None:
    """
    Deploy one or more Prefect flows used by opticstream.

    Parameters
    ----------
    flow_name:
        Logical name of the flow to deploy. Supported values include:
        {available_flows}
    dry_run:
        If True, print the underlying `prefect deploy` commands instead of
        executing them.
    prefect_args:
        Optional additional arguments to pass through to `prefect deploy`
        (e.g., `--pool`, `--name`, `--workspace`). These are appended to each
        generated `prefect deploy` command as-is.
    """
    if flow_name not in FLOW_DEPLOYMENTS:
        available = ", ".join(sorted(FLOW_DEPLOYMENTS))
        raise ValueError(f"Unknown flow '{flow_name}'. Available flows: {available}")

    spec = FLOW_DEPLOYMENTS[flow_name]
    commands = _build_prefect_deploy_commands(
        spec=spec,
        extra_prefect_args=prefect_args,
    )

    for cmd in commands:
        if dry_run:
            print(" ".join(cmd))
        else:
            subprocess.run(cmd, check=True)
