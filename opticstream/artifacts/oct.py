"""OCT mosaic/slice/project Prefect artifacts: pure summaries, rendering, and tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from prefect import task
from prefect.logging import get_run_logger

from opticstream.artifacts.common import (
    bool_cell_emoji,
    format_milestone_lines,
    pct,
    publish_table_artifact,
    timestamp_str,
)
from opticstream.state.oct_project_state import (
    OCT_STATE_SERVICE,
    OCTBatchStateView,
    OCTMosaicId,
    OCTMosaicStateView,
    OCTProjectId,
)
from opticstream.state.project_state_core import ProcessingState
from opticstream.utils.naming_convention import normalize_project_name


# ---------------------------------------------------------------------------
# Pure summaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OCTBatchProgressSummary:
    """Aggregate counts for a set of batches (one mosaic or a subset)."""

    total_batches: int
    started: int
    archived: int
    enface_processed: int
    uploaded: int

    @property
    def progress_pct(self) -> float:
        return pct(self.enface_processed, self.total_batches)


def summarize_oct_batches(
    batches: Sequence[OCTBatchStateView],
) -> OCTBatchProgressSummary:
    total = len(batches)
    if total == 0:
        return OCTBatchProgressSummary(0, 0, 0, 0, 0)
    started = sum(1 for b in batches if b.processing_state != ProcessingState.PENDING)
    archived = sum(1 for b in batches if b.archived)
    enface = sum(1 for b in batches if b.enface_processed)
    uploaded = sum(1 for b in batches if b.uploaded)
    return OCTBatchProgressSummary(total, started, archived, enface, uploaded)


# ---------------------------------------------------------------------------
# Artifact keys
# ---------------------------------------------------------------------------


def build_oct_mosaic_artifact_key(project_name: str, mosaic_id: int) -> str:
    return f"{normalize_project_name(project_name)}-mosaic-{mosaic_id}-progress"


def build_oct_project_artifact_key(project_name: str) -> str:
    return f"{normalize_project_name(project_name)}-all-mosaics-progress"


# ---------------------------------------------------------------------------
# Table and description builders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OCTProjectTableStats:
    """Aggregates derived from the all-mosaics project artifact table rows."""

    total_mosaics: int
    complete_mosaics: int
    enface_stitched_count: int
    enface_uploaded_count: int
    volume_stitched_count: int
    volume_uploaded_count: int


def summarize_oct_project_table_rows(
    table: Sequence[dict[str, Any]],
) -> OCTProjectTableStats:
    total_mosaics = len(table)
    complete_mosaics = sum(
        1
        for row in table
        if row["Enface processed"] == row["Total Batches"] and row["Total Batches"] > 0
    )
    enface_stitched_count = sum(1 for row in table if row["Enface Stitched"] == "✅")
    enface_uploaded_count = sum(1 for row in table if row["Enface uploaded"] == "✅")
    volume_stitched_count = sum(1 for row in table if row["3D Volume Stitched"] == "✅")
    volume_uploaded_count = sum(1 for row in table if row["3D Volume Uploaded"] == "✅")
    return OCTProjectTableStats(
        total_mosaics=total_mosaics,
        complete_mosaics=complete_mosaics,
        enface_stitched_count=enface_stitched_count,
        enface_uploaded_count=enface_uploaded_count,
        volume_stitched_count=volume_stitched_count,
        volume_uploaded_count=volume_uploaded_count,
    )


def build_oct_mosaic_batch_status_table_rows(
    mosaic_view: OCTMosaicStateView,
) -> list[dict[str, Any]]:
    """
    One row per batch (sorted by batch id); columns are batch index and per-batch flags only.

    Mosaic-level milestones (enface stitched, enface uploaded, volume stitched/uploaded) appear
    on the **all-mosaics** project table, not here.
    """
    rows: list[dict[str, Any]] = []
    for batch_id in sorted(mosaic_view.batches.keys()):
        b = mosaic_view.batches[batch_id]
        rows.append(
            {
                "Batch": batch_id,
                "Processing": b.processing_state.value,
                "Enface processed": bool_cell_emoji(b.enface_processed),
                "Volume processed": bool_cell_emoji(b.volume_processed),
                "Complexed": bool_cell_emoji(b.complexed),
                "Archived": bool_cell_emoji(b.archived),
                "Batch uploaded": bool_cell_emoji(b.uploaded),
            }
        )
    return rows


def build_oct_mosaic_description(
    *,
    project_name: str,
    mosaic_id: int,
    summary: OCTBatchProgressSummary,
) -> str:
    status_text = (
        "✅ COMPLETE - All batches enface-processed"
        if summary.total_batches > 0
        and summary.enface_processed == summary.total_batches
        else "⏳ IN PROGRESS - Processing batches..."
    )
    return f"""Mosaic {mosaic_id} Processing Progress

Project: {project_name}
Mosaic ID: {mosaic_id}
Last Updated: {timestamp_str()}

