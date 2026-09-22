"""Per-scan-config gate, applied outside processing milestone decorators."""

from functools import wraps
from inspect import signature

from prefect import get_run_logger
from prefect.states import Completed

from opticstream.config.psoct_scan_config import get_psoct_scan_config


MATLAB_DISABLED_STATE = "MatlabDisabled"


def matlab_flow_enabled(func):
    """Skip a MATLAB flow before its body/milestones, preserving its signature.

    Tile flows use their explicit config (including manual-run overrides).
    Registration flows resolve the project's block because they accept no config.
    Fail closed if that block cannot be loaded instead of silently running MATLAB.
    """
    sig = signature(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        config = bound.arguments.get("config")
        if config is None:
            config = get_psoct_scan_config(bound.arguments["project_name"])
        if not config.matlab_processing_enabled:
            message = "MATLAB processing disabled by this project's PSOCTScanConfig."
            get_run_logger().info("Skipping %s: %s", func.__name__, message)
            return Completed(name=MATLAB_DISABLED_STATE, message=message)
        return func(*args, **kwargs)

    return wrapper
