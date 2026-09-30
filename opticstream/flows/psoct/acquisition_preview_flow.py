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


def _image_regex(pattern, values, *, any_padding):
    """Regex for a preview pattern with {image} as a digits group, or None.

    Returns None when ``any_padding`` is False and {image} has an explicit
    format spec (e.g. {image:04d}), which keeps exact rendering.
    """
    parts = []
    for literal, field, spec, conversion in Formatter().parse(pattern):
        parts.append(re.escape(literal))
        if field is None:
            continue
        if field == "image":
            if (spec or conversion) and not any_padding:
                return None
            parts.append(r"(?P<image>[0-9]+)")
        else:
            parts.append(re.escape(format(values[field], spec)))
    return re.compile("".join(parts))


def list_names(root):
    """File names in ``root`` (empty if it does not exist yet)."""
    try:
        return [entry.name for entry in os.scandir(root)]
    except FileNotFoundError:
        return []


def image_numbers_present(pattern, values, names):
    """Image numbers of files matching ``pattern``, whatever their padding."""
    regex = _image_regex(pattern, values, any_padding=True)
    return {int(found["image"]) for name in names if (found := regex.fullmatch(name))}


def _padded_image_matches(pattern, values, names):
    """Map image number -> filename for a pattern whose {image} has no format spec.

    Like filename_pattern, a bare {image} matches any zero padding (1, 01, 0001),
    compared as a number. Returns None when {image} has an explicit format spec
    (e.g. {image:04d}), which keeps exact rendering.
    """
    regex = _image_regex(pattern, values, any_padding=False)
    if regex is None:
        return None
    matches = {}
    for name in names:
        found = regex.fullmatch(name)
        if found is None:
            continue
        number = int(found["image"])
        if number in matches:
            raise ValueError(f"Several files match image {number} of {pattern!r}: "
                             f"{matches[number]}, {name}")
        matches[number] = name
    return matches


def expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id=None, *,
                   image_offset=0, names=None):
    """Return exact expected paths, never infer completeness from file counts.

    ``image_offset`` shifts filename image numbers for continuously numbered
    acquisitions (tile 1 of this mosaic is image first_image + image_offset).
    ``names`` is an optional pre-read listing of ``input_dir``.
    """
    context = mosaic_context_from_ident(mosaic_ident, config)
    rows = context.grid_size_y(config)
    columns = context.grid_size_x(config)
    if batch_id is not None and not 1 <= batch_id <= columns:
        raise ValueError(f"batch_id must be between 1 and {columns}")
    indices = range(columns * rows) if batch_id is None else range((batch_id - 1) * rows, batch_id * rows)
    root = Path(input_dir).resolve()
    values = dict(slice=mosaic_ident.slice_id, mosaic=mosaic_ident.mosaic_id,
                  acquisition=acquisition, project=mosaic_ident.project_name)
    if names is None:
        names = list_names(root)
    result = {}
    for modality in MODALITIES:
        pattern = getattr(config.enface_preview, f"{modality}_pattern")
        existing = _padded_image_matches(pattern, values, names)
        paths = {}
        for index in indices:
            image = index + config.enface_preview.first_image + image_offset
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


def tile_shape(path):
    """In-plane (x, y) size of an enface tile, from the NIfTI header when possible."""
    # Treat only the final extension as a format indicator; acquisition labels
    # may contain dots (e.g. tilted12.5deg).
    if path.name.lower().endswith((".nii", ".nii.gz")):
        shape = nib.load(path).shape
    else:
        from opticstream.data_processing.qc.convert_image import load_data
        shape = np.shape(load_data(input_path=path, mat_variable=None))
    if len(shape) < 2 or any(size != 1 for size in shape[2:]):
        raise ValueError(f"Expected 2D enface map (or singleton trailing axes): {path}, {shape}")
    return tuple(shape[:2])


def tile_config(config, files, shape, overlap, out, per_strip=None):
    """Write a linc-convert tile YAML for ``files`` (acquisition index -> path).

    ``per_strip`` is the mosaic's tiles per strip (default: grid_size_y).

    The full grid comes from linc-convert's generate_tile_config; entries are then
    limited to ``files`` (a single batch keeps the direction its strip has in the
    full snake), pointed at the resolved paths, and shifted to start at (0, 0).
    """
    from linc_convert.modalities.psoct.generate_tile_config import generate_tile_config

    grid = config.enface_preview.grid_config
    per_strip = per_strip or config.acquisition.grid_size_y
    strips = max(files) // per_strip + 1
    row_based = grid.grid_type in {"row-by-row", "snake-by-rows"}
    columns, rows = (per_strip, strips) if row_based else (strips, per_strip)
    generate_tile_config(columns=columns, rows=rows, tile_size_x=shape[0], tile_size_y=shape[1],
                         base_dir="", naming_format="{tile_number}", grid_type=grid.grid_type,
                         order=grid.order, overlap_percentage=overlap, out=str(out))
    spec = yaml.safe_load(Path(out).read_text())
    tiles = [tile for tile in spec["tiles"] if tile["tile_number"] - 1 in files]
    x0 = min(tile["x"] for tile in tiles)
    y0 = min(tile["y"] for tile in tiles)
    for tile in tiles:
        index = tile["tile_number"] - 1
        # Round like pixel offsets; mosaic2d truncates, so 20.999... would become 20.
        tile.update(filepath=str(files[index]), tile_number=index + 1,
                    x=round(tile["x"] - x0), y=round(tile["y"] - y0))
    spec["tiles"] = tiles
    spec["metadata"].update(base_dir="", scan_resolution=list(config.acquisition.scan_resolution_3d[:2]))
    Path(out).write_text(yaml.safe_dump(spec))
    return out


