---
title: CLI commands
---

# CLI commands

This page provides a source-aligned index of the OpticStream CLI commands.

For detailed, auto-generated API documentation of the CLI modules, see the
Python API reference pages under `reference/modules/`.

## Core commands

| Command | Purpose |
|---------|---------|
| `opticstream --help` | Show full command tree and options. |
| `opticstream deploy FLOW_NAME` | Run Prefect deployment command(s) for one logical flow group. |

Valid `FLOW_NAME` values for `opticstream deploy` are:
`mosaic`, `volume-stitching`, `state-management`, `upload`,
`slice-registration`, `slack-notification`.

## `init` group

| Command | Purpose |
|---------|---------|
| `opticstream init blocks` | Register Prefect block schemas used by OpticStream. |

## `lsm` group

| Command | Purpose |
|---------|---------|
| `opticstream lsm setup PROJECT_NAME ...` | Create/update project LSM block and required directories. |
| `opticstream lsm create-lock PROJECT_NAME` | Ensure project-scoped state lock exists. |
| `opticstream lsm update-block` | Register/update `LSMScanConfig` block type metadata. |
| `opticstream lsm watch PROJECT_NAME [WATCH_DIR] ...` | Watch strip folders and dispatch processing (direct or event mode). |
| `opticstream lsm serve ...` | Serve local LSM deployments with `prefect.serve`. |
| `opticstream lsm reset PROJECT_NAME ...` | Remove slice/channel/strip state entries. |
| `opticstream lsm state mark PROJECT_NAME ...` | Mark LSM state fields/status values at slice/channel/strip levels. |

## `oct` group

| Command | Purpose |
|---------|---------|
| `opticstream oct setup PROJECT_NAME ...` | Create/update project PS-OCT block and lock. |
| `opticstream oct create-lock PROJECT_NAME` | Ensure project-scoped OCT state lock exists. |
| `opticstream oct update-block [PROJECT...]` | Register the current `PSOCTScanConfig` schema and re-save saved blocks (all, or the named projects) onto it so new fields appear in the Prefect UI. |
| `opticstream oct watch PROJECT_NAME FOLDER_PATH [MOSAIC_RANGES] ...` | Watch spectral data and dispatch batch processing (direct or event mode). |
| `opticstream oct deploy ...` | Create Prefect deployments from in-repo flow sources. |
| `opticstream oct serve register ...` | Serve registration/dependent OCT flows for local execution. |
| `opticstream oct serve all PROJECT_NAME ...` | Serve the full OCT deployment set. |
| `opticstream oct state reset PROJECT_NAME ...` | Remove slice/mosaic/batch state entries. |
| `opticstream oct state mark PROJECT_NAME ...` | Mark OCT state fields/status values at slice/mosaic/batch levels. |

## `utils` group

| Command | Purpose |
|---------|---------|
| `opticstream utils clear ...` | Clear Prefect flows/deployments/automations (supports dry-run). |
| `opticstream utils cancel-flow-runs ...` | Cancel (and optionally delete) stale flow runs. |
| `opticstream utils hash-dir ...` | Hash all files in a directory into a CSV manifest. |
| `opticstream utils convert-image ...` | Convert source image/volume data to compressed output formats. |
| `opticstream utils repull-deps ...` | Reinstall key git-based dependencies with `uv pip install`. |
| `opticstream utils inspect-deps ...` | Inspect installed dependency versions and source locations. |

## Acquisition watcher quick links

| Command | Purpose | Operator guide | Design layer | Implementation |
|---------|---------|----------------|--------------|------------------|
| `opticstream lsm watch` | New LSM strip folders → `STRIP_READY` | [LSM workflow](../user-guides/lsm_workflow.md#watching-for-new-strip-folders) | [Concepts: acquisition watchers](../concepts/acquisition_watchers.md) | [Developer: watcher design](../developer/watcher_design_lsm_oct.md) |
| `opticstream oct watch` | Spectral tiles → batch processing or `BATCH_READY` | [PS-OCT workflow](../user-guides/psoct_workflow.md#watching-for-new-spectral-batches) | [Concepts: acquisition watchers](../concepts/acquisition_watchers.md) | [Developer: watcher design](../developer/watcher_design_lsm_oct.md) |

Related setup commands: `opticstream oct setup`, LSM config blocks as documented under configuration and LSM workflow.
