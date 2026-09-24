"""Continuous image numbering across mosaics and slices (`ops oct watch`)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from opticstream.cli.oct.watch import OCTWatcherService, build_watch_previews
from opticstream.config.enface_preview import EnfacePreviewConfig
from opticstream.config.psoct_scan_config import PSOCTAcquisitionParams
from opticstream.utils.acquisition_sequence import AcquisitionSequence

BIG = 100 * 1024  # the watcher ignores files smaller than 100 KB


def config(root="/tmp", normal=3, tilted=2, rows=2, pattern="spectral_{image}.nii"):
    return SimpleNamespace(
        project_base_path=Path(root) / "out",
        mosaics_per_slice=2,
        acquisition=PSOCTAcquisitionParams(
            grid_size_x_normal=normal, grid_size_x_tilted=tilted, grid_size_y=rows,
            tile_overlap=0, filename_pattern=pattern,
            acquisition_mosaic_map={"normal0deg": 1, "tilted15deg": 2}),
        enface_preview=EnfacePreviewConfig(**{
            f"{m}_pattern": f"img_{{image}}_{m}.nii" for m in ("aip", "mip", "ori", "ret")}),
    )


def write(folder, names):
    for name in names:
        with open(Path(folder) / name, "wb") as f:
            f.truncate(BIG)


class SequenceTests(unittest.TestCase):
    def test_sizes_follow_each_mosaic_grid(self):
        # 22x16 normal and tilted, as in sub-TestBscans.
        seq = AcquisitionSequence(config(normal=22, tilted=22, rows=16))
        self.assertEqual(seq.sizes, {1: 352, 2: 352})
        where = lambda i: (lambda p: (p.slice_id, p.mosaic_slot, p.tile))(seq.locate(i))
        self.assertEqual(where(1), (1, 1, 1))
        self.assertEqual(where(352), (1, 1, 352))
        self.assertEqual(where(353), (1, 2, 1))
        self.assertEqual(where(704), (1, 2, 352))
        self.assertEqual(where(705), (2, 1, 1))
        self.assertEqual(where(1409), (3, 1, 1))

    def test_unequal_grids_and_start_position(self):
        seq = AcquisitionSequence(config(normal=3, tilted=2, rows=2), start_slice=5, start_mosaic=2)
        self.assertEqual(seq.sizes, {1: 6, 2: 4})
        pos = [seq.locate(i) for i in (1, 4, 5, 10, 11, 15)]
        self.assertEqual([(p.slice_id, p.mosaic_slot, p.tile) for p in pos],
                         [(5, 2, 1), (5, 2, 4), (6, 1, 1), (6, 1, 6), (6, 2, 1), (7, 1, 1)])
        self.assertEqual(pos[0].source_mosaic_id, 10)
        for slice_id, slot in [(5, 2), (6, 1), (6, 2), (7, 1)]:
            self.assertEqual(seq.locate(seq.first_image(slice_id, slot)).tile, 1)
        self.assertIsNone(seq.first_image(5, 1))

    def test_invalid_start(self):
        for kwargs in ({"start_slice": 0}, {"start_mosaic": 3}, {"start_mosaic": 0}):
            with self.assertRaises(ValueError):
                AcquisitionSequence(config(), **kwargs)


class WatcherSequenceTests(unittest.TestCase):
    def watcher(self, folder, cfg, **kwargs):
        return OCTWatcherService(project_name="p", folder_path=Path(folder),
            project_base_path=folder, mosaic_ranges=[(1, 999)], slice_offset=0,
            batch_size=cfg.acquisition.grid_size_y, scan_config=cfg, direct=True,
            force_resend=False, **kwargs)

    def batches(self, watcher):
        return sorted((c.source_mosaic_id, c.logical_batch, tuple(p.name for p in c.files))
                      for c in watcher.discover_candidates())

    def test_accumulating_files_move_to_next_mosaic_and_slice(self):
        cfg = config(normal=3, tilted=2, rows=2)  # mosaic 1: 6 tiles, mosaic 2: 4 tiles
        with TemporaryDirectory() as folder:
            write(folder, [f"spectral_{i:04d}.nii" for i in range(1, 13)])
            found = self.batches(self.watcher(folder, cfg, sequence=AcquisitionSequence(cfg)))
        self.assertEqual([(m, b) for m, b, _ in found],
                         [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (3, 1)])
        self.assertEqual(found[3][2], ("spectral_0007.nii", "spectral_0008.nii"))
        self.assertEqual(found[5][2], ("spectral_0011.nii", "spectral_0012.nii"))

    def test_start_mosaic_two_of_slice_three(self):
        cfg = config(normal=3, tilted=2, rows=2)
        with TemporaryDirectory() as folder:
            write(folder, [f"spectral_{i:04d}.nii" for i in range(1, 7)])
            seq = AcquisitionSequence(cfg, start_slice=3, start_mosaic=2)
            found = self.batches(self.watcher(folder, cfg, sequence=seq))
        # slice 3 tilted = mosaic 6 (4 tiles), then slice 4 normal = mosaic 7.
        self.assertEqual([(m, b) for m, b, _ in found], [(6, 1), (6, 2), (7, 1)])

    def test_fixed_mosaic_ignores_batches_beyond_grid(self):
        cfg = config(normal=3, tilted=2, rows=2)
        with TemporaryDirectory() as folder:
            write(folder, [f"spectral_{i:04d}.nii" for i in range(1, 11)])
            found = self.batches(self.watcher(folder, cfg, fixed_slice_id=1, fixed_mosaic_slot=1))
        self.assertEqual([(m, b) for m, b, _ in found], [(1, 1), (1, 2), (1, 3)])


class SequencePreviewTests(unittest.TestCase):
    @patch("opticstream.cli.oct.watch_enface.slack_notifications_enabled", return_value=False)
    def test_previews_follow_sequence(self, slack):
        with TemporaryDirectory() as folder:
            cfg = config(folder, normal=3, tilted=2, rows=2)
            seq = AcquisitionSequence(cfg)
            watcher = build_watch_previews(cfg, project_name="p", folder_path=Path(folder),
                fixed_slice_id=None, fixed_mosaic_slot=None, acquisition=None, slice_offset=0,
                stability_seconds=0, poll_interval=1, sequence=seq)
            worker = watcher.discover_candidates.__self__
            maps = lambda images: [f"img_{i}_{m}.nii" for i in images
                                   for m in ("aip", "mip", "ori", "ret")]
            write(folder, maps(range(1, 9)))  # mosaic 1 complete + mosaic 2 batch 1
            found = list(worker.discover())
            self.assertEqual(found, [(1, None), (1, 1), (1, 2), (1, 3), (2, 1)])
            mosaic2 = worker.workers[2]
            self.assertEqual((mosaic2.ident.mosaic_id, mosaic2.acquisition, mosaic2.image_offset),
                             (2, "tilted15deg", 6))
            self.assertEqual([p.name for p in mosaic2.files(1)["aip"].values()],
                             ["img_7_aip.nii", "img_8_aip.nii"])

            # Mosaic 1 finished (previews exist); it is dropped once mosaic 2 has started.
            with patch.object(worker.workers[1], "discover", return_value=iter(())):
                self.assertEqual(list(worker.discover()), [(2, 1)])
            self.assertIn(1, worker.finished)


if __name__ == "__main__":
    unittest.main()