The table lists each batch (rows) with processing state; flags use ✅ / ❌.
Batch uploaded is per-batch DANDI upload. Mosaic-level upload/stitch status is on the all-mosaics artifact.

Status: {status_text}

{format_milestone_lines(summary.progress_pct)}
"""


def build_oct_project_mosaic_row(mosaic_view: OCTMosaicStateView) -> dict[str, Any]:
    s = summarize_oct_batches(list(mosaic_view.batches.values()))
    return {
        "Mosaic ID": mosaic_view.mosaic_id,
        "Total Batches": s.total_batches,
        "Started": s.started,
        "Archived": s.archived,
        "Enface processed": s.enface_processed,
        "Uploaded": s.uploaded,
        "Enface Stitched": "✅" if mosaic_view.enface_stitched else "⏳",
        "Enface uploaded": "✅" if mosaic_view.enface_uploaded else "⏳",
        "3D Volume Stitched": "✅" if mosaic_view.volume_stitched else "⏳",
        "3D Volume Uploaded": "✅" if mosaic_view.volume_uploaded else "⏳",
    }


def build_oct_project_description(
    *,
    project_name: str,
    table: list[dict[str, Any]],
) -> str:
    s = summarize_oct_project_table_rows(table)
    return f"""All Mosaics Processing Progress

Project: {project_name}
Total Mosaics: {s.total_mosaics}
Last Updated: {timestamp_str()}

Summary:
- Complete Mosaics: {s.complete_mosaics}/{s.total_mosaics}
- Enface Stitched: {s.enface_stitched_count}/{s.total_mosaics}
- Enface uploaded: {s.enface_uploaded_count}/{s.total_mosaics}
- 3D Volume Stitched: {s.volume_stitched_count}/{s.total_mosaics}
- 3D Volume Uploaded: {s.volume_uploaded_count}/{s.total_mosaics}
"""


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task
def publish_mosaic_artifact_task(mosaic_ident: OCTMosaicId) -> str:
    return publish_oct_mosaic_artifact(mosaic_ident, logger=get_run_logger())


def publish_oct_mosaic_artifact(
    mosaic_ident: OCTMosaicId,
    *,
    logger: Any | None = None,
) -> str:
    logger = logger or logging.getLogger(__name__)
    mosaic_view = OCT_STATE_SERVICE.peek_mosaic(mosaic_ident=mosaic_ident)
    if mosaic_view is None:
        logger.warning(
            "No OCT state found for mosaic %s in project %s",
            mosaic_ident.mosaic_id,
            mosaic_ident.project_name,
        )
        return ""

    batches = list(mosaic_view.batches.values())
    summary = summarize_oct_batches(batches)
    artifact_key = build_oct_mosaic_artifact_key(
        mosaic_ident.project_name,
        mosaic_ident.mosaic_id,
    )
    table_data = build_oct_mosaic_batch_status_table_rows(mosaic_view)
    description = build_oct_mosaic_description(
        project_name=mosaic_ident.project_name,
        mosaic_id=mosaic_ident.mosaic_id,
        summary=summary,
    )
    publish_table_artifact(key=artifact_key, table=table_data, description=description)
    logger.info(
        "Updated artifact %s: %s/%s batches enface-processed (%.1f%%)",
        artifact_key,
        summary.enface_processed,
        summary.total_batches,
        summary.progress_pct,
    )
    return artifact_key


@task
def publish_project_artifact_task(project_ident: OCTProjectId) -> str:
    """
    Prefect artifact for all mosaics in the project (:class:`OCTProjectStateView`).
    """
    return publish_oct_project_artifact(project_ident, logger=get_run_logger())


def publish_oct_project_artifact(
    project_ident: OCTProjectId,
    *,
    logger: Any | None = None,
) -> str:
    logger = logger or logging.getLogger(__name__)
    project_view = OCT_STATE_SERVICE.peek_project(project_ident)

    table_data = []
    for slice_view in project_view.slices.values():
        for mosaic_view in slice_view.mosaics.values():
            table_data.append(build_oct_project_mosaic_row(mosaic_view))

    if not table_data:
        logger.warning(
            "No valid mosaic states found for project %s", project_ident.project_name
        )
        return ""

    artifact_key = build_oct_project_artifact_key(project_ident.project_name)
    description = build_oct_project_description(
        project_name=project_ident.project_name,
        table=table_data,
    )
    publish_table_artifact(key=artifact_key, table=table_data, description=description)
    stats = summarize_oct_project_table_rows(table_data)
    logger.info(
        "Updated unified artifact %s: %s mosaics, %s complete, %s enface stitched, "
        "%s enface uploaded, %s volume stitched, %s volume uploaded",
        artifact_key,
        stats.total_mosaics,
        stats.complete_mosaics,
        stats.enface_stitched_count,
        stats.enface_uploaded_count,
        stats.volume_stitched_count,
        stats.volume_uploaded_count,
    )
    return artifact_key
