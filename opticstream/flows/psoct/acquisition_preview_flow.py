"""Batch and acquisition QC previews from externally produced enface tiles.

No processing milestones are mutated. Local manifests permit resumable previews.
Run only one preview watcher for a given project/slice/mosaic at a time.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import time

import nibabel as nib
import numpy as np
from prefect import flow, task
import yaml

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.data_processing.qc.convert_image import convert_image
from opticstream.flows.psoct.utils import mosaic_context_from_ident
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.tasks.slack_notification import upload_multiple_files_to_slack
from opticstream.utils.slack_settings import slack_notifications_enabled

MODALITIES = ("aip", "mip", "ori", "ret")


def expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id=None):
    """Return exact expected paths, never infer completeness from file counts."""
    context = mosaic_context_from_ident(mosaic_ident, config)
    rows = config.acquisition.grid_size_y
    columns = context.grid_size_x(config)
    if batch_id is not None and not 1 <= batch_id <= columns:
        raise ValueError(f"batch_id must be between 1 and {columns}")
    indices = range(columns * rows) if batch_id is None else range((batch_id - 1) * rows, batch_id * rows)
    root = Path(input_dir).resolve()
    result = {}
    for modality in MODALITIES:
        pattern = getattr(config.enface_preview, f"{modality}_pattern")
        paths = {}
        for index in indices:
            name = pattern.format(image=index + config.enface_preview.first_image,
                                  slice=mosaic_ident.slice_id, mosaic=mosaic_ident.mosaic_id,
                                  acquisition=acquisition, project=mosaic_ident.project_name)
            path = (root / name).resolve()
            if path.parent != root:
                raise ValueError("Rendered preview filename must remain inside the input directory")
            paths[index] = path
        result[modality] = paths
    all_paths = [p for paths in result.values() for p in paths.values()]
    if len(set(all_paths)) != len(all_paths):
        raise ValueError("Preview patterns must identify distinct modality/tile files")
    return result


def fingerprint(config, files):
    records = []
    for modality, paths in files.items():
        for index, path in paths.items():
            stat = path.stat()
            if not path.is_file() or stat.st_size == 0:
                raise ValueError(f"Empty or invalid tile: {path}")
            records.append((modality, index, str(path), stat.st_size, stat.st_mtime_ns))
    settings = (config.enface_preview.model_dump(mode="json"), config.acquisition.model_dump(mode="json"))
    return hashlib.sha256(json.dumps([records, settings], sort_keys=True).encode()).hexdigest()


def preview_dir(config, mosaic_ident, batch_id=None):
    scope = "acquisition" if batch_id is None else f"batch-{batch_id:04d}"
    return Path(config.project_base_path) / "acquisition-previews" / f"slice-{mosaic_ident.slice_id:03d}" / f"mosaic-{mosaic_ident.mosaic_id:03d}" / scope


def read_progress(directory, signature):
    try:
        data = json.loads((directory / "progress.json").read_text())
    except (FileNotFoundError, ValueError):
        return {"signature": signature, "stitched": [], "uploaded": []}
    if data.get("signature") != signature:
        return {"signature": signature, "stitched": [], "uploaded": []}
    return data


def save_progress(directory, progress):
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "progress.json.tmp"
    temporary.write_text(json.dumps(progress, indent=2))
    # Windows indexers/antivirus may briefly hold the replaced file open.
    for attempt in range(5):
        try:
            temporary.replace(directory / "progress.json")
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def tile_position(index, rows, shape, overlap, traversal, batch_id=None):
    column, row = divmod(index, rows)
    if traversal == "snake-by-columns" and column % 2:
        row = rows - 1 - row
    if batch_id is not None:
        column = 0
    return round(column * shape[0] * (1 - overlap)), round(row * shape[1] * (1 - overlap))


@task
def stitch_preview_modality(config: PSOCTScanConfigModel, files: dict[int, Path],
                            modality: str, output: Path, batch_id: int | None = None) -> Path:
    """Normalize singleton NIfTI maps to 2D then call linc-convert mosaic2d.

    Temporary files leave acquisition originals untouched and prevent linc-convert
    from silently skipping unreadable inputs. TIFF and other formats accepted by
    the existing QC loader can also be used through configurable suffixes.
    """
    from opticstream.data_processing.qc.convert_image import load_data
    from linc_convert.modalities.psoct.mosaic import mosaic2d

    overlap = config.acquisition.tile_overlap / 100.0
    if not 0 <= overlap < 1:
        raise ValueError("Preview tile_overlap must be a percentage in [0, 100)")
    output.parent.mkdir(parents=True, exist_ok=True)
    shape = None
    with tempfile.TemporaryDirectory(prefix="enface-preview-", dir=output.parent) as scratch:
        entries = []
        for index, path in files.items():
            # Treat only the final extension as a format indicator; acquisition
            # labels may contain dots (e.g. tilted12.5deg).
            if path.name.lower().endswith((".nii", ".nii.gz")):
                array = np.asarray(nib.load(path).dataobj)
            else:
                array = np.asarray(load_data(input_path=path, mat_variable=None))
            if array.ndim > 2 and all(size == 1 for size in array.shape[2:]):
                array = array.reshape(array.shape[:2])
            if array.ndim != 2:
                raise ValueError(f"Expected 2D enface map (or singleton trailing axes): {path}, {array.shape}")
            if shape is not None and array.shape != shape:
                raise ValueError(f"Tile dimensions differ: {path}, {array.shape} != {shape}")
            shape = array.shape
            array = array.astype(np.float32)
            if modality == "ori" and config.enface_preview.orientation_units == "radians":
                array = np.rad2deg(array)
            normalized = Path(scratch) / f"tile-{index}.nii"
            nib.save(nib.Nifti1Image(array, np.eye(4)), normalized)
            x, y = tile_position(index, config.acquisition.grid_size_y, shape, overlap,
                                 config.enface_preview.traversal, batch_id)
            entries.append({"filepath": str(normalized.resolve()), "x": x, "y": y})
        spec = Path(scratch) / "tiles.yaml"
        spec.write_text(yaml.safe_dump({"metadata": {"scan_resolution": config.acquisition.scan_resolution_3d[:2]}, "tiles": entries}))
        mosaic2d(tile_info_file=str(spec), nifti_output=str(output),
                 tile_overlap=overlap if overlap else 0, circular_mean=modality == "ori")
    if not output.is_file():
        raise RuntimeError(f"mosaic2d did not produce {output}")
    jpeg = output.with_suffix(".jpg")
    convert_image(input=output, output=jpeg, angle_to_rgb=modality == "ori", output_format="jpg")
    return jpeg


def run_preview(config, mosaic_ident, input_dir, acquisition, batch_id=None):
    enabled = config.enface_preview.acquisition_enabled if batch_id is None else config.enface_preview.batch_enabled
    if not enabled:
        return {}
    files = expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id)
    signature = fingerprint(config, files)
    directory = preview_dir(config, mosaic_ident, batch_id)
    progress = read_progress(directory, signature)
    for modality in MODALITIES:
        output = directory / f"{modality}.nii"
        if modality not in progress["stitched"] or not output.with_suffix(".jpg").exists() or not output.exists():
            stitch_preview_modality(config, files[modality], modality, output, batch_id)
            if fingerprint(config, files) != signature:
                raise RuntimeError("Acquisition files changed while stitching; wait for stability and retry")
            if modality not in progress["stitched"]:
                progress["stitched"].append(modality)
            save_progress(directory, progress)
    if slack_notifications_enabled():
        remaining = [m for m in MODALITIES if m not in progress["uploaded"]]
        if remaining:
            paths = [str(directory / f"{m}.jpg") for m in remaining]
            scope = "full acquisition" if batch_id is None else f"batch {batch_id}"
            label = f"{mosaic_ident.project_name}, slice {mosaic_ident.slice_id}, mosaic {mosaic_ident.mosaic_id}, {acquisition}, {scope}"
            results = upload_multiple_files_to_slack(filepaths=paths,
                titles=[f"{label} - {m.upper()}" for m in remaining], initial_comment=label)
            for modality, path in zip(remaining, paths):
                if results.get(path):
                    progress["uploaded"].append(modality)
            save_progress(directory, progress)
            if any(not results.get(path) for path in paths):
                raise RuntimeError("Some preview Slack uploads failed; successful uploads are checkpointed")
    return {m: str(directory / f"{m}.jpg") for m in MODALITIES}


@flow(name="preview-enface-batch")
def preview_enface_batch(config: PSOCTScanConfigModel, mosaic_ident: OCTMosaicId,
                          input_dir: Path, acquisition: str, batch_id: int):
    return run_preview(config, mosaic_ident, input_dir, acquisition, batch_id)


@flow(name="preview-enface-acquisition")
def preview_enface_acquisition(config: PSOCTScanConfigModel, mosaic_ident: OCTMosaicId,
                                input_dir: Path, acquisition: str):
    return run_preview(config, mosaic_ident, input_dir, acquisition)


def to_deployment(*, deployment_name="local", extra_tags=()):
    return [f.to_deployment(name=deployment_name, tags=["acquisition-preview", *extra_tags], concurrency_limit=1)
            for f in (preview_enface_batch, preview_enface_acquisition)]
