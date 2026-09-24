"""Batch and acquisition QC previews from externally produced enface tiles.

No processing milestones are mutated. Local manifests permit resumable previews.
Run only one preview watcher for a given project/slice/mosaic at a time.
"""
import hashlib
import json
import os
from pathlib import Path
import re
from string import Formatter
import tempfile
import time

import nibabel as nib
import numpy as np
from prefect import flow, task
import yaml

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.data_processing.qc.convert_image import convert_image
from opticstream.flows.psoct.utils import get_slice_paths, mosaic_context_from_ident
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.tasks.slack_notification import upload_multiple_files_to_slack
from opticstream.utils.slack_settings import slack_notifications_enabled

MODALITIES = ("aip", "mip", "ori", "ret")


def _padded_image_matches(pattern, values, root):
    """Map image number -> filename for a pattern whose {image} has no format spec.

    Like filename_pattern, a bare {image} matches any zero padding (1, 01, 0001),
    compared as a number. Returns None when {image} has an explicit format spec
    (e.g. {image:04d}), which keeps exact rendering.
    """
    parts = []
    for literal, field, spec, conversion in Formatter().parse(pattern):
        parts.append(re.escape(literal))
        if field is None:
            continue
        if field == "image":
            if spec or conversion:
                return None
            parts.append(r"(?P<image>[0-9]+)")
        else:
            parts.append(re.escape(format(values[field], spec)))
    regex = re.compile("".join(parts))
    matches = {}
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        return matches
    for entry in entries:
        found = regex.fullmatch(entry.name)
        if found is None:
            continue
        number = int(found["image"])
        if number in matches:
            raise ValueError(f"Several files match image {number} of {pattern!r}: "
                             f"{matches[number]}, {entry.name}")
        matches[number] = entry.name
    return matches


def expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id=None):
    """Return exact expected paths, never infer completeness from file counts."""
    context = mosaic_context_from_ident(mosaic_ident, config)
    rows = config.acquisition.grid_size_y
    columns = context.grid_size_x(config)
    if batch_id is not None and not 1 <= batch_id <= columns:
        raise ValueError(f"batch_id must be between 1 and {columns}")
    indices = range(columns * rows) if batch_id is None else range((batch_id - 1) * rows, batch_id * rows)
    root = Path(input_dir).resolve()
    values = dict(slice=mosaic_ident.slice_id, mosaic=mosaic_ident.mosaic_id,
                  acquisition=acquisition, project=mosaic_ident.project_name)
    result = {}
    for modality in MODALITIES:
        pattern = getattr(config.enface_preview, f"{modality}_pattern")
        existing = _padded_image_matches(pattern, values, root)
        paths = {}
        for index in indices:
            image = index + config.enface_preview.first_image
            # Missing tiles keep the unpadded name so callers still see them as absent.
            name = (existing or {}).get(image) or pattern.format(image=image, **values)
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


def preview_dir(config, mosaic_ident):
    """``{project_base}/slice-NN/acquisition_previews``, shared by all mosaics of a slice."""
    slice_path, *_ = get_slice_paths(config.project_base_path, mosaic_ident.slice_id)
    return slice_path / "acquisition_previews"


def preview_path(config, mosaic_ident, batch_id, name):
    """Scope-prefixed file, e.g. ``mosaic_001_aip.nii`` or ``mosaic_001_batch_0003_aip.nii``."""
    prefix = f"mosaic_{mosaic_ident.mosaic_id:03d}"
    if batch_id is not None:
        prefix += f"_batch_{batch_id:04d}"
    return preview_dir(config, mosaic_ident) / f"{prefix}_{name}"


def progress_path(config, mosaic_ident, batch_id=None):
    return preview_path(config, mosaic_ident, batch_id, "progress.json")


def read_progress(path, signature):
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return {"signature": signature, "stitched": [], "uploaded": []}
    if data.get("signature") != signature:
        return {"signature": signature, "stitched": [], "uploaded": []}
    return data


def save_progress(path, progress):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(progress, indent=2))
    # Windows indexers/antivirus may briefly hold the replaced file open.
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def tile_position(index, rows, shape, overlap, traversal, batch_id=None, *, order=None, columns=None):
    """Map acquisition sequence to linc-convert grid coordinates.

    rows is the historical tiles-per-batch setting, not the physical Y extent
    in row-first mode. columns is the number of batches for the full acquisition.
    """
    row_based = traversal in {"row-by-row", "snake-by-rows"}
    order = order or ("right-down" if row_based else "down-right")
    from opticstream.config.enface_preview import PreviewGridConfig
    PreviewGridConfig(grid_type=traversal, order=order)
    strip, fast = divmod(index, rows)
    slow = strip
    count = columns if columns is not None else strip + 1
    initial, subsequent = order.split("-")
    reverse_fast = initial in {"left", "up"}
    if traversal.startswith("snake-") and strip % 2:
        reverse_fast = not reverse_fast
    if reverse_fast:
        fast = rows - 1 - fast
    if subsequent in {"left", "up"}:
        slow = count - 1 - slow
    if batch_id is not None:
        slow = 0
    column, row = (fast, slow) if row_based else (slow, fast)
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
            grid = config.enface_preview.grid_config
            x, y = tile_position(index, config.acquisition.grid_size_y, shape, overlap,
                                 grid.grid_type, batch_id, order=grid.order,
                                 columns=max(files) // config.acquisition.grid_size_y + 1)
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
    progress_file = progress_path(config, mosaic_ident, batch_id)
    progress = read_progress(progress_file, signature)
    outputs = {m: preview_path(config, mosaic_ident, batch_id, f"{m}.nii") for m in MODALITIES}
    for modality in MODALITIES:
        output = outputs[modality]
        if modality not in progress["stitched"] or not output.with_suffix(".jpg").exists() or not output.exists():
            stitch_preview_modality(config, files[modality], modality, output, batch_id)
            if fingerprint(config, files) != signature:
                raise RuntimeError("Acquisition files changed while stitching; wait for stability and retry")
            if modality not in progress["stitched"]:
                progress["stitched"].append(modality)
            save_progress(progress_file, progress)
    if slack_notifications_enabled():
        remaining = [m for m in MODALITIES if m not in progress["uploaded"]]
        if remaining:
            paths = [str(outputs[m].with_suffix(".jpg")) for m in remaining]
            scope = "full acquisition" if batch_id is None else f"batch {batch_id}"
            label = f"{mosaic_ident.project_name}, slice {mosaic_ident.slice_id}, mosaic {mosaic_ident.mosaic_id}, {acquisition}, {scope}"
            results = upload_multiple_files_to_slack(filepaths=paths,
                titles=[f"{label} - {m.upper()}" for m in remaining], initial_comment=label)
            for modality, path in zip(remaining, paths):
                if results.get(path):
                    progress["uploaded"].append(modality)
            save_progress(progress_file, progress)
            if any(not results.get(path) for path in paths):
                raise RuntimeError("Some preview Slack uploads failed; successful uploads are checkpointed")
    return {m: str(outputs[m].with_suffix(".jpg")) for m in MODALITIES}


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
