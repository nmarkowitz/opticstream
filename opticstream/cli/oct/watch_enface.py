"""Watch externally acquired enface maps without running MATLAB."""
from pathlib import Path

from opticstream.cli.oct.cli import oct_cli
from opticstream.cli.oct.watch import _configure_logging
from opticstream.config.psoct_scan_config import get_psoct_scan_config
from opticstream.flows.psoct.acquisition_preview_flow import (
    enface_files, expected_tiles, fingerprint, list_names, naming_error,
    preview_path, progress_path, read_progress, preview_enface_batch,
    preview_enface_acquisition,
)
from opticstream.flows.psoct.utils import (
    logical_mosaic_from_source_mosaic,
    mosaic_context_from_ident,
)
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.utils.polling_watcher import PollingStableWatcher
from opticstream.utils.slack_settings import slack_notifications_enabled


class EnfacePreviewWatcher:
    def __init__(self, config, mosaic_ident, folder, acquisition, image_offset=0, *,
                 acquisition_previews=True, batch_previews=False):
        self.config = config
        self.ident = mosaic_ident
        self.folder = Path(folder)
        self.acquisition = acquisition
        # Continuous numbering: tile 1 of this mosaic is image 1 + image_offset.
        self.image_offset = image_offset
        # Optional folder listing shared across mosaics for one polling pass.
        self.names = None
        context = mosaic_context_from_ident(mosaic_ident, config)
        # The full acquisition goes first: once its last batch is stable it is
        # stitched before any batch previews still waiting in the same pass.
        self.candidates = [None] if acquisition_previews else []
        if batch_previews:
            self.candidates += list(range(1, context.grid_size_x(config) + 1))

    def files(self, batch):
        return expected_tiles(self.config, self.ident, self.folder, self.acquisition, batch,
                              image_offset=self.image_offset, names=self.names)

    def fingerprint(self, batch):
        return fingerprint(self.config, self.files(batch))

    def discover(self):
        slack_enabled = slack_notifications_enabled()
        for batch in self.candidates:
            files = self.files(batch)
            if not all(p.is_file() for paths in files.values() for p in paths.values()):
                continue
            try:
                signature = fingerprint(self.config, files)
            except (OSError, ValueError):
                continue
            progress = read_progress(progress_path(self.config, self.ident, batch), signature)
            stitched = all(m in progress["stitched"]
                           and preview_path(self.config, self.ident, batch, f"{m}.jpg").exists()
                           for m in files)
            uploaded = all(m in progress["uploaded"] for m in files)
            if not stitched or (slack_enabled and not uploaded):
                yield batch

    def process(self, batch):
        kwargs = dict(config=self.config, mosaic_ident=self.ident,
                      input_dir=self.folder, acquisition=self.acquisition,
                      image_offset=self.image_offset)
        if batch is None:
            preview_enface_acquisition(**kwargs)
        else:
            preview_enface_batch(**kwargs, batch_id=batch)
        return 1


class SequencePreviewWatcher:
    """Previews for continuously numbered acquisitions (see AcquisitionSequence).

    Follows the sequence from its start mosaic up to the mosaic holding the newest
    image. A mosaic with nothing left to do is dropped once a later mosaic has
    started, so long runs do not keep rescanning finished mosaics.
    """

    def __init__(self, config, sequence, folder, project_name, *, slice_offset=0,
                 acquisition_previews=True, batch_previews=False):
        self.config = config
        self.sequence = sequence
        self.folder = Path(folder)
        self.project_name = project_name
        self.slice_offset = slice_offset
        self.previews = dict(acquisition_previews=acquisition_previews,
                             batch_previews=batch_previews)
        self.labels = {slot: label for label, slot in reversed(
            list(config.acquisition.acquisition_mosaic_map.items()))}
        self.workers = {}
        self.finished = set()

    def _worker(self, position):
        key = position.source_mosaic_id
        if key not in self.workers:
            slice_id, mosaic_id = logical_mosaic_from_source_mosaic(
                key, mosaics_per_slice=self.sequence.mosaics_per_slice,
                slice_offset=self.slice_offset)
            ident = OCTMosaicId(project_name=self.project_name, slice_id=slice_id,
                                mosaic_id=mosaic_id)
            offset = self.sequence.first_image(position.slice_id, position.mosaic_slot) - 1
            label = self.labels.get(position.mosaic_slot, str(position.mosaic_slot))
            self.workers[key] = EnfacePreviewWatcher(self.config, ident, self.folder, label,
                                                     image_offset=offset, **self.previews)
        return self.workers[key]

    def _positions(self, last_image):
        """Start of each mosaic from the sequence start through ``last_image``."""
        image = 1
        while image <= last_image:
            position = self.sequence.locate(image)
            yield position
            image += self.sequence.sizes[position.mosaic_slot] - position.tile + 1

    def discover(self):
        names = list_names(self.folder)
        present = {image for _, image in enface_files(self.config, names)}
        if not present:
            return
        last_image = max(present)
        positions = list(self._positions(last_image))
        for number, position in enumerate(positions):
            key = position.source_mosaic_id
            if key in self.finished:
                continue
            worker = self._worker(position)
            worker.names = names
            pending = [(key, batch) for batch in worker.discover()]
            yield from pending
            if not pending and number < len(positions) - 1:
                self.finished.add(key)

    def fingerprint(self, candidate):
        key, batch = candidate
        return self.workers[key].fingerprint(batch)

    def process(self, candidate):
        key, batch = candidate
        return self.workers[key].process(batch)


