# Acquisition-produced enface previews

Two new Prefect flows call linc-convert `mosaic2d()` and send JPEG previews to Slack:

- `preview-enface-batch`: one complete strip of `acquisition.grid_size_y` tiles.
- `preview-enface-acquisition`: every tile in one acquisition/mosaic.

These consume AIP, MIP, orientation (`ori`) and retardance (`ret`) files written by
the acquisition software, plus surface-finding (`surf`) maps when the project maps
them, independently of spectral processing and MATLAB. Only JPEGs are written; the
stitched NIfTI that mosaic2d produces is kept in a scratch folder and deleted. They
do not set OpticStream processing/upload milestones. Preview images are not
uploaded to DANDI or LINC.

These previews are the pipeline's Slack QC images. The pipeline's own stitched 2D
mosaics (`slice-NN/stitched/mosaic_NNN_*.jpg`) are still written but are posted to
Slack only when the block's `stitched_enface_slack_upload` is true (default false).

## Configure and run

The maps are found with the project's input naming, the same settings the
spectral watcher uses (see oct_input_naming.md):

- `acquisition.filename_pattern` must contain `{modality}`.
- `acquisition.filename_modality_map` maps each filename label to a type. The
  labels mapped to `aip`, `mip`, `ori` and `ret` are the preview maps (all four
  required); a label mapped to `surf` adds a surface-finding preview.

For example, a folder holding

```
sub-test_tile-0011_acq-normal0deg_spectral.nii
sub-test_tile-0011_acq-normal0deg_processed_aip.nii
sub-test_tile-0011_acq-normal0deg_processed_mip.nii
sub-test_tile-0011_acq-normal0deg_processed_orientation.nii
sub-test_tile-0011_acq-normal0deg_processed_retardance.nii
sub-test_tile-0011_acq-normal0deg_processed_surface_finding.nii
```

uses

```json
{
  "filename_pattern": "sub-test_tile-{image}_acq-{acq}_{modality}",
  "filename_modality_map": {
    "spectral": "spectral",
    "processed_aip": "aip",
    "processed_mip": "mip",
    "processed_orientation": "ori",
    "processed_retardance": "ret",
    "processed_surface_finding": "surf"
  }
}
```

The pattern may leave out the extension; it then matches `.nii`, `.nii.gz`,
`.mat` or `.raw` (see oct_input_naming.md). Labels missing from the map (here
`processed`) are ignored. The batch watcher puts only the processing input type into batches
(`spectral` for spectral `tile_saving_type`), so the enface maps in the same
folder are left to the previews. With `processed_with_spectral` input, every
mapped file is a processing input.

`{image}` matches any zero padding. Two files for the same modality and image
(e.g. `..._0001_...` and `..._1_...`) are an error. NIfTI (.nii/.nii.gz) and formats
supported by the existing QC loader are accepted; only 2D maps or maps with
singleton trailing dimensions are supported. Every previewed modality (the four
required ones, plus `surf` when mapped) must be present for every expected tile
before a candidate is ready.

Set `acquisition.orientation_units` to `radians` if the acquisition writes
orientation in radians (default `degrees`); radians are converted for linc-convert.

After upgrading, refresh saved blocks with `python -m opticstream.config.migrate_psoct_blocks
--apply` using the project's virtual environment. It removes the old
`enface_preview` group (its per-modality patterns, grid settings, `first_image`
and enable switches), keeping its `orientation_units` under `acquisition`.
Blocks that were not re-saved still load; the old group is ignored.

From Miniforge Prompt, with the project's venv activated and Prefect API configured:

```cmd
opticstream oct watch human-10um D:\data\acquisition --slice 1 --mosaic 1
```

The main `oct watch` runs the previews in a background polling loop next to batch
dispatch. With continuously numbered files (a `filename_pattern` without `{slice}` or
`{acq}`, see oct_input_naming.md) the previews follow the same sequence:
each mosaic's maps are the images in its range (mosaic 2 of a 22 x 16 grid starts at
image 353), named with its `acquisition_mosaic_map` label. Otherwise previews need
the folder's slice and mosaic (`--slice` with `--mosaic` or `--acquisition`).
Previews do not wait for batch processing (e.g. MATLAB on the last batch): the
full-acquisition preview starts as soon as the last batch's maps have been stable
for `--stability-seconds` (default 15), ahead of any batch previews still pending.
A preview error is logged and never stops batch watching. If the input naming
cannot locate the maps, `oct watch` logs why and runs without previews.

