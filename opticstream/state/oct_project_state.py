"""OCT-specific project state models and services backed by Postgres."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from datetime import datetime
from typing import ClassVar, Iterator

from pydantic import BaseModel, ConfigDict, Field

from opticstream.utils.naming_convention import normalize_project_name
from opticstream.state.project_state_postgres import PostgresProjectStateRepository
from opticstream.state.project_state_core import (
    BaseProjectStateStore,
    PrefectProjectLock,
    ProcessingState,
    ToViewMixin,
    ensure_limit,
)


"""
Hierarchy (in-memory and persisted JSON):
    project -> slice -> mosaic -> batch
"""


# ------------------------------------------------------------------------------
# Naming helpers
# ------------------------------------------------------------------------------


OCT_PROJECT_TYPE = "oct"
STATE_DB_BLOCK_NAME = "opticstream-db"


def _state_lock_name(project_name: str) -> str:
    return f"{normalize_project_name(project_name)}_oct_state_lock"


def ensure_lock(project_name: str) -> None:
    asyncio.run(ensure_limit(_state_lock_name(project_name), 1))


# ------------------------------------------------------------------------------
# Domain models
# ------------------------------------------------------------------------------


def _derive_slice_id_from_mosaic_id(mosaic_id: int) -> int:
    """
    Derive slice_id from mosaic_id when it is not explicitly provided.

    Current convention: slice_id is computed as mosaic_id // 2.
    """
    return mosaic_id // 2


class OCTProjectId(BaseModel):
    model_config = ConfigDict(frozen=True)
    project_name: str = Field(..., min_length=1)


class OCTSliceId(OCTProjectId):
    slice_id: int = Field(..., ge=0)


class OCTMosaicId(OCTSliceId):
    mosaic_id: int = Field(..., ge=0)


class OCTBatchId(OCTMosaicId):
    batch_id: int = Field(..., ge=0)


class OCTStateView(BaseModel):
    """Base immutable view for OCT state objects."""

    model_config = ConfigDict(frozen=True)

    processing_state: ProcessingState = ProcessingState.PENDING
    processing_started_at: datetime | None = None
    processing_finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @property
    def finished(self) -> bool:
        return self.processing_finished_at is not None


class OCTStateMutationsMixin:
    """Common lifecycle helpers for mutable OCT state objects."""

    def touch(self) -> None:
        self.updated_at = datetime.now()

    def mark_started(self) -> None:
        now = datetime.now()
        self.processing_state = ProcessingState.RUNNING
        self.processing_started_at = now
        self.updated_at = now

    def mark_completed(self) -> None:
        now = datetime.now()
        self.processing_state = ProcessingState.COMPLETED
        self.processing_finished_at = now
        self.updated_at = now

    def mark_failed(self) -> None:
        now = datetime.now()
        self.processing_state = ProcessingState.FAILED
        self.processing_finished_at = now
        self.updated_at = now

    def mark_pending(self) -> None:
        self.processing_state = ProcessingState.PENDING
        self.processing_started_at = None
        self.processing_finished_at = None
        self.touch()


class OCTBatchStateView(OCTStateView):
    """
    Readonly view of a single OCT batch.

    Tracks processing lifecycle flags and timestamps.
    """

    slice_id: int = Field(..., ge=0)
    mosaic_id: int = Field(..., ge=0)
    batch_id: int = Field(..., ge=0)
    complexed: bool = False
    volume_processed: bool = False
    enface_processed: bool = False
    uploaded: bool = False
    archived: bool = False
    complex_uploaded: bool = False


class OCTBatchState(
    OCTStateMutationsMixin, OCTBatchStateView, ToViewMixin[OCTBatchStateView]
):
    model_config = ConfigDict(frozen=False)
    VIEW_MODEL: ClassVar[type[OCTBatchStateView]] = OCTBatchStateView

    def reset_archived(self) -> None:
        self.archived = False
        self.uploaded = False
        self.touch()

    def set_uploaded(self, value: bool = True) -> None:
        self.uploaded = value
        self.touch()

    def set_archived(self, value: bool = True) -> None:
        self.archived = value
        self.touch()

    def set_complexed(self, value: bool = True) -> None:
        self.complexed = value
        self.touch()

    def set_volume_processed(self, value: bool = True) -> None:
        self.volume_processed = value
        self.touch()

    def set_enface_processed(self, value: bool = True) -> None:
        self.enface_processed = value
        self.touch()

    def reset_complexed(self) -> None:
        self.complexed = False
        self.volume_processed = False
        self.enface_processed = False
        self.touch()

    def reset_volume_processed(self) -> None:
        self.volume_processed = False
        self.touch()

    def reset_enface_processed(self) -> None:
        self.enface_processed = False
        self.touch()
        
    def reset_uploaded(self) -> None:
        self.uploaded = False
        self.touch()

    def set_complex_uploaded(self, value: bool = True) -> None:
        self.complex_uploaded = value
        self.touch()

    def reset_complex_uploaded(self) -> None:
        self.complex_uploaded = False
        self.touch()


class OCTMosaicStateView(OCTStateView):
    """Readonly view for one OCT mosaic; batches are keyed by batch_id."""

    slice_id: int = Field(..., ge=0)
    mosaic_id: int = Field(..., ge=0)
    enface_stitched: bool = False
    volume_stitched: bool = False
    enface_uploaded: bool = False
    volume_uploaded: bool = False
    batches: dict[int, OCTBatchStateView] = Field(default_factory=dict)

    def iter_batches(self) -> Iterator[OCTBatchStateView]:
        return iter(self.batches.values())

    def all_batches_done(self, total_batches: int) -> bool:
        """
        Return True if this mosaic has at least one batch and all batches have finished processing.
        """
        if not self.batches:
            return False

        return all(
            i in self.batches and self.batches[i].finished
            for i in range(1, total_batches + 1)
        )


class OCTMosaicState(
    OCTStateMutationsMixin,
    OCTMosaicStateView,
    ToViewMixin[OCTMosaicStateView],
):
    model_config = ConfigDict(frozen=False)
    VIEW_MODEL: ClassVar[type[OCTMosaicStateView]] = OCTMosaicStateView
    batches: dict[int, OCTBatchState] = Field(default_factory=dict)

    def get_batch(self, batch_id: int) -> OCTBatchState | None:
        return self.batches.get(batch_id)

    def get_or_create_batch(self, batch_id: int) -> OCTBatchState:
        if batch_id not in self.batches:
            self.batches[batch_id] = OCTBatchState(
                slice_id=self.slice_id,
                mosaic_id=self.mosaic_id,
                batch_id=batch_id,
            )
        return self.batches[batch_id]

    def set_enface_stitched(self, value: bool = True) -> None:
        self.enface_stitched = value
        self.touch()

    def set_volume_stitched(self, value: bool = True) -> None:
        self.volume_stitched = value
        self.touch()

    def set_enface_uploaded(self, value: bool = True) -> None:
        self.enface_uploaded = value
        self.touch()

    def set_volume_uploaded(self, value: bool = True) -> None:
        self.volume_uploaded = value
        self.touch()

    def reset_enface_stitched(self) -> None:
        self.enface_stitched = False
        self.enface_uploaded = False
        self.touch()

    def reset_volume_stitched(self) -> None:
        self.volume_stitched = False
        self.volume_uploaded = False
        self.touch()

    def reset_enface_uploaded(self) -> None:
        self.enface_uploaded = False
        self.touch()

    def reset_volume_uploaded(self) -> None:
        self.volume_uploaded = False
        self.touch()


class OCTSliceStateView(OCTStateView):
    """Readonly view for one OCT slice; mosaics are keyed by mosaic_id."""

    slice_id: int = Field(..., ge=0)
    mosaics: dict[int, OCTMosaicStateView] = Field(default_factory=dict)
    registered: bool = False
    uploaded: bool = False

    def iter_mosaics(self) -> Iterator[OCTMosaicStateView]:
        return iter(self.mosaics.values())

    def all_mosaics_done(self, total_mosaics: int | None = None) -> bool:
        """
        Return True if the expected number of mosaics for this slice have all finished processing.

        When total_mosaics is not provided, defaults to 2 expected mosaics per slice.
        """
        target = total_mosaics or 2
        if len(self.mosaics) < target:
            return False
        return all(mosaic.finished for mosaic in self.mosaics.values())

    def all_mosaics_enface_stitched(self, total_mosaics: int | None = None) -> bool:
        """
        Return True if the expected number of mosaics for this slice have all been stitched.
        """
        target = total_mosaics or 2
        if len(self.mosaics) < target:
            return False
        return all(mosaic.enface_stitched for mosaic in self.mosaics.values())


class OCTSliceState(
    OCTStateMutationsMixin,
    OCTSliceStateView,
    ToViewMixin[OCTSliceStateView],
):
    model_config = ConfigDict(frozen=False)
    VIEW_MODEL: ClassVar[type[OCTSliceStateView]] = OCTSliceStateView
    mosaics: dict[int, OCTMosaicState] = Field(default_factory=dict)

    def get_mosaic(self, mosaic_id: int) -> OCTMosaicState | None:
        return self.mosaics.get(mosaic_id)

    def get_or_create_mosaic(self, mosaic_id: int) -> OCTMosaicState:
        if mosaic_id not in self.mosaics:
            self.mosaics[mosaic_id] = OCTMosaicState(
                slice_id=self.slice_id,
                mosaic_id=mosaic_id,
            )
        return self.mosaics[mosaic_id]

    def set_registered(self, value: bool = True) -> None:
        self.registered = value
        self.touch()

    def set_uploaded(self, value: bool = True) -> None:
        self.uploaded = value
        self.touch()

    def reset_registered(self) -> None:
        self.registered = False
        self.uploaded = False
        self.touch()

    def reset_uploaded(self) -> None:
        self.uploaded = False
        self.touch()


class OCTSequenceAnchor(BaseModel):
    """
    Where continuous image numbering (e.g. ``spectral_{image}.nii``) starts.

    Written by ``ops oct watch``: ``start_image`` in ``folder`` is tile 1 of
    ``mosaic_slot`` (position within the slice) of ``slice_id``, both before
    ``--slice-offset`` is applied. Images numbered below ``start_image`` are ignored.
    """

    model_config = ConfigDict(frozen=True)

    folder: str
    start_image: int = Field(default=1, ge=1)
    slice_id: int = Field(..., ge=1)
    mosaic_slot: int = Field(..., ge=1)
    created_at: datetime = Field(default_factory=datetime.now)


class OCTProjectStateView(OCTStateView):
    """
    Readonly view of the entire persisted OCT state for one project.
    """

    slices: dict[int, OCTSliceStateView] = Field(default_factory=dict)
    sequence_anchor: OCTSequenceAnchor | None = None

    def last_mosaic_id(self) -> int | None:
        """Highest mosaic id with at least one batch recorded, or None."""
        ids = [mosaic.mosaic_id for mosaic in self.iter_mosaics() if mosaic.batches]
        return max(ids, default=None)

    def get_batch(
        self,
        slice_id: int,
        mosaic_id: int,
        batch_id: int,
    ) -> OCTBatchStateView | None:
        slice_state = self.slices.get(slice_id)
        if slice_state is None:
            return None
        mosaic_state = slice_state.mosaics.get(mosaic_id)
        if mosaic_state is None:
            return None
        return mosaic_state.batches.get(batch_id)

    def iter_mosaics(self) -> Iterator[OCTMosaicStateView]:
        for slice_state in self.slices.values():
            yield from slice_state.iter_mosaics()

    def iter_batches(self) -> Iterator[OCTBatchStateView]:
        for mosaic_state in self.iter_mosaics():
            yield from mosaic_state.iter_batches()


class OCTProjectState(
    OCTStateMutationsMixin,
    OCTProjectStateView,
    ToViewMixin[OCTProjectStateView],
):
    """
    Entire persisted OCT state for one project (mutable form used for storage).
    """

    model_config = ConfigDict(frozen=False)
    VIEW_MODEL: ClassVar[type[OCTProjectStateView]] = OCTProjectStateView
    slices: dict[int, OCTSliceState] = Field(default_factory=dict)

    def get_slice(self, slice_id: int) -> OCTSliceState | None:
        return self.slices.get(slice_id)

    def get_mosaic(self, slice_id: int, mosaic_id: int) -> OCTMosaicState | None:
        slice_state = self.get_slice(slice_id)
        if slice_state is None:
            return None
        return slice_state.get_mosaic(mosaic_id)

    def get_batch(
        self,
        slice_id: int,
        mosaic_id: int,
        batch_id: int,
    ) -> OCTBatchState | None:
        mosaic_state = self.get_mosaic(slice_id, mosaic_id)
        if mosaic_state is None:
            return None
        return mosaic_state.get_batch(batch_id)

    def get_or_create_slice(self, slice_id: int) -> OCTSliceState:
        if slice_id not in self.slices:
            self.slices[slice_id] = OCTSliceState(
                slice_id=slice_id,
            )
        return self.slices[slice_id]

    def get_or_create_mosaic(self, slice_id: int, mosaic_id: int) -> OCTMosaicState:
        slice_state = self.get_or_create_slice(slice_id)
        return slice_state.get_or_create_mosaic(mosaic_id)

    def get_or_create_batch(
        self,
        slice_id: int,
        mosaic_id: int,
        batch_id: int,
    ) -> OCTBatchState:
        mosaic_state = self.get_or_create_mosaic(slice_id, mosaic_id)
        return mosaic_state.get_or_create_batch(batch_id)

    def delete_slice(self, slice_id: int) -> bool:
        if slice_id not in self.slices:
            return False
        del self.slices[slice_id]
        self.touch()
        return True

    def delete_mosaic(self, slice_id: int, mosaic_id: int) -> bool:
        slice_state = self.slices.get(slice_id)
        if slice_state is None or mosaic_id not in slice_state.mosaics:
            return False
        del slice_state.mosaics[mosaic_id]
        self.touch()
        return True

    def delete_batch(self, slice_id: int, mosaic_id: int, batch_id: int) -> bool:
        slice_state = self.slices.get(slice_id)
        if slice_state is None:
            return False
        mosaic_state = slice_state.mosaics.get(mosaic_id)
        if mosaic_state is None or batch_id not in mosaic_state.batches:
            return False
        del mosaic_state.batches[batch_id]
        self.touch()
        return True

    def clear_from_mosaic(self, slice_id: int, mosaic_id: int) -> list[tuple[int, int]]:
        """
        Delete mosaic ``mosaic_id`` and every later mosaic and slice.

        Mosaic ids are global, so "later" means a higher mosaic id. When earlier
        mosaics of ``slice_id`` remain, that slice's registration/upload flags and
        status are reset since it must be registered again. Returns the deleted
        ``(slice_id, mosaic_id)`` pairs.
        """
        removed: list[tuple[int, int]] = []
        for sid in sorted(self.slices):
            if sid < slice_id:
                continue
            slice_state = self.slices[sid]
            doomed = sorted(m for m in slice_state.mosaics if sid > slice_id or m >= mosaic_id)
            for mid in doomed:
                del slice_state.mosaics[mid]
                removed.append((sid, mid))
            if sid > slice_id or not slice_state.mosaics:
                del self.slices[sid]
            elif doomed:
                slice_state.reset_registered()
                slice_state.mark_pending()
        self.touch()
        return removed


def _get_slice_view(
    state: OCTProjectState,
    *,
    slice_id: int,
) -> OCTSliceStateView | None:
    slice_state = state.slices.get(slice_id)
    return None if slice_state is None else slice_state.to_view()


def _get_mosaic_view(
    state: OCTProjectState,
    *,
    slice_id: int,
    mosaic_id: int,
) -> OCTMosaicStateView | None:
    mosaic_state = state.get_mosaic(slice_id, mosaic_id)
    return None if mosaic_state is None else mosaic_state.to_view()


def _get_batch_view(
    state: OCTProjectState,
    *,
    slice_id: int,
    mosaic_id: int,
    batch_id: int,
) -> OCTBatchStateView | None:
    batch_state = state.get_batch(slice_id, mosaic_id, batch_id)
    return None if batch_state is None else batch_state.to_view()


# ------------------------------------------------------------------------------
# OCT-specific ProjectStateStore wiring
# ------------------------------------------------------------------------------


def _make_oct_repository() -> PostgresProjectStateRepository[OCTProjectState]:
    return PostgresProjectStateRepository(
        block_name=STATE_DB_BLOCK_NAME,
        model_cls=OCTProjectState,
        project_type=OCT_PROJECT_TYPE,
        table_name="project_state",
    )


def _make_oct_lock() -> PrefectProjectLock:
    return PrefectProjectLock(_state_lock_name)


OCTProjectStateStore = BaseProjectStateStore[OCTProjectState]


def make_oct_store() -> OCTProjectStateStore:
    return OCTProjectStateStore(
        repository=_make_oct_repository(),
        lock=_make_oct_lock(),
    )


class OCTProjectStateService:
    """
    OCT-specific state service exposing open/read/peek APIs.
    """

    def __init__(self, store: OCTProjectStateStore | None = None) -> None:
        self._store = store or make_oct_store()

    # ------------------------------------------------------------------
    # Mutable scoped access (open_*)
    # ------------------------------------------------------------------

    def open_project(
        self,
        project_ident: OCTProjectId,
        *,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTProjectState]:
        return self.open_project_by_parts(
            project_name=project_ident.project_name,
            timeout_seconds=timeout_seconds,
        )

    def open_project_by_parts(
        self,
        project_name: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTProjectState]:
        return self._store.open(
            project_name,
            getter=lambda state: state,
            timeout_seconds=timeout_seconds,
        )

    def open_slice(
        self,
        slice_ident: OCTSliceId,
        *,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTSliceState]:
        return self.open_slice_by_parts(
            project_name=slice_ident.project_name,
            slice_id=slice_ident.slice_id,
            timeout_seconds=timeout_seconds,
        )

    def open_slice_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTSliceState]:
        return self._store.open(
            project_name,
            getter=lambda state: state.get_or_create_slice(slice_id),
            timeout_seconds=timeout_seconds,
        )

    def open_mosaic(
        self,
        mosaic_ident: OCTMosaicId,
        *,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTMosaicState]:
        return self.open_mosaic_by_parts(
            project_name=mosaic_ident.project_name,
            slice_id=mosaic_ident.slice_id,
            mosaic_id=mosaic_ident.mosaic_id,
            timeout_seconds=timeout_seconds,
        )

    def open_mosaic_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int | None = None,
        mosaic_id: int,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTMosaicState]:
        resolved_slice_id = (
            _derive_slice_id_from_mosaic_id(mosaic_id) if slice_id is None else slice_id
        )
        return self._store.open(
            project_name,
            getter=lambda state: state.get_or_create_mosaic(
                resolved_slice_id,
                mosaic_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    def open_batch(
        self,
        batch_ident: OCTBatchId,
        *,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTBatchState]:
        return self.open_batch_by_parts(
            project_name=batch_ident.project_name,
            slice_id=batch_ident.slice_id,
            mosaic_id=batch_ident.mosaic_id,
            batch_id=batch_ident.batch_id,
            timeout_seconds=timeout_seconds,
        )

    def open_batch_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int | None = None,
        mosaic_id: int,
        batch_id: int,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[OCTBatchState]:
        resolved_slice_id = (
            _derive_slice_id_from_mosaic_id(mosaic_id) if slice_id is None else slice_id
        )
        return self._store.open(
            project_name,
            getter=lambda state: state.get_or_create_batch(
                slice_id=resolved_slice_id,
                mosaic_id=mosaic_id,
                batch_id=batch_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    # ------------------------------------------------------------------
    # Locked readonly access (read_*)
    # ------------------------------------------------------------------

    def read_project(
        self,
        project_ident: OCTProjectId,
        *,
        timeout_seconds: float | None = None,
    ) -> OCTProjectStateView:
        return self.read_project_by_parts(
            project_name=project_ident.project_name,
            timeout_seconds=timeout_seconds,
        )

    def read_project_by_parts(
        self,
        project_name: str,
        *,
        timeout_seconds: float | None = None,
    ) -> OCTProjectStateView:
        return self._store.read(
            project_name,
            reader=lambda state: state.to_view(),
            timeout_seconds=timeout_seconds,
        )

    def read_slice(
        self,
        slice_ident: OCTSliceId,
        *,
        timeout_seconds: float | None = None,
    ) -> OCTSliceStateView | None:
        return self.read_slice_by_parts(
            project_name=slice_ident.project_name,
            slice_id=slice_ident.slice_id,
            timeout_seconds=timeout_seconds,
        )

    def read_slice_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int,
        timeout_seconds: float | None = None,
    ) -> OCTSliceStateView | None:
        return self._store.read(
            project_name,
            reader=lambda state: _get_slice_view(
                state,
                slice_id=slice_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    def read_mosaic(
        self,
        mosaic_ident: OCTMosaicId,
        *,
        timeout_seconds: float | None = None,
    ) -> OCTMosaicStateView | None:
        return self.read_mosaic_by_parts(
            project_name=mosaic_ident.project_name,
            slice_id=mosaic_ident.slice_id,
            mosaic_id=mosaic_ident.mosaic_id,
            timeout_seconds=timeout_seconds,
        )

    def read_mosaic_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int | None = None,
        mosaic_id: int,
        timeout_seconds: float | None = None,
    ) -> OCTMosaicStateView | None:
        resolved_slice_id = (
            _derive_slice_id_from_mosaic_id(mosaic_id) if slice_id is None else slice_id
        )
        return self._store.read(
            project_name,
            reader=lambda state: _get_mosaic_view(
                state,
                slice_id=resolved_slice_id,
                mosaic_id=mosaic_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    def read_batch(
        self,
        batch_ident: OCTBatchId,
        *,
        timeout_seconds: float | None = None,
    ) -> OCTBatchStateView | None:
        return self.read_batch_by_parts(
            project_name=batch_ident.project_name,
            slice_id=batch_ident.slice_id,
            mosaic_id=batch_ident.mosaic_id,
            batch_id=batch_ident.batch_id,
            timeout_seconds=timeout_seconds,
        )

    def read_batch_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int | None = None,
        mosaic_id: int,
        batch_id: int,
        timeout_seconds: float | None = None,
    ) -> OCTBatchStateView | None:
        resolved_slice_id = (
            _derive_slice_id_from_mosaic_id(mosaic_id) if slice_id is None else slice_id
        )
        return self._store.read(
            project_name,
            reader=lambda state: _get_batch_view(
                state,
                slice_id=resolved_slice_id,
                mosaic_id=mosaic_id,
                batch_id=batch_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    # ------------------------------------------------------------------
    # Unlocked readonly access (peek_*)
    # ------------------------------------------------------------------

    def peek_project(self, project_ident: OCTProjectId) -> OCTProjectStateView:
        return self.peek_project_by_parts(project_name=project_ident.project_name)

    def peek_project_by_parts(
        self,
        project_name: str,
    ) -> OCTProjectStateView:
        return self._store.peek(
            project_name,
            reader=lambda state: state.to_view(),
        )

    def peek_slice(
        self,
        slice_ident: OCTSliceId,
    ) -> OCTSliceStateView | None:
        return self.peek_slice_by_parts(
            project_name=slice_ident.project_name,
            slice_id=slice_ident.slice_id,
        )

    def peek_slice_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int,
    ) -> OCTSliceStateView | None:
        return self._store.peek(
            project_name,
            reader=lambda state: _get_slice_view(
                state,
                slice_id=slice_id,
            ),
        )

    def peek_mosaic(
        self,
        mosaic_ident: OCTMosaicId,
    ) -> OCTMosaicStateView | None:
        return self.peek_mosaic_by_parts(
            project_name=mosaic_ident.project_name,
            slice_id=mosaic_ident.slice_id,
            mosaic_id=mosaic_ident.mosaic_id,
        )

    def peek_mosaic_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int | None = None,
        mosaic_id: int,
    ) -> OCTMosaicStateView | None:
        resolved_slice_id = (
            _derive_slice_id_from_mosaic_id(mosaic_id) if slice_id is None else slice_id
        )
        return self._store.peek(
            project_name,
            reader=lambda state: _get_mosaic_view(
                state,
                slice_id=resolved_slice_id,
                mosaic_id=mosaic_id,
            ),
        )

    def peek_batch(
        self,
        batch_ident: OCTBatchId,
    ) -> OCTBatchStateView | None:
        return self.peek_batch_by_parts(
            project_name=batch_ident.project_name,
            slice_id=batch_ident.slice_id,
            mosaic_id=batch_ident.mosaic_id,
            batch_id=batch_ident.batch_id,
        )

    def peek_batch_by_parts(
        self,
        project_name: str,
        *,
        slice_id: int | None = None,
        mosaic_id: int,
        batch_id: int,
    ) -> OCTBatchStateView | None:
        resolved_slice_id = (
            _derive_slice_id_from_mosaic_id(mosaic_id) if slice_id is None else slice_id
        )
        return self._store.peek(
            project_name,
            reader=lambda state: _get_batch_view(
                state,
                slice_id=resolved_slice_id,
                mosaic_id=mosaic_id,
                batch_id=batch_id,
            ),
        )


OCT_STATE_SERVICE = OCTProjectStateService(make_oct_store())
