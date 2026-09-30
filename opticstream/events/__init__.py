"""
Event system for OCT pipeline workflow.

This module provides event constants and utilities following the design document
naming convention: linc.opticstream.{pipeline}.{hierarchy}.{state}
"""

from opticstream.events.psoct_events import (
    BATCH_ARCHIVED,
    BATCH_COMPLEX_ARCHIVED,
    BATCH_PROCESSED,
    BATCH_READY,
    BATCH_UPLOADED,
    BATCH_COMPLEXED,
    MOSAIC_READY,
    MOSAIC_ENFACE_STITCHED,
    MOSAIC_VOLUME_STITCHED,
    MOSAIC_VOLUME_UPLOADED,
    SLICE_READY,
    SLICE_REGISTERED,
)
from opticstream.events.lsm_events import (
    STRIP_READY,
    STRIP_PROCESSED,
    STRIP_COMPRESSED,
    STRIP_UPLOADED,
    STRIP_ARCHIVED,
    CHANNEL_READY,
    CHANNEL_MIP_STITCHED,
    CHANNEL_VOLUME_STITCHED,
    CHANNEL_VOLUME_UPLOADED,
)
from opticstream.events.utils import get_event_trigger
