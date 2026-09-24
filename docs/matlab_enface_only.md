# MATLAB enface-only disk output

Each PSOCTScanConfig has `processing.save_volume_outputs` (default `true`).
Set it to `false` to stop writing per-tile dBI, R3D and O3D NIfTI volumes.
MATLAB still reconstructs intermediate volumes in memory to calculate enface maps.
This saves disk space and write time, not the reconstruction memory allocation.

Both spectral and complex indexed MATLAB batch routes honor the flag, in serial
and parallel execution. OpticStream bundles the overrides in `opticstream/matlab`;
its normal MATLAB connector puts this path first. No edits to `.venv` are required.

Enface outputs, raw archives, raw uploads, enface stitching and enface events are
unchanged. Volume output validation, volume stitching, and volume uploads are
skipped. Skipped volume work does not emit volume-success events or set volume
milestones. Existing volume files and completed milestones are not deleted/reset.
Slice registration remains enabled; this setting controls reconstructed tile
volumes and their mosaic outputs, not registration-derived orientation products.

`processing.save_enface_2d_as_3d` is independent: false stores enface maps as 2D
NIfTI instead of X-by-Y-by-1. `stitch_3d_volumes` (default `true`) is an
independent switch: set it to `false` to keep saving per-tile volumes but skip
mosaic volume stitching (including focus finding) and mosaic volume uploads.
Enabling volume saving does not override disabled stitching.

## Enable for an existing block

Stop the watcher and serve processes first. Run in Miniforge Prompt with the same
PREFECT_API_URL as your services (example uses the current LAN server):

```bat
cd /d C:\Users\Ayman\Documents\opticstream_nm\opticstream
set "PREFECT_API_URL=http://172.20.126.209:4200/api"
".venv\Scripts\opticstream.exe" oct update-block
".venv\Scripts\python.exe" -c "from opticstream.config.psoct_scan_config import PSOCTScanConfig; b=PSOCTScanConfig.load('human-10um-psoct-config'); b.processing.save_volume_outputs=False; b.save('human-10um-psoct-config', overwrite=True)"
```

Replace the block name to target another project. This load/save also migrates
that block to the registered schema, preserving its other values. Refresh the
dashboard to see the new setting under Processing. Restart watcher and serve
processes so cached configs and code are refreshed. No project-state reset is needed
for future batches. Existing completed batches are not automatically reprocessed.
