"""Continuous tile numbering across mosaics and slices.

When the scanner numbers files continuously (spectral_0001.nii, spectral_0002.nii,
...) without a slice or acquisition in the name, the image number alone says where
a tile belongs: the first ``grid_size_x * grid_size_y`` images fill the starting
mosaic, the next images fill the following mosaic (sized by its own grid), and
after the slice's last mosaic the sequence moves to mosaic 1 of the next slice.
"""
from __future__ import annotations

from dataclasses import dataclass

from opticstream.flows.psoct.utils import mosaic_context_from_ids


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

    ``start_slice``/``start_mosaic`` place image 1: e.g. start_slice=3,
    start_mosaic=2 means the folder starts with the tilted mosaic of slice 3.
    ``start_mosaic`` is the mosaic's position within its slice.
    """

    def __init__(self, scan_config, start_slice: int = 1, start_mosaic: int = 1) -> None:
        self.mosaics_per_slice = scan_config.mosaics_per_slice
        if start_slice < 1:
            raise ValueError(f"start slice must be >= 1, got {start_slice}")
        if not 1 <= start_mosaic <= self.mosaics_per_slice:
            raise ValueError(
                f"start mosaic must be between 1 and {self.mosaics_per_slice} "
                f"(its position within the slice), got {start_mosaic}"
            )
        self.start_slice = start_slice
        self.start_mosaic = start_mosaic
        rows = scan_config.acquisition.grid_size_y
        self.sizes = {}
        for slot in range(1, self.mosaics_per_slice + 1):
            context = mosaic_context_from_ids(
                slice_id=1, mosaic_id=slot, mosaics_per_slice=self.mosaics_per_slice
            )
            self.sizes[slot] = context.grid_size_x(scan_config) * rows
        self.slice_total = sum(self.sizes.values())

    def locate(self, image: int) -> SequencePosition:
        if image < 1:
            raise ValueError(f"image number must be >= 1, got {image}")
        remaining = image - 1
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
        return before + 1

    def _position(self, slice_id: int, slot: int, tile: int) -> SequencePosition:
        return SequencePosition(slice_id, slot, tile, self.mosaics_per_slice)
