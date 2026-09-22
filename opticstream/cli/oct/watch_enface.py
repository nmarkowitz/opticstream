"""Watch externally acquired enface maps without running MATLAB."""
from pathlib import Path

from opticstream.cli.oct.cli import oct_cli
from opticstream.cli.oct.watch import _configure_logging
from opticstream.config.psoct_scan_config import get_psoct_scan_config
from opticstream.flows.psoct.acquisition_preview_flow import (
    MODALITIES, expected_tiles, fingerprint, preview_dir, read_progress,
    preview_enface_batch, preview_enface_acquisition,
)
from opticstream.flows.psoct.utils import mosaic_context_from_ident
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.utils.polling_watcher import PollingStableWatcher
from opticstream.utils.slack_settings import slack_notifications_enabled


class EnfacePreviewWatcher:
    def __init__(self, config, mosaic_ident, folder, acquisition):
        self.config = config
        self.ident = mosaic_ident
        self.folder = Path(folder)
        self.acquisition = acquisition
        context = mosaic_context_from_ident(mosaic_ident, config)
        self.candidates = list(range(1, context.grid_size_x(config) + 1)) if config.enface_preview.batch_enabled else []
        if config.enface_preview.acquisition_enabled:
            self.candidates.append(None)

    def files(self, batch):
        return expected_tiles(self.config, self.ident, self.folder, self.acquisition, batch)

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
            directory = preview_dir(self.config, self.ident, batch)
            progress = read_progress(directory, signature)
            stitched = all(m in progress["stitched"] and (directory / f"{m}.jpg").exists()
                           and (directory / f"{m}.nii").exists() for m in MODALITIES)
            uploaded = all(m in progress["uploaded"] for m in MODALITIES)
            if not stitched or (slack_enabled and not uploaded):
                yield batch

    def process(self, batch):
        kwargs = dict(config=self.config, mosaic_ident=self.ident,
                      input_dir=self.folder, acquisition=self.acquisition)
        if batch is None:
            preview_enface_acquisition(**kwargs)
        else:
            preview_enface_batch(**kwargs, batch_id=batch)
        return 1


@oct_cli.command
def watch_enface(project_name: str, folder_path: Path, *, slice: int, mosaic: int,
                 acquisition: str, stability_seconds: int = 15,
                 poll_interval: int = 5, verbose: bool = False):
    """Watch one acquisition for complete batches and a complete full mosaic.

    slice/mosaic identify the acquisition explicitly, allowing filenames such as
    aip_0001.nii without embedded IDs. acquisition substitutes into file patterns.
    Use one watcher per acquisition. No MATLAB or spectral watcher is required.
    """
    _configure_logging(verbose)
    if slice < 1 or mosaic < 1:
        raise ValueError("slice and mosaic must be positive")
    if not folder_path.is_dir():
        raise ValueError(f"Input directory does not exist: {folder_path}")
    config = get_psoct_scan_config(project_name)
    if not config.enface_preview.batch_enabled and not config.enface_preview.acquisition_enabled:
        raise ValueError("Both enface preview flows are disabled in this block")
    ident = OCTMosaicId(project_name=project_name, slice_id=slice, mosaic_id=mosaic)
    worker = EnfacePreviewWatcher(config, ident, folder_path.resolve(), acquisition)
    # Validate templates before entering the polling loop.
    worker.files(None)
    PollingStableWatcher(discover_candidates=worker.discover,
        candidate_key=lambda batch: batch, fingerprint=worker.fingerprint,
        process=worker.process, stability_seconds=stability_seconds,
        poll_interval=poll_interval, running_message="Watching acquisition enface maps").run()
