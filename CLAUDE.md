# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

OpticStream is a set of Prefect 3 workflows for processing large microscopy datasets. It runs two pipelines: **LSM** (light-sheet strips → channels → volumes) and **PS-OCT** (tile batches → mosaics → slices). The CLI is `opticstream`, also available as `ops` (Cyclopts, `opticstream/cli`).

## Commands

```bash
uv sync --extra dev                  # install (Python >=3.11; git-pinned deps: linc-convert, nifti-zarr, psoct-toolbox)
uv run pytest -ra                    # unit tests (integration tests are excluded by default via addopts)
uv run pytest tests/test_state_guards.py::TestName::test_name   # single test
uv run pytest -o addopts= -m integration -ra   # integration tests; need a Prefect server + Postgres:
docker compose --profile server up -d          #   Prefect at :4200, then: prefect config set PREFECT_API_URL=http://127.0.0.1:4200/api
uv run ruff check opticstream        # lint (ruff is in dev extras; no project-specific ruff config)
mkdocs serve                         # docs (mkdocs.yml, docs/)
```

`tests/matlab/*.m` are MATLAB tests for the bundled MATLAB overlay. They need a MATLAB install and are not run by CI.

## Architecture

**Event-driven flow graph.** Flows do not call downstream flows directly. Each stage emits a Prefect event named `linc.opticstream.{pipeline}.{hierarchy}.{state}` (constants live in `opticstream/events/{lsm,psoct}_events.py`, and emitters in `*_event_emitters.py`). Deployments subscribe to those events through `get_event_trigger` / `create_event_deployment` (`opticstream/utils/deployment_utils.py`), and Jinja templates map the event payload into flow parameters. For example, PS-OCT runs `batch.ready → batch.complexed → batch.processed → mosaic.ready → mosaic stitch → slice.ready → …`. Uploads always run as separate event-triggered flows, so they never block compute. Deployments are registered and served through `ops lsm|oct deploy/serve`, and watchers (`ops lsm|oct watch`, `opticstream/utils/directory_watch.py`, `polling_watcher.py`) watch acquisition directories and emit the first events.

**Payload conventions.** Event payloads carry typed identifiers such as `mosaic_ident` and `strip_ident`, which parse into `OCTMosaicId`, `LSMStripId`, and so on. Most processing parameters come from the project's scan-config **Prefect Block**: `PSOCTScanConfig` saved as `"{project_name}-config"`, or `LSMScanConfig` saved as `"{project_name}-lsm-config"`, both in `opticstream/config/`. A payload may override only the keys whitelisted in the flow utils, e.g. `PROCESS_MOSAIC_FLOW_KWARGS_KEYS` in `opticstream/flows/psoct/utils.py`. `force_rerun` in a payload bypasses the skip guards.

**Project state (PostgreSQL).** `opticstream/state/` stores per-project state as JSONB rows keyed by `(project_type, project_name)` and accessed through a Prefect `SqlAlchemyConnector` block (`project_state_postgres.py`). Every mutation takes a project-scoped Prefect global concurrency lock (limit 1), then loads, mutates, and saves. The services are `OCT_STATE_SERVICE` and `LSM_STATE_SERVICE`, and they expose three kinds of access:
- `open_*`: locked context manager that yields a **mutable** model and saves on exit.
- `read_*`: locked read that returns a frozen **View** model.
- `peek_*`: unlocked, best-effort read.

Mutable models only exist inside `open_*` scopes; use `to_view()` to take a snapshot. See `docs/developer/prefect_state_design.md`.

**Idempotency / milestones.** Flows call `state_guards.should_skip_run` / `enter_flow_stage` to skip work that is already done or to restart it. Milestone decorators (`state/milestone_wrappers_{psoct,lsm}.py`, built on `milestone_runtime.guarded_milestone`) set a boolean field through `set_<field>()` on success and optionally emit the next event. When you add a milestone field, the state model needs matching `set_<field>` and `reset_<field>` methods. Flow hooks in `opticstream/hooks/` (such as `check_mosaic_ready_hook` and `check_slice_ready_hook`) check whether a parent level is complete and emit its `ready` event. Other hooks handle Slack notifications and publishing artifacts.

**MATLAB processing.** The PS-OCT spectral → complex → volume/enface reconstruction runs MATLAB code from `psoct-toolbox` through `utils/matlab_execution.run_matlab_batch_command_or_cli`, which uses the MATLAB Engine when it is importable and falls back to `matlab -batch`. `opticstream/matlab/` is an overlay that gets prepended to the MATLAB path after the toolbox loads, so it overrides toolbox functions. Its `.m` files ship as package-data (listed explicitly in `pyproject.toml`, so a new subpackage directory must be added there). `config/pipeline_opts_builder.py` converts the scan config into the MATLAB opts struct. Stitching uses Fiji (`data_processing/stitch/fiji.py`).

**Uploads.** `tasks/dandi_upload.py` handles DANDI and LINC uploads, and `tasks/archive_file.py` handles archiving.

## Notes

- Dependencies are pinned to specific git commits in `pyproject.toml`. Local editable overrides go in `[tool.uv.sources]` (commented out by default). Don't commit machine-specific `path =` entries.
- Deeper design docs are in `docs/developer/`: `oct_*.md`, `watcher_design_lsm_oct.md`, and `prefect_state_design.md`. Concept docs are in `docs/concepts/`.
