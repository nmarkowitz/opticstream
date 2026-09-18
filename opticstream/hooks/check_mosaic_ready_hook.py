"""Hook to emit MOSAIC_READY when all batches in a mosaic are processed."""

from __future__ import annotations

from typing import Any

from prefect.logging.loggers import flow_run_logger

from opticstream.config.psoct_scan_config import get_psoct_scan_config
from opticstream.utils.matlab_settings import MATLAB_DISABLED_STATE
from opticstream.events.psoct_event_emitters import emit_mosaic_psoct_event
from opticstream.events.psoct_events import MOSAIC_READY
from opticstream.flows.psoct.utils import mosaic_context_from_ident
from opticstream.state.oct_project_state import OCTMosaicId, OCT_STATE_SERVICE
from opticstream.utils.flow_run_name_parse import (
    missing_required_fields,
    parse_flow_run_name_fields,
)

_REQUIRED_FIELDS = ("project_name", "slice_id", "mosaic_id")


def check_mosaic_ready_hook(flow: Any, flow_run: Any, state: Any) -> None:
    """
    Prefect on_completion hook: emit MOSAIC_READY when every batch in the
    mosaic has been processed.

    Parses ``project_name``, ``slice_id``, and ``mosaic_id`` from the flow
    run name, loads the scan config, computes the expected batch count, and
    checks the mosaic state.
    """
    if getattr(state, "name", None) == MATLAB_DISABLED_STATE:
        return
    logger = flow_run_logger(flow_run, flow)
    parsed = parse_flow_run_name_fields(flow_run.name or "")
    missing = missing_required_fields(parsed, _REQUIRED_FIELDS)
    if missing:
        logger.info(
            "check_mosaic_ready_hook skipped for run %s; missing fields: %s",
            flow_run.name,
            ", ".join(missing),
        )
        return

    mosaic_ident = OCTMosaicId(
        project_name=str(parsed["project_name"]),
        slice_id=int(parsed["slice_id"]),
        mosaic_id=int(parsed["mosaic_id"]),
    )

    scan_config = get_psoct_scan_config(str(parsed["project_name"]))
    ctx = mosaic_context_from_ident(mosaic_ident, scan_config)
    total_batches = ctx.grid_size_x(scan_config)

    mosaic_view = OCT_STATE_SERVICE.peek_mosaic(mosaic_ident=mosaic_ident)
    if mosaic_view is None:
        logger.info(
            "check_mosaic_ready_hook: no mosaic state for %s", mosaic_ident
        )
        return

    if mosaic_view.all_batches_done(total_batches=total_batches):
        emit_mosaic_psoct_event(MOSAIC_READY, mosaic_ident)
        logger.info(
            "check_mosaic_ready_hook: emitted MOSAIC_READY for %s", mosaic_ident
        )