def build_sequence_preview_watcher(config, sequence, folder, project_name, *,
                                   slice_offset=0, stability_seconds=15, poll_interval=5,
                                   acquisition_previews=True, batch_previews=False):
    """Polling watcher for previews of a continuously numbered acquisition folder."""
    worker = SequencePreviewWatcher(config, sequence, Path(folder).resolve(), project_name,
                                    slice_offset=slice_offset,
                                    acquisition_previews=acquisition_previews,
                                    batch_previews=batch_previews)
    return PollingStableWatcher(discover_candidates=worker.discover,
        candidate_key=lambda candidate: candidate, fingerprint=worker.fingerprint,
        process=worker.process, stability_seconds=stability_seconds,
        poll_interval=poll_interval, running_message="Watching acquisition enface maps")


def build_preview_watcher(config, mosaic_ident, folder, acquisition, *,
                          stability_seconds=15, poll_interval=5,
                          acquisition_previews=True, batch_previews=False):
    """Polling watcher that stitches previews for one acquisition folder."""
    worker = EnfacePreviewWatcher(config, mosaic_ident, Path(folder).resolve(), acquisition,
                                  acquisition_previews=acquisition_previews,
                                  batch_previews=batch_previews)
    # Validate the input naming before entering the polling loop.
    worker.files(None)
    return PollingStableWatcher(discover_candidates=worker.discover,
        candidate_key=lambda batch: batch, fingerprint=worker.fingerprint,
        process=worker.process, stability_seconds=stability_seconds,
        poll_interval=poll_interval, running_message="Watching acquisition enface maps")


@oct_cli.command
def watch_enface(project_name: str, folder_path: Path, *, slice: int, mosaic: int,
                 acquisition: str, stability_seconds: int = 15,
                 poll_interval: int = 5, previews: bool = True,
                 batch_previews: bool = False, verbose: bool = False):
    """Watch one acquisition for complete batches and a complete full mosaic.

    `ops oct watch` already runs these previews when given --slice/--acquisition
    or --mosaic; use this command only to preview without processing.

    Maps are found with acquisition.filename_pattern (which needs {modality}) and
    filename_modality_map labels for aip, mip, ori and ret. slice/mosaic identify
    the acquisition explicitly; acquisition is its filename label.
    --no-previews skips the full-acquisition preview; --batch-previews also
    previews each complete batch. Use one watcher per acquisition.
    No MATLAB or spectral watcher is required.
    """
    _configure_logging(verbose)
    if slice < 1 or mosaic < 1:
        raise ValueError("slice and mosaic must be positive")
    if not folder_path.is_dir():
        raise ValueError(f"Input directory does not exist: {folder_path}")
    if not previews and not batch_previews:
        raise ValueError("Nothing to preview: --no-previews without --batch-previews")
    config = get_psoct_scan_config(project_name)
    error = naming_error(config)
    if error:
        raise ValueError(f"Enface previews need the input naming to cover them: {error}")
    ident = OCTMosaicId(project_name=project_name, slice_id=slice, mosaic_id=mosaic)
    build_preview_watcher(config, ident, folder_path, acquisition,
                          stability_seconds=stability_seconds, poll_interval=poll_interval,
                          acquisition_previews=previews,
                          batch_previews=batch_previews).run()
