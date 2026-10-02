"""Continuous tile numbering across mosaics and slices.

When the scanner numbers files continuously (spectral_0001.nii, spectral_0002.nii,
...) without a slice or acquisition in the name, the image number alone says where
a tile belongs: the first ``grid_size_x * grid_size_y`` images fill the starting
mosaic, the next images fill the following mosaic (sized by its own grid), and
after the slice's last mosaic the sequence moves to mosaic 1 of the next slice.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from opticstream.flows.psoct.utils import (
    logical_mosaic_from_source_mosaic,
    mosaic_context_from_ids,
    mosaic_position_in_slice,
    slice_from_mosaic,
)
from opticstream.state.oct_project_state import OCTSequenceAnchor


@dataclass(frozen=True)
class SequencePosition:
    slice_id: int
    mosaic_slot: int
    tile: int
    mosaics_per_slice: int

    @property
    def source_mosaic_id(self) -> int:
        return (self.slice_id - 1) * self.mosaics_per_slice + self.mosaic_slot


class AcquisitionSequence:
    """Map continuous image numbers (1-based) to slice/mosaic/tile.

    ``start_slice``/``start_mosaic`` place ``start_image`` (default 1): e.g.
    start_slice=3, start_mosaic=2 means that image is tile 1 of the tilted mosaic
    of slice 3. ``start_mosaic`` is the mosaic's position within its slice. Images
    numbered below ``start_image`` belong to an earlier run and are not placed.
    """

    def __init__(
        self,
        scan_config,
        start_slice: int = 1,
        start_mosaic: int = 1,
        start_image: int = 1,
    ) -> None:
        self.mosaics_per_slice = scan_config.mosaics_per_slice
        if start_slice < 1:
            raise ValueError(f"start slice must be >= 1, got {start_slice}")
        if not 1 <= start_mosaic <= self.mosaics_per_slice:
            raise ValueError(
                f"start mosaic must be between 1 and {self.mosaics_per_slice} "
                f"(its position within the slice), got {start_mosaic}"
            )
        if start_image < 1:
            raise ValueError(f"start image must be >= 1, got {start_image}")
        self.start_slice = start_slice
        self.start_mosaic = start_mosaic
        self.start_image = start_image
        self.sizes = {}
        for slot in range(1, self.mosaics_per_slice + 1):
            context = mosaic_context_from_ids(
                slice_id=1, mosaic_id=slot, mosaics_per_slice=self.mosaics_per_slice
            )
            self.sizes[slot] = context.grid_size_x(scan_config) * context.grid_size_y(scan_config)
        self.slice_total = sum(self.sizes.values())

    def locate(self, image: int) -> SequencePosition | None:
        """Position of ``image``, or None if it precedes ``start_image``."""
        if image < 1:
            raise ValueError(f"image number must be >= 1, got {image}")
        if image < self.start_image:
            return None
        remaining = image - self.start_image
        for slot in range(self.start_mosaic, self.mosaics_per_slice + 1):
            if remaining < self.sizes[slot]:
                return self._position(self.start_slice, slot, remaining + 1)
            remaining -= self.sizes[slot]
        full_slices, remaining = divmod(remaining, self.slice_total)
        slice_id = self.start_slice + 1 + full_slices
        for slot in range(1, self.mosaics_per_slice + 1):
            if remaining < self.sizes[slot]:
                return self._position(slice_id, slot, remaining + 1)
            remaining -= self.sizes[slot]
        raise AssertionError("unreachable")

    def first_image(self, slice_id: int, mosaic_slot: int) -> int | None:
        """Image number of tile 1 of a mosaic, or None if it precedes the start."""
        if (slice_id, mosaic_slot) < (self.start_slice, self.start_mosaic):
            return None
        if slice_id == self.start_slice:
            before = sum(self.sizes[s] for s in range(self.start_mosaic, mosaic_slot))
        else:
            before = (
                sum(self.sizes[s] for s in range(self.start_mosaic, self.mosaics_per_slice + 1))
                + (slice_id - self.start_slice - 1) * self.slice_total
                + sum(self.sizes[s] for s in range(1, mosaic_slot))
            )
        return before + self.start_image

    def _position(self, slice_id: int, slot: int, tile: int) -> SequencePosition:
        return SequencePosition(slice_id, slot, tile, self.mosaics_per_slice)


def start_sequence(
    project,
    scan_config,
    folder: str,
    images: Collection[int],
    *,
    slice_id: int | None = None,
    mosaic_slot: int | None = None,
    start_image: int | None = None,
    slice_offset: int = 0,
) -> tuple[AcquisitionSequence, list[str]]:
    """
    Decide where continuous numbering starts, using the anchor saved in project state.

    ``project`` is the mutable OCT project state and is updated in place; ``images``
    are the image numbers currently in ``folder``. Returns the sequence and log notes.

    - No ``slice_id``/``mosaic_slot``: resume the saved anchor for this folder. A new
      folder continues with the mosaic after the last one recorded in project state,
      starting at its lowest image number (1 if empty).
    - ``slice_id``/``mosaic_slot`` (each defaults to 1): reset. That mosaic and every
      later one are cleared from project state and redone; numbering restarts there.
      ``start_image`` defaults to the image after the highest one in the folder when
      the folder is the one already in use (the scanner kept counting), otherwise to
      its lowest image (1 if empty). Repeating the saved slice/mosaic in the same
      folder without ``start_image`` resumes instead, so rerunning a command does
      not clear state twice.
    """
    mps = scan_config.mosaics_per_slice
    anchor = project.sequence_anchor
    same_folder = anchor is not None and anchor.folder == folder
    last_mosaic = project.last_mosaic_id()
    notes: list[str] = []

    def describe(mosaic_id: int) -> str:
        return (f"slice {slice_from_mosaic(mosaic_id, mps)} "
                f"mosaic {mosaic_position_in_slice(mosaic_id, mps)}")

    if slice_id is None and mosaic_slot is None:
        if start_image is not None:
            raise ValueError(
                "--start-image needs --slice/--mosaic: it is the image number of "
                "that mosaic's first tile."
            )
        if same_folder:
            notes.append("Resuming saved sequence start for this folder.")
        elif anchor is None:
            # Projects processed before anchors were saved placed image 1 at slice 1
            # mosaic 1; keep that for them.
            first = 1 if last_mosaic is not None else min(images, default=1)
            anchor = OCTSequenceAnchor(folder=folder, start_image=first, slice_id=1,
                                       mosaic_slot=1)
            notes.append("No saved sequence start; starting at slice 1 mosaic 1.")
        else:
            if last_mosaic is None:
                target = (anchor.slice_id, anchor.mosaic_slot)
                reason = "nothing has been processed yet"
            else:
                logical_slice = slice_from_mosaic(last_mosaic + 1, mps)
                target = (logical_slice - slice_offset,
                          mosaic_position_in_slice(last_mosaic + 1, mps))
                if target[0] < 1:
                    raise ValueError(
                        f"--slice-offset {slice_offset} puts the next mosaic before slice 1; "
                        "pass --slice/--mosaic."
                    )
                reason = f"the last mosaic in project state is {describe(last_mosaic)}"
            anchor = OCTSequenceAnchor(folder=folder, start_image=min(images, default=1),
                                       slice_id=target[0], mosaic_slot=target[1])
            notes.append(f"New folder; continuing at slice {target[0]} mosaic {target[1]} "
                         f"({reason}).")
    else:
        target = (slice_id or 1, mosaic_slot or 1)
        if (start_image is None and same_folder
                and (anchor.slice_id, anchor.mosaic_slot) == target):
            notes.append(
                f"This folder already restarted at slice {target[0]} mosaic {target[1]}; "
                "resuming without clearing state. Pass --start-image to redo it again."
            )
        else:
            if start_image is None:
                if same_folder:
                    start_image = max(images, default=0) + 1
                elif anchor is None and last_mosaic is not None and images:
                    raise ValueError(
                        "This project has state from before the watcher saved where "
                        "numbering starts, so it is unclear which files in this folder "
                        f"belong to slice {target[0]} mosaic {target[1]}. Pass "
                        "--start-image N, the image number of its first tile."
                    )
                else:
                    start_image = min(images, default=1)
            source_mosaic = (target[0] - 1) * mps + target[1]
            logical_slice, logical_mosaic = logical_mosaic_from_source_mosaic(
                source_mosaic, mosaics_per_slice=mps, slice_offset=slice_offset
            )
            removed = project.clear_from_mosaic(logical_slice, logical_mosaic)
            cleared = ", ".join(describe(mid) for _, mid in removed) or "nothing recorded yet"
            notes.append(f"Cleared project state from {describe(logical_mosaic)} onward: "
                         f"{cleared}.")
            anchor = OCTSequenceAnchor(folder=folder, start_image=start_image,
                                       slice_id=target[0], mosaic_slot=target[1])

    project.sequence_anchor = anchor
    notes.append(f"Image {anchor.start_image} is tile 1 of slice {anchor.slice_id} "
                 f"mosaic {anchor.mosaic_slot}; lower image numbers are ignored.")
    sequence = AcquisitionSequence(scan_config, anchor.slice_id, anchor.mosaic_slot,
                                   anchor.start_image)
    return sequence, notes