@task
def stitch_preview_modality(config: PSOCTScanConfigModel, files: dict[int, Path],
                            modality: str, output: Path, batch_id: int | None = None,
                            per_strip: int | None = None) -> Path:
    """Stitch acquisition tiles in place with linc-convert generate_tile_config + mosaic2d.

    Writes ``output`` (NIfTI), a JPEG next to it, and a ``*_grid.jpg`` tile diagram.
    Tiles are copied to a scratch directory only when they need converting:
    radian orientation maps (linc-convert expects degrees) and plain 2D maps
    (mosaic2d writes 3D voxel sizes, so tiles keep a singleton third axis).
    """
    from opticstream.data_processing.qc.convert_image import load_data
    from linc_convert.modalities.psoct.mosaic import mosaic2d

    overlap = config.acquisition.tile_overlap / 100.0
    if not 0 <= overlap < 1:
        raise ValueError("Preview tile_overlap must be a percentage in [0, 100)")
    output.parent.mkdir(parents=True, exist_ok=True)
    shapes = {index: tile_shape(path) for index, path in files.items()}
    shape = next(iter(shapes.values()))
    for index, other in shapes.items():
        if other != shape:
            raise ValueError(f"Tile dimensions differ: {files[index]}, {other} != {shape}")
    to_degrees = modality == "ori" and config.enface_preview.orientation_units == "radians"
    jpeg = output.with_suffix(".jpg")
    with tempfile.TemporaryDirectory(prefix="enface-preview-", dir=output.parent) as scratch:
        sources = dict(files)
        for index, path in files.items():
            is_nifti = path.name.lower().endswith((".nii", ".nii.gz"))
            if not to_degrees and is_nifti and len(nib.load(path).shape) > 2:
                continue
            array = (np.asarray(nib.load(path).dataobj) if is_nifti
                     else np.asarray(load_data(input_path=path, mat_variable=None)))
            array = array.reshape(*shape, 1).astype(np.float32)
            if to_degrees:
                array = np.rad2deg(array)
            sources[index] = Path(scratch) / f"tile-{index}.nii"
            nib.save(nib.Nifti1Image(array, np.eye(4)), sources[index])
        spec = tile_config(config, sources, shape, overlap, Path(scratch) / "tiles.yaml",
                           per_strip)
        mosaic2d(tile_info_file=str(spec), nifti_output=str(output),
                 jpeg_output=None if modality == "ori" else str(jpeg),
                 #print_grid=str(output.with_name(f"{output.stem}_grid.jpg")),
                 tile_overlap=overlap if overlap else 0, circular_mean=modality == "ori",
                 voxel_size_xyz=list(config.acquisition.scan_resolution_3d[:2]))
    if not output.is_file():
        raise RuntimeError(f"mosaic2d did not produce {output}")
    if modality == "ori":
        # mosaic2d's JPEG is grayscale; orientation previews use an angle colormap.
        # Transpose to match the orientation of mosaic2d's JPEGs.
        angles = np.asarray(nib.load(output).dataobj).squeeze().T
        convert_image(data=angles, output=jpeg, angle_to_rgb=True, output_format="jpg")
    return jpeg


def run_preview(config, mosaic_ident, input_dir, acquisition, batch_id=None, *, image_offset=0):
    enabled = config.enface_preview.acquisition_enabled if batch_id is None else config.enface_preview.batch_enabled
    if not enabled:
        return {}
    files = expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id,
                           image_offset=image_offset)
    signature = fingerprint(config, files)
    progress_file = progress_path(config, mosaic_ident, batch_id)
    progress = read_progress(progress_file, signature)
    outputs = {m: preview_path(config, mosaic_ident, batch_id, f"{m}.nii") for m in MODALITIES}
    per_strip = mosaic_context_from_ident(mosaic_ident, config).grid_size_y(config)
    for modality in MODALITIES:
        output = outputs[modality]
        if modality not in progress["stitched"] or not output.with_suffix(".jpg").exists() or not output.exists():
            stitch_preview_modality(config, files[modality], modality, output, batch_id,
                                    per_strip=per_strip)
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
                          input_dir: Path, acquisition: str, batch_id: int,
                          image_offset: int = 0):
    return run_preview(config, mosaic_ident, input_dir, acquisition, batch_id,
                       image_offset=image_offset)


@flow(name="preview-enface-acquisition")
def preview_enface_acquisition(config: PSOCTScanConfigModel, mosaic_ident: OCTMosaicId,
                                input_dir: Path, acquisition: str, image_offset: int = 0):
    return run_preview(config, mosaic_ident, input_dir, acquisition,
                       image_offset=image_offset)


def to_deployment(*, deployment_name="local", extra_tags=()):
    return [f.to_deployment(name=deployment_name, tags=["acquisition-preview", *extra_tags], concurrency_limit=1)
            for f in (preview_enface_batch, preview_enface_acquisition)]
