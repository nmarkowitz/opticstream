# Configurable OCT input filenames

Edit the existing PSOCTScanConfig block's `acquisition` section after registering
the updated block schema. Restart the watcher after saving the block.
Omitting `filename_pattern` (or setting it to null) preserves legacy parsing.

```json
{
  "filename_pattern": "sub-{subject}_sample-slice{slice}_chunk-{image}_acq-{acq}_{modality}{extension}",
  "acquisition_mosaic_map": {"normal0deg": 1, "tilted15deg": 2}
}
```

This is a template, not a regular expression. The only required placeholder is
`image`. Optional placeholders are `slice`, `acq` (the acquisition label), `subject`, `modality`,
and `extension`. Each may appear once; do not add format specifiers such as `:04d`.
Numeric fields accept zero padding. Matching is case-sensitive and covers the
entire basename. The watcher scans the selected directory, not recursively.

For `sub-MF283_sample-slice037_chunk-0041_acq-normal0deg_spectral.nii`,
slice is 37, image is 41, and the mosaic slot is 1. With two mosaics per slice,
the global source mosaic is 73. The tilted15deg file maps to source mosaic 74.
Watcher mosaic-range filters use these global source IDs. Slice offsets retain
their existing behavior. Image numbers must be 1-based indices within a mosaic;
batch size remains `grid_size_y` (`grid_size_y_tilted` for tilted mosaics when set),
and incomplete batches are not dispatched.

Acquisition labels are arbitrary: change the map to accept `normal5deg` or
another label. Values are mosaic slots, not angle measurements. Slot ordering
must agree with the project's existing two-/three-mosaic illumination layout.
The subject field is matched but does not select a project: do not mix subjects
with overlapping slice/tile IDs in the same watched directory.

## Scanner names without slice or acquisition

If the scanner only numbers its output (`spectral_0001.nii`, `spectral_0002.nii`,
...), set `"filename_pattern": "spectral_{image}.nii"`. The numbers are then one
continuous sequence across mosaics and slices. A new project starts at slice 1
mosaic 1, so no options are needed:

```bash
ops oct watch myproject /path/to/scanner/output
```

With 22 x 16 grids for both illuminations, that places:

| Images | Slice | Mosaic |
|---|---|---|
| 1-352 | 1 | 1 (normal) |
| 353-704 | 1 | 2 (tilted) |
| 705-1056 | 2 | 1 (normal) |
| ... | ... | ... |

Each mosaic holds `grid_size_x * grid_size_y` tiles, using `grid_size_x_normal` or
`grid_size_x_tilted` (and `grid_size_y_tilted`, when set) for its illumination, and
its tiles are renumbered from 1. After the slice's last mosaic the sequence
continues with mosaic 1 of the next slice.

### Where the sequence starts is saved in project state

The watcher records which image is tile 1 of which slice/mosaic, and in which
folder (`sequence_anchor`, shown by `ops oct state show`). On each start:

- **No `--slice`/`--mosaic`, same folder:** resume the saved start. Restarting
  the watcher is safe; batches already in project state are skipped.
- **No `--slice`/`--mosaic`, new folder:** continue with the mosaic after the last
  one recorded in project state, starting at the folder's lowest image number
  (1 if it is empty).
- **`--slice S --mosaic M`:** restart there. Slice S mosaic M and every later mosaic
  are cleared from project state, so they are processed, archived and stitched
  again. For example `--slice 5 --mosaic 2` redoes slice 5 mosaic 2, and the next
  mosaic after it is slice 6 mosaic 1.

When restarting, the first new file is taken to be:

- in the folder already in use: the image after the highest number present (the
  scanner kept counting; older files are ignored);
- in a new folder: its lowest image number, or image 1 if it is empty.

Pass `--start-image N` when that guess is wrong, e.g. the scanner restarted at 1 in
the same folder and has already written some files. Running the same
`--slice`/`--mosaic` again on the same folder resumes rather than clearing again;
add `--start-image` to redo that mosaic once more.

`--mosaic` is the mosaic's position within its slice (1..`mosaics_per_slice`, e.g.
1 = normal, 2 = tilted), not a global mosaic id. `--acquisition` may name that
position by its `acquisition_mosaic_map` label instead; you never need both.
`--slice-offset` still shifts the resulting slice numbers.

A batch number beyond a mosaic's `grid_size_x` is never dispatched (it is logged
and ignored), whatever the naming.

When the filename already contains `{slice}`/`{acq}`, or uses legacy
`mosaic_###_image_####` names, these options act as a filter: only matching files
are dispatched.

No `_spectral.nii` suffix is required. For example, use
`s{slice}_tile{image}_{acq}.nii` with
`filename_default_modality: "spectral"`. Or keep `{modality}{extension}` and
set `filename_modality_map` to `{"rawscan": "spectral"}` for `_rawscan.nii`.
The default map accepts spectral, complex, processed, aip, mip, ori, ret, surf,
and dbi. Unknown acquisition/modality labels are ignored. Duplicate files for
the same tile and modality cause an error rather than silently replacing data.

Batches contain only the processing input for `tile_saving_type` (`spectral` for
spectral input; `complex`/`processed` for complex; all three for
`complex_with_spectral`; every mapped label for `processed_with_spectral`). Other
mapped labels in the same folder, such as `processed_aip: aip`, are not batched;
labels mapped to aip/mip/ori/ret are what acquisition enface previews read
(see acquisition_enface_previews.md).

The extension can be left out: a pattern with no `{extension}` and no literal
extension at its end, such as `sub-test_tile-{image}_acq-{acq}_{modality}`, matches
names ending in `.nii`, `.nii.gz`, `.mat` or `.raw` (other endings, e.g. `.nii.bak`,
are ignored). A literal extension (`..._{modality}.nii`) is still matched exactly.

The extension placeholder accepts `.nii`, `.nii.gz`, `.raw`, etc.; the actual
contents must still be supported by the configured reader and tile saving type.
Matching a name does not convert a file format. Files must still meet the
watcher's size/readability/stability requirements.

Generated archive/processed/upload names are unchanged. Custom parsing applies
to input discovery and input references in both direct and event-driven flows.
