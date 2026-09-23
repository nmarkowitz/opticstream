import unittest

from opticstream.data_processing.stitch.coords import Grid, Tile


def _grid(stitched_step, rows=3, cols=4, step=280.0):
    tiles = []
    for r in range(rows):
        for c in range(cols):
            tiles.append(
                Tile(
                    path=f"mosaic_001_image_{r * cols + c + 1:04d}_aip.nii",
                    normal_coord=(c * step, r * step),
                    stitched_coord=(c * stitched_step, r * stitched_step),
                    avg_signal=100.0,
                )
            )
    return Grid.from_tiles(tiles)


class TestDriftFallback(unittest.TestCase):
    def test_collapsed_registration_falls_back_to_ideal_step(self):
        grid = _grid(stitched_step=0.0)
        grid.compute_registered_offset(signal_threshold=60)
        self.assertEqual(grid._tile_at(2, 3).derived_coord, (840.0, 560.0))

    def test_plausible_registration_is_kept(self):
        grid = _grid(stitched_step=270.0)
        grid.compute_registered_offset(signal_threshold=60)
        self.assertEqual(grid._tile_at(2, 3).derived_coord, (810.0, 540.0))

    def test_no_reliable_tiles_falls_back_to_ideal_step(self):
        grid = _grid(stitched_step=0.0)
        grid.compute_registered_offset(signal_threshold=1000)
        self.assertEqual(grid._tile_at(0, 1).derived_coord, (280.0, 0.0))


if __name__ == "__main__":
    unittest.main()
