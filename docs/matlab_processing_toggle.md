# Per-project MATLAB processing toggle

Each PSOCTScanConfig block has a top-level boolean `matlab_processing_enabled`.
It defaults to `true`, including for existing saved configurations.

Set it to `false` to skip `process_tile_batch` (both spectral and complex input)
and `register_slice_flow`, including their event-driven entry points. Skips appear
as `MatlabDisabled` completed flow states, without changing processing milestones
or emitting processing-success/readiness events. Force-rerun does not override it.

The entire tile-batch flow is skipped, including its embedded archiving step.
Standalone archiving/upload flows, Python/Fiji mosaic stitching and QC are not
disabled. This is not an upload toggle and does not stop already-running MATLAB
sessions. Low-level tasks called directly outside these flows are not gated.

Tile flows use the config passed to the run. Registration loads the project's saved
block at entry. Restart watchers after editing the block because they hold a
loaded config; existing scheduled runs with serialized configs can retain old values.
When enabling processing again, manually rerun skipped batches if watcher state
already exists; enabling the field does not automatically replay events.

## Update an existing block

Restart serving processes after installing this code. Registering the schema alone
does not migrate existing block documents. In Miniforge Prompt, using the correct
Prefect API configuration, run (replace the example block name):

```cmd
"C:\Users\Ayman\Documents\opticstream_nm\opticstream\.venv\Scripts\python.exe" -c "from opticstream.config.psoct_scan_config import PSOCTScanConfig; PSOCTScanConfig.register_type_and_schema(); b = PSOCTScanConfig.load('human-10um-psoct-config'); b.matlab_processing_enabled = False; b.save('human-10um-psoct-config', overwrite=True)"
```

This updates only that block, preserving its other validated fields. Fix any
pre-existing validation errors first. Refresh the block editor to see the field.
Use `True` to re-enable. No global Prefect Variable is used.

## Refresh every PSOCT block after additive schema changes

Use the installed code version and the Prefect API/profile for your dashboard:

```cmd
".venv\Scripts\python.exe" -m opticstream.config.migrate_psoct_blocks
".venv\Scripts\python.exe" -m opticstream.config.migrate_psoct_blocks --apply
```

The first command only validates. The second registers the current schema and
resaves all named PSOCTScanConfig blocks, retaining existing values and filling
new fields with their defaults. It does not set every MATLAB toggle to false.
All blocks must validate before any are saved. Secret values are not printed.
Writes are sequential, not transactional; a save failure reports partial progress.
Pause block editing and preferably processing during migration; refresh the UI
and restart consumers afterward. This is an explicit deployment step, not an
automatic action during imports or watcher startup. Renamed/removed fields or new
required fields without defaults need a dedicated data migration first; this tool
is intended for backward-compatible additive changes.