Flags (on both `oct watch` and `oct watch-enface`):

- `--no-previews`: skip the full-acquisition preview (on by default).
- `--batch-previews`: also preview each complete batch (off by default).

To preview without processing, run the standalone watcher instead:

```cmd
opticstream oct watch-enface human-10um D:\data\acquisition --slice 1 --mosaic 1 --acquisition normal0deg
```

Both run the flows directly as files stabilize; neither requires `oct serve`.
`oct serve all` additionally registers the two manual preview flows. Do not run
`watch` and `watch-enface` for the same acquisition at the same time.
Use one watcher per acquisition, and do not run a manual preview for the same
acquisition concurrently. Select the correct mosaic ID (normal vs tilted) so the
existing `grid_size_x_normal`/`grid_size_x_tilted` selects the correct batch count.
Slice/mosaic identify outputs explicitly; acquisition is the exact filename label.

## Geometry and readiness

Tiles are numbered consecutively within the acquisition starting at image 1
(with continuous numbering, at the mosaic's first image). Batch 1 contains the
first `grid_size_y` tiles, batch 2 the next set, etc. Total tiles = selected
`grid_size_x` * `grid_size_y`. Tilted mosaics use `grid_size_y_tilted` in place of
`grid_size_y` when it is set, e.g. a scanner with 22 strips of 18 (normal) and 25
(tilted) tiles uses `grid_size_x_normal=22`, `grid_size_x_tilted=22`,
`grid_size_y=18`, `grid_size_y_tilted=25`.

The grid traversal is fixed (`GRID_TYPE`/`GRID_ORDER` in
`opticstream/flows/psoct/acquisition_preview_flow.py`): linc-convert
`snake-by-columns` / `up-right`. Strips of `grid_size_y` tiles run along columns,
starting at the bottom-left, and alternate strips reverse direction; batches
advance along X. Individual images are not flipped. Coordinates use actual image
dimensions and `acquisition.tile_overlap` (percentage). These are nominal grid QC
previews, not image-registered/Fiji-optimized mosaics. Inspect alignment against a
known acquisition before relying on the preview geometry.

Orientation uses circular blending. AIP/MIP/retardance use ordinary blending.
Acquisition originals are never changed. Temporary 2D NIfTI copies accommodate
singleton NIfTI dimensions.

The watcher waits for all expected files to have stable sizes and modification
times (15 seconds by default). This is a heuristic, not a producer completion
handshake; increase `--stability-seconds` if acquisition pauses during writes.
Corrupt or inconsistent tiles cause failure rather than a partial mosaic.

## Outputs, Slack and retries

All previews for a slice go in one folder, `project_base_path/slice-NN/acquisition_previews/`,
next to the pipeline's `processed/` and `stitched/`. File names carry the mosaic (and
batch) so mosaics never collide:

- full acquisition: `mosaic_001_aip.jpg`, ..., `mosaic_001_surf.jpg` and `mosaic_001_progress.json`
- batch 3: `mosaic_001_batch_0003_aip.jpg`, ... and `mosaic_001_batch_0003_progress.json`

No NIfTI files are written there. `.nii` previews from earlier versions are left
in place and can be deleted.

The `progress.json` files are per-scope checkpoints. Completed work is skipped on restart.
Input path/size/mtime or relevant configuration (the `acquisition` settings)
changes invalidate checkpoints, so previews may be regenerated and sent again
after editing them. Changing only file content without changing size/mtime is
not detected.

The existing Slack bot credentials/channel and `slack-notifications-enabled`
variable are reused. When Slack is disabled, stitching still runs; uploading waits
until Slack is enabled. Successful modality uploads are checkpointed; failures
retry after another stability interval. Slack delivery is not exactly-once: a crash
after Slack accepts an upload but before the checkpoint is saved can duplicate it.

`matlab_processing_enabled` does not gate these non-MATLAB flows. Restart the
watcher after editing block fields. A project-state reset does not erase preview
checkpoints; to deliberately resend, delete or move that scope's `*_progress.json`
while the watcher is stopped (deleting only its `.nii`/`.jpg` restitches without resending).
