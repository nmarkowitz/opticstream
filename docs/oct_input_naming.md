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

This is a template, not a regular expression. Required placeholders are `slice`,
`image`, and `acquisition`. Optional placeholders are `subject`, `modality`, and
`extension`. Each may appear once; do not add format specifiers such as `:04d`.
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
