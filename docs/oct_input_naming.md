# Configurable OCT input filenames

Edit the existing PSOCTScanConfig block's `acquisition` section after registering
the updated block schema. Restart the watcher after saving the block.
Omitting `filename_pattern` (or setting it to null) preserves legacy parsing.

```json
{
  "filename_pattern": "sub-{subject}_sample-slice{slice}_chunk-{image}_acq-{acquisition}_{modality}{extension}",
  "acquisition_mosaic_map": {"normal0deg": 1, "tilted15deg": 2}
}
```

This is a template, not a regular expression. The only required placeholder is
`image`. Optional placeholders are `slice`, `acquisition`, `subject`, `modality`,
and `extension`. Each may appear once; do not add format specifiers such as `:04d`.
Numeric fields accept zero padding. Matching is case-sensitive and covers the
entire basename. The watcher scans the selected directory, not recursively.

For `sub-MF283_sample-slice037_chunk-0041_acq-normal0deg_spectral.nii`,
slice is 37, image is 41, and the mosaic slot is 1. With two mosaics per slice,
the global source mosaic is 73. The tilted15deg file maps to source mosaic 74.
Watcher mosaic-range filters use these global source IDs. Slice offsets retain
their existing behavior. Image numbers must be 1-based indices within a mosaic;
batch size remains `grid_size_y`, and incomplete batches are not dispatched.

Acquisition labels are arbitrary: change the map to accept `normal5deg` or
another label. Values are mosaic slots, not angle measurements. Slot ordering
must agree with the project's existing two-/three-mosaic illumination layout.
The subject field is matched but does not select a project: do not mix subjects
with overlapping slice/tile IDs in the same watched directory.

## Scanner names without slice or acquisition

If the scanner only numbers its output (`spectral_0001.nii`, `spectral_0002.nii`,
...), set `"filename_pattern": "spectral_{image}.nii"`. The numbers are then one
continuous sequence across mosaics and slices, and `--slice`/`--mosaic` say where
image 1 starts (both default to 1):

```bash
ops oct watch myproject /path/to/scanner/output --slice 1 --mosaic 1
```

With 22 x 16 grids for both illuminations, that places:

| Images | Slice | Mosaic |
|---|---|---|
| 1-352 | 1 | 1 (normal) |
| 353-704 | 1 | 2 (tilted) |
| 705-1056 | 2 | 1 (normal) |
| ... | ... | ... |

Each mosaic holds `grid_size_x * grid_size_y` tiles, using `grid_size_x_normal` or
`grid_size_x_tilted` for its illumination, and its tiles are renumbered from 1. After
the slice's last mosaic the sequence continues with mosaic 1 of the next slice.
Placement depends only on the image number, so restarting the watcher is safe.

`--mosaic` is the mosaic's position within its slice (1..`mosaics_per_slice`, e.g.
1 = normal, 2 = tilted), not a global mosaic id. `--acquisition` may name that
position by its `acquisition_mosaic_map` label instead; you never need both. To
start a folder partway through, e.g. with the tilted mosaic of slice 3, pass
`--slice 3 --mosaic 2`. `--slice-offset` still shifts the resulting slice numbers.

A batch number beyond a mosaic's `grid_size_x` is never dispatched (it is logged
and ignored), whatever the naming.

When the filename already contains `{slice}`/`{acquisition}`, or uses legacy
`mosaic_###_image_####` names, these options act as a filter: only matching files
are dispatched.

No `_spectral.nii` suffix is required. For example, use
`s{slice}_tile{image}_{acquisition}.nii` with
`filename_default_modality: "spectral"`. Or keep `{modality}{extension}` and
set `filename_modality_map` to `{"rawscan": "spectral"}` for `_rawscan.nii`.
The default map accepts spectral, complex, processed, aip, mip, ori, ret, surf,
and dbi. Unknown acquisition/modality labels are ignored. Duplicate files for
the same tile and modality cause an error rather than silently replacing data.

The extension placeholder accepts `.nii`, `.nii.gz`, `.raw`, etc.; the actual
contents must still be supported by the configured reader and tile saving type.
Matching a name does not convert a file format. Files must still meet the
watcher's size/readability/stability requirements.

Generated archive/processed/upload names are unchanged. Custom parsing applies
to input discovery and input references in both direct and event-driven flows.
