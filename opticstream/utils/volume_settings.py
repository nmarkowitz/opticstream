"""Guard volume uploads before milestone bookkeeping, including stale events."""
from functools import wraps
from inspect import signature

from prefect.logging.loggers import get_logger
from opticstream.config.psoct_scan_config import get_psoct_scan_config


def volume_output_upload_enabled(func):
    sig = signature(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        ident = bound.arguments["mosaic_ident"]
        config = get_psoct_scan_config(ident.project_name)
        if not config.processing.save_volume_outputs:
            get_logger(__name__).info("Volume output disabled for %s; skipping volume upload", ident.project_name)
            return {"uploaded": 0}
        if not config.stitch_3d_volumes:
            get_logger(__name__).info("3D volume stitching disabled for %s; skipping volume upload", ident.project_name)
            return {"uploaded": 0}
        return func(*args, **kwargs)

    return wrapper
