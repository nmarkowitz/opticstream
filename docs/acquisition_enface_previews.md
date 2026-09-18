# Acquisition-produced enface previews

Two new Prefect flows call linc-convert `mosaic2d()` and send JPEG previews to Slack:

- `preview-enface-batch`: one complete strip of `acquisition.grid_size_y` tiles.
- `preview-enface-acquisition`: every tile in one acquisition/mosaic.

These consume AIP, MIP, orientation (`ori`) and retardance (`ret`) files written by
the acquisition software, independently of spectral processing and MATLAB. They
do not set OpticStream processing/upload milestones. Existing processed-output QC
flows remain separate. Preview images are not uploaded to DANDI or LINC.

## Configure and run

Refresh saved block schemas with `python -m opticstream.config.migrate_psoct_blocks
--apply` using the project's virtual environment. In the block's new
`enface_preview` group, configure all four filename patterns. They are exact Python
format templates (not globs), with `{image}` required and `{slice}`, `{mosaic}`,
`{acquisition}`, `{project}` optional. Numeric formats such as `{image:04d}` are
supported. These templates are separate from the spectral watcher's parser.

For example:

```json
{
  "aip_pattern": "slice-{slice}_acq-{acquisition}_aip_{image:04d}.nii",
  "mip_pattern": "slice-{slice}_acq-{acquisition}_mip_{image:04d}.nii",
  "ori_pattern": "slice-{slice}_acq-{acquisition}_ori_{image:04d}.nii",
  "ret_pattern": "slice-{slice}_acq-{acquisition}_ret_{image:04d}.nii"
}
```

Change names/suffixes to match the acquisition software exactly. Simple names such
as `aip_{image:04d}.nii` are supported when the folder contains only that acquisition.
NIfTI (.nii/.nii.gz) and formats supported by the existing QC loader are accepted;
only 2D maps or maps with singleton trailing dimensions are supported. All four
modalities must be present for every expected tile before a candidate is ready.

From Miniforge Prompt, with the project's venv activated and Prefect API configured:

```cmd
opticstream oct watch-enface human-10um D:\data\acquisition --slice 1 --mosaic 1 --acquisition normal
```

This command runs the flows directly as files stabilize; it does not require
`oct serve`. `oct serve all` additionally registers the two manual preview flows.
The existing `oct watch` remains unchanged; run `watch-enface` separately.
Use one watcher per acquisition, and do not run a manual preview for the same
acquisition concurrently. Select the correct mosaic ID (normal vs tilted) so the
existing `grid_size_x_normal`/`grid_size_x_tilted` selects the correct batch count.
Slice/mosaic identify outputs explicitly; acquisition is the exact filename label.

## Geometry and readiness

Tiles are numbered consecutively within the acquisition starting at `first_image`
(default 1). Batch 1 contains the first `grid_size_y` tiles, batch 2 the next set,
etc. Total tiles = selected `grid_size_x` * `grid_size_y`.

In the block editor, `enface_preview.grid_config` exposes all linc-convert modes:

| grid_type | Valid order values |
| --- | --- |
| row-by-row / snake-by-rows | right-down, left-down, right-up, left-up |
| column-by-column / snake-by-columns | down-right, down-left, up-right, up-left |

The first direction is motion within the first strip; the second is motion between
strips. `right-down` and `down-right` start at top-left; `left-down` and `down-left`
at top-right; `right-up` and `up-right` at bottom-left; `left-up` and `up-left` at
bottom-right. Snake reverses the fast direction on alternate strips.

Default is `column-by-column` / `down-right`. In column-first mode strips advance
along Y and batches along X; row-first swaps those axes. **Batch size stays
grid_size_y and batch count stays the selected grid_size_x**, so row-first has
grid_size_y physical columns and grid_size_x physical rows. This keeps the existing
consecutive batch convention. Individual images are not flipped. Coordinates use actual
image dimensions and `acquisition.tile_overlap` (percentage). These are nominal
grid QC previews, not image-registered/Fiji-optimized mosaics. Inspect alignment
against a known acquisition before relying on the preview geometry.

Existing saved `enface_preview.traversal` settings migrate automatically to
`grid_config.grid_type` with `order=down-right` when loaded/resaved by the block
migration command. Explicit new grid_config values take precedence. Change both
grid_type and order together to a compatible pair. These settings affect the new
acquisition-preview flows, not the existing Fiji-registered processing pipeline.
The new grid_config representation changes preview fingerprints, including after
legacy migration, so existing previews may be regenerated and sent again. Disable
Slack temporarily if you only want to inspect the regenerated images locally.

Orientation uses circular blending; set `orientation_units` to degrees (default)
or radians. AIP/MIP/retardance use ordinary blending. Acquisition originals are
never changed. Temporary 2D NIfTI copies accommodate singleton NIfTI dimensions.

The watcher waits for all expected files to have stable sizes and modification
times (15 seconds by default). This is a heuristic, not a producer completion
handshake; increase `--stability-seconds` if acquisition pauses during writes.
Corrupt or inconsistent tiles cause failure rather than a partial mosaic.

## Outputs, Slack and retries

Outputs and per-scope `progress.json` checkpoints are under
`project_base_path/acquisition-previews/slice-NNN/mosaic-NNN/`, with separate
`batch-NNNN` and `acquisition` directories. Completed work is skipped on restart.
Input path/size/mtime or relevant configuration changes invalidate checkpoints.
Changing only file content without changing size/mtime is not detected.

The existing Slack bot credentials/channel and `slack-notifications-enabled`
variable are reused. When Slack is disabled, stitching still runs; uploading waits
until Slack is enabled. Successful modality uploads are checkpointed; failures
retry after another stability interval. Slack delivery is not exactly-once: a crash
after Slack accepts an upload but before the checkpoint is saved can duplicate it.

`matlab_processing_enabled` does not gate these non-MATLAB flows. Disable batch or
full previews independently with `batch_enabled`/`acquisition_enabled`. Restart the
watcher after editing block fields. A project-state reset does not erase preview
checkpoints; to deliberately resend, archive/move the relevant preview output
directory while the watcher is stopped.
