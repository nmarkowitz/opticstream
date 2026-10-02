"""Continuous image numbering across mosaics and slices (`ops oct watch`)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from opticstream.cli.oct.watch import OCTWatcherService, build_watch_previews
from opticstream.config.psoct_scan_config import PSOCTAcquisitionParams
from opticstream.flows.psoct.tile_file_reference import build_tile_file_reference_list
from opticstream.flows.psoct.utils import mosaic_context_from_ids
from opticstream.state.oct_project_state import OCTProjectState, OCTSequenceAnchor
from opticstream.utils.acquisition_sequence import AcquisitionSequence, start_sequence

BIG = 100 * 1024  # the watcher ignores files smaller than 100 KB


def config(root="/tmp", normal=3, tilted=2, rows=2, pattern="spectral_{image}.nii",
           rows_tilted=None):
    return SimpleNamespace(
        project_base_path=Path(root) / "out",
        mosaics_per_slice=2,
        acquisition=PSOCTAcquisitionParams(
            grid_size_x_normal=normal, grid_size_x_tilted=tilted, grid_size_y=rows,
            grid_size_y_tilted=rows_tilted,
            tile_overlap=0, filename_pattern=pattern,
            acquisition_mosaic_map={"normal0deg": 1, "tilted15deg": 2}),
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

    def test_tilted_tiles_per_batch(self):
        # 22 strips of 18 (normal) and 25 (tilted) tiles, as in sub-260928.
        seq = AcquisitionSequence(config(normal=22, tilted=22, rows=18, rows_tilted=25))
        self.assertEqual(seq.sizes, {1: 396, 2: 550})
        self.assertEqual(seq.first_image(2, 1), 947)

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
        for kwargs in ({"start_slice": 0}, {"start_mosaic": 3}, {"start_mosaic": 0},
                       {"start_image": 0}):
            with self.assertRaises(ValueError):
                AcquisitionSequence(config(), **kwargs)

    def test_start_image_shifts_numbering(self):
        # Scanner kept counting: image 21 is tile 1 of slice 5 mosaic 2.
        seq = AcquisitionSequence(config(normal=3, tilted=2, rows=2), start_slice=5,
                                  start_mosaic=2, start_image=21)
        self.assertIsNone(seq.locate(20))
        where = lambda i: (lambda p: (p.slice_id, p.mosaic_slot, p.tile))(seq.locate(i))
        self.assertEqual([where(i) for i in (21, 24, 25, 31)],
                         [(5, 2, 1), (5, 2, 4), (6, 1, 1), (6, 2, 1)])
        self.assertEqual(seq.first_image(6, 1), 25)


def project(*mosaics):
    """Project state with one batch in each (slice_id, mosaic_id)."""
    state = OCTProjectState()
    for slice_id, mosaic_id in mosaics:
        state.get_or_create_batch(slice_id, mosaic_id, 1)
    return state


class ProjectClearTests(unittest.TestCase):
    def test_clear_from_second_mosaic_keeps_first_and_resets_slice(self):
        state = project((4, 7), (4, 8), (5, 9), (5, 10), (6, 11))
        state.slices[5].set_registered(True)
        removed = state.clear_from_mosaic(5, 10)
        self.assertEqual(removed, [(5, 10), (6, 11)])
        self.assertEqual(sorted(state.slices), [4, 5])
        self.assertEqual(list(state.slices[5].mosaics), [9])
        self.assertFalse(state.slices[5].registered)
        self.assertEqual(sorted(state.slices[4].mosaics), [7, 8])

    def test_clear_from_first_mosaic_drops_slice(self):
        state = project((5, 9), (5, 10))
        self.assertEqual(state.clear_from_mosaic(5, 9), [(5, 9), (5, 10)])
        self.assertEqual(state.slices, {})
        self.assertEqual(state.last_mosaic_id(), None)


class StartSequenceTests(unittest.TestCase):
    cfg = config(normal=3, tilted=2, rows=2)  # mosaic 1: 6 tiles, mosaic 2: 4 tiles

    def start(self, state, images, folder="/scan/a", **kwargs):
        seq, _ = start_sequence(state, self.cfg, folder, set(images), **kwargs)
        anchor = state.sequence_anchor
        return seq, (anchor.folder, anchor.start_image, anchor.slice_id, anchor.mosaic_slot)

    def test_new_project_starts_at_slice_one(self):
        state = project()
        _, anchor = self.start(state, range(1, 5))
        self.assertEqual(anchor, ("/scan/a", 1, 1, 1))

    def test_resumes_saved_start_for_same_folder(self):
        state = project((1, 1))
        state.sequence_anchor = OCTSequenceAnchor(folder="/scan/a", start_image=7,
                                                  slice_id=3, mosaic_slot=2)
        seq, anchor = self.start(state, range(1, 30))
        self.assertEqual(anchor[1:], (7, 3, 2))
        self.assertEqual(list(state.slices), [1])  # nothing cleared

    def test_reset_in_same_folder_continues_after_highest_image(self):
        state = project((5, 9), (5, 10), (6, 11))
        state.sequence_anchor = OCTSequenceAnchor(folder="/scan/a", slice_id=1, mosaic_slot=1)
        seq, anchor = self.start(state, range(1, 21), slice_id=5, mosaic_slot=2)
        self.assertEqual(anchor, ("/scan/a", 21, 5, 2))
        self.assertEqual(list(state.slices[5].mosaics), [9])
        self.assertNotIn(6, state.slices)
        # Slice 5 mosaic 2 is redone (4 tiles), then the next mosaic is slice 6 mosaic 1.
        self.assertEqual((seq.locate(21).slice_id, seq.locate(21).mosaic_slot), (5, 2))
        self.assertEqual((seq.locate(25).slice_id, seq.locate(25).mosaic_slot), (6, 1))
        self.assertIsNone(seq.locate(20))

    def test_repeating_same_reset_resumes_instead_of_clearing(self):
        state = project((5, 10))
        state.sequence_anchor = OCTSequenceAnchor(folder="/scan/a", start_image=21,
                                                  slice_id=5, mosaic_slot=2)
        _, anchor = self.start(state, range(1, 26), slice_id=5, mosaic_slot=2)
        self.assertEqual(anchor[1:], (21, 5, 2))
        self.assertIn(10, state.slices[5].mosaics)
        # --start-image forces a fresh reset of the same mosaic.
        _, anchor = self.start(state, range(1, 26), slice_id=5, mosaic_slot=2, start_image=26)
        self.assertEqual(anchor[1:], (26, 5, 2))
        self.assertNotIn(5, state.slices)

    def test_reset_in_new_folder_starts_at_its_lowest_image(self):
        state = project((5, 10))
        state.sequence_anchor = OCTSequenceAnchor(folder="/scan/a", slice_id=1, mosaic_slot=1)
        _, anchor = self.start(state, [1, 2, 3], folder="/scan/b", slice_id=5, mosaic_slot=2)
        self.assertEqual(anchor, ("/scan/b", 1, 5, 2))
        _, anchor = self.start(state, [], folder="/scan/c", slice_id=7, mosaic_slot=1)
        self.assertEqual(anchor, ("/scan/c", 1, 7, 1))

    def test_new_folder_continues_after_last_mosaic_in_state(self):
        state = project((5, 9), (5, 10))
        state.sequence_anchor = OCTSequenceAnchor(folder="/scan/a", slice_id=1, mosaic_slot=1)
        _, anchor = self.start(state, [], folder="/scan/b")
        self.assertEqual(anchor, ("/scan/b", 1, 6, 1))
        state = project((5, 9))
        state.sequence_anchor = OCTSequenceAnchor(folder="/scan/a", slice_id=1, mosaic_slot=1)
        _, anchor = self.start(state, [], folder="/scan/b")
        self.assertEqual(anchor[2:], (5, 2))

    def test_unanchored_project_with_state_needs_start_image(self):
        state = project((1, 1))
        with self.assertRaises(ValueError):
            self.start(state, range(1, 10), slice_id=5, mosaic_slot=2)
        _, anchor = self.start(state, range(1, 10), slice_id=5, mosaic_slot=2, start_image=4)
        self.assertEqual(anchor[1:], (4, 5, 2))

    def test_start_image_requires_slice_or_mosaic(self):
        with self.assertRaises(ValueError):
            self.start(project(), [], start_image=5)


class TileNumberTests(unittest.TestCase):
    def test_continuous_names_numbered_within_mosaic(self):
        cfg = config(normal=22, tilted=22, rows=16)
        context = mosaic_context_from_ids(slice_id=1, mosaic_id=2, mosaics_per_slice=2)
        files = [Path(f"/scan/spectral_{i:04d}.nii") for i in range(369, 385)]
        refs = build_tile_file_reference_list(files, config=cfg, mosaic_context=context,
                                              batch_id=2)
        self.assertEqual(sorted(refs), list(range(17, 33)))
        self.assertEqual(refs[17].spectral_file_path.name, "spectral_0369.nii")


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

    def test_tilted_batches_use_grid_size_y_tilted(self):
        cfg = config(normal=2, tilted=2, rows=2, rows_tilted=3)  # mosaic 1: 4 tiles, mosaic 2: 6
        with TemporaryDirectory() as folder:
            write(folder, [f"spectral_{i:04d}.nii" for i in range(1, 11)])
            found = self.batches(self.watcher(folder, cfg, sequence=AcquisitionSequence(cfg)))
        self.assertEqual([(m, b, len(files)) for m, b, files in found],
                         [(1, 1, 2), (1, 2, 2), (2, 1, 3), (2, 2, 3)])
        self.assertEqual(found[3][2], ("spectral_0008.nii", "spectral_0009.nii", "spectral_0010.nii"))

    def test_files_before_start_image_are_ignored(self):
        cfg = config(normal=3, tilted=2, rows=2)
        with TemporaryDirectory() as folder:
            write(folder, [f"spectral_{i:04d}.nii" for i in range(1, 27)])
            seq = AcquisitionSequence(cfg, start_slice=5, start_mosaic=2, start_image=21)
            found = self.batches(self.watcher(folder, cfg, sequence=seq))
        # Images 21-24 redo slice 5 mosaic 2 (mosaic 10); 25-26 start slice 6 (mosaic 11).
        self.assertEqual([(m, b, files[0]) for m, b, files in found],
                         [(10, 1, "spectral_0021.nii"), (10, 2, "spectral_0023.nii"),
                          (11, 1, "spectral_0025.nii")])

    def test_fixed_mosaic_ignores_batches_beyond_grid(self):
        cfg = config(normal=3, tilted=2, rows=2)
        with TemporaryDirectory() as folder:
            write(folder, [f"spectral_{i:04d}.nii" for i in range(1, 11)])
            found = self.batches(self.watcher(folder, cfg, fixed_slice_id=1, fixed_mosaic_slot=1))
        self.assertEqual([(m, b) for m, b, _ in found], [(1, 1), (1, 2), (1, 3)])

    def test_enface_maps_stay_out_of_spectral_batches(self):
        cfg = config(normal=3, tilted=2, rows=2, pattern="img_{image}_{modality}.nii")
        with TemporaryDirectory() as folder:
            write(folder, [f"img_{i}_{m}.nii" for i in range(1, 5)
                           for m in ("spectral", "aip", "mip", "ori", "ret")])
            found = self.batches(self.watcher(folder, cfg, sequence=AcquisitionSequence(cfg)))
        self.assertEqual(found, [(1, 1, ("img_1_spectral.nii", "img_2_spectral.nii")),
                                 (1, 2, ("img_3_spectral.nii", "img_4_spectral.nii"))])

    def test_processed_input_keeps_enface_maps_in_batches(self):
        cfg = config(normal=3, tilted=2, rows=2, pattern="img_{image}_{modality}.nii")
        cfg.acquisition.tile_saving_type = "processed_with_spectral"
        with TemporaryDirectory() as folder:
            write(folder, [f"img_{i}_{m}.nii" for i in (1, 2) for m in ("spectral", "aip")])
            found = self.batches(self.watcher(folder, cfg, sequence=AcquisitionSequence(cfg)))
        self.assertEqual(len(found[0][2]), 4)


class SequencePreviewTests(unittest.TestCase):
    @patch("opticstream.cli.oct.watch_enface.slack_notifications_enabled", return_value=False)
    def test_previews_follow_sequence(self, slack):
        with TemporaryDirectory() as folder:
            cfg = config(folder, normal=3, tilted=2, rows=2, pattern="img_{image}_{modality}.nii")
            seq = AcquisitionSequence(cfg)
            watcher = build_watch_previews(cfg, project_name="p", folder_path=Path(folder),
                fixed_slice_id=None, fixed_mosaic_slot=None, acquisition=None, slice_offset=0,
                stability_seconds=0, poll_interval=1, sequence=seq, batch_previews=True)
            worker = watcher.discover_candidates.__self__
            maps = lambda images: [f"img_{i}_{m}.nii" for i in images
                                   for m in ("aip", "mip", "ori", "ret", "surf")]
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
