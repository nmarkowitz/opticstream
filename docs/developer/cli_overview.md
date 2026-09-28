---
title: CLI overview
---

# CLI overview

The OpticStream CLI is implemented with Cyclopts under `opticstream.cli` and
is exposed as `opticstream` (with alias `ops`).

## Root app and command groups

- Root app: `opticstream` (`opticstream.cli.root:app`)
- Command groups: `init`, `lsm`, `oct`, `utils`
- Additional top-level command: `deploy`

## Command tree

### `init`

- `opticstream init blocks`

### `lsm`

- `opticstream lsm setup PROJECT_NAME [--project-base-path ... --info-file ... --output-path ...]`
- `opticstream lsm create-lock PROJECT_NAME`
- `opticstream lsm update-block`
- `opticstream lsm watch PROJECT_NAME [WATCH_DIR] [--slice-offset ... --stability-seconds ... --poll-interval ... --direct/--no-direct --force-resend]`
- `opticstream lsm serve [--concurrent-workers ...]`
- `opticstream lsm reset PROJECT_NAME --slice N [--channel N --strip N]`
- `opticstream lsm state mark PROJECT_NAME {slice|channel|strip} FIELD [VALUE] --slice N [--channel N --strip N]`

### `oct`

- `opticstream oct setup PROJECT_NAME [--project-base-path ... --grid-size-x-normal ... --grid-size-x-tilted ... --grid-size-y ... --dandi ... --dandi-path ... --archive-path ... --interpolation {wavelength,phase_calibration} --num-workers ... --linphase-file ... --dspphase-file ... --dispcomp-file ... --dphase-file ... --acq-mosaic-map normal0deg=1,tilted15deg=2 --filename-pattern 'spectral_{image}.nii']`
- `opticstream oct create-lock PROJECT_NAME`
- `opticstream oct update-block`
- `opticstream oct watch PROJECT_NAME FOLDER_PATH [MOSAIC_RANGES] [--slice-offset ... --stability-seconds ... --poll-interval ... --direct/--no-direct --refresh ... --force-resend --min-complex-file-size-bytes ... --verbose]`
- `opticstream oct deploy [--project-name ... --deployment-name ... --work-pool-name ...]`
- `opticstream oct serve register [--project-name ... --deployment-name ...]`
- `opticstream oct serve all PROJECT_NAME [--deployment-name ... --exclude ...]`
- `opticstream oct state reset PROJECT_NAME [--slice N --mosaic N --batch N]`
- `opticstream oct state mark PROJECT_NAME {slice|mosaic|batch} FIELD [VALUE] [--slice N --mosaic N --batch N]`

### `utils`

- `opticstream utils clear [--flows --deployments --automations --name ... --dry-run]`
- `opticstream utils cancel-flow-runs [--states ... --name ... --older-than-days ... --limit ... --delete --dry-run]`
- `opticstream utils hash-dir --input DIR --output CSV [--algo sha256]`
- `opticstream utils convert-image --input ... --output ... [--angle-to-rgb --window-min ... --window-max ... --mat-variable ... --slice-index ... --quality ... --output-format ...]`
- `opticstream utils repull-deps [--dependency ... --dry-run]`
- `opticstream utils inspect-deps [--dependency ...]`

### Top-level deployment helper

- `opticstream deploy FLOW_NAME [--dry-run --prefect-args ...]`

Valid `FLOW_NAME` values are currently:
`mosaic`, `volume-stitching`, `state-management`, `upload`,
`slice-registration`, `slack-notification`.

## Notes

```bash
opticstream --help      # or: ops --help
opticstream deploy ...  # or: ops deploy ...
```

