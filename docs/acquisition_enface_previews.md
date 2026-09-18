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

Within each batch, tiles advance along Y; batches advance along X. Set `traversal`
to `snake-by-columns` if alternate batches reverse direction. Default is
`column-by-column`. Individual images are not flipped. Coordinates use actual
image dimensions and `acquisition.tile_overlap` (percentage). These are nominal
grid QC previews, not image-registered/Fiji-optimized mosaics. Inspect alignment
against a known acquisition before relying on the preview geometry.

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
