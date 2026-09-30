"""
Event name constants following the design document naming convention.

All events follow the pattern: linc.opticstream.psoct.{hierarchy}.{state}
"""

# Batch-Level Events
BATCH_READY = "linc.opticstream.psoct.batch.ready"  # Batch of tiles detected (to start converting to complex data)
BATCH_COMPLEXED = "linc.opticstream.psoct.batch.complexed"  # Batch complexed (triggers complex to processed flow)
BATCH_PROCESSED = "linc.opticstream.psoct.batch.processed"  # Batch processed (triggers state management, emits mosaic.ready when all batches done)
BATCH_ARCHIVED = "linc.opticstream.psoct.batch.archived"  # Batch archived and compressed (triggers upload flow, state management)
BATCH_COMPLEX_ARCHIVED = "linc.opticstream.psoct.batch.complex_archived"  # MATLAB complex tiles archived (triggers complex upload flow)
BATCH_UPLOADED = "linc.opticstream.psoct.batch.uploaded"  # Batch uploaded to LINC storage (triggers upload completion handlers)

# Mosaic-Level Events
MOSAIC_READY = "linc.opticstream.psoct.mosaic.ready"  # All tiles in mosaic processed (all batches complete), triggers mosaic stitching flow # Mosaic started (triggers state management, emits mosaic.ready when all mosaics done)
MOSAIC_VOLUME_STITCHED = "linc.opticstream.psoct.mosaic.volume_stitched"  # All volume modalities stitched (triggers mosaic state management, upload flow)
MOSAIC_VOLUME_UPLOADED = "linc.opticstream.psoct.mosaic.volume_uploaded"  # All volume modalities uploaded to LINC storage (triggers upload completion handlers)
MOSAIC_ENFACE_STITCHED = "linc.opticstream.psoct.mosaic.uploaded"  # All modalities uploaded to LINC storage (triggers upload completion handlers)
MOSAIC_ENFACE_UPLOADED = "linc.opticstream.psoct.mosaic.enface_uploaded"  # All enface modalities uploaded to LINC storage (triggers upload completion handlers)
# Slice-Level Events
SLICE_READY = "linc.opticstream.psoct.slice.ready"  # Both mosaics in slice are stitched, ready for registration (triggers slice registration flow)
SLICE_REGISTERED = "linc.opticstream.psoct.slice.registered"  # Slice registration complete (triggers slice state management, upload flow)
