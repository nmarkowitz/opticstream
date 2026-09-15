"""Live Prefect switches for destination-specific upload flows."""

from functools import wraps
from inspect import signature

from prefect.logging.loggers import get_logger
from prefect.variables import Variable


def uploads_enabled(instance: str) -> bool:
    """Missing switches preserve existing behavior; only JSON booleans are valid."""
    if instance not in ("linc", "dandi"):
        return True
    name = f"{instance}-uploads-enabled"
    enabled = Variable.get(name, default=True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{name} must be a JSON boolean (true or false).")
    return enabled


def upload_flow_enabled(func):
    """Place outside milestone decorators to avoid recording skipped uploads."""
    sig = signature(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        instance = bound.arguments["dandi_instance"]
        if not uploads_enabled(instance):
            get_logger(__name__).info(
                "%s uploads disabled; skipping %s without marking uploaded.",
                instance, func.__name__,
            )
            return None
        return func(*args, **kwargs)

    return wrapper
