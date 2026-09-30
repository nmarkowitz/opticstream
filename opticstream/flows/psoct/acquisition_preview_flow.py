"""Batch and acquisition QC previews from externally produced enface tiles.

The maps are found through the project's input naming: acquisition.filename_pattern
(with a {modality} placeholder) and filename_modality_map labels mapped to aip, mip,
ori and ret (required) and surf (when mapped). Only JPEGs are written. No processing
milestones are mutated. Local manifests permit resumable previews. Run only one
preview watcher for a given project/slice/mosaic at a time.
"""
import hashlib
import json
import os
from pathlib import Path
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
from opticstream.utils.oct_input_naming import compile_pattern
from opticstream.utils.slack_settings import slack_notifications_enabled

REQUIRED_MODALITIES = ("aip", "mip", "ori", "ret")
# Previewed only when filename_modality_map has a label for it.
OPTIONAL_MODALITIES = ("surf",)
MODALITIES = REQUIRED_MODALITIES + OPTIONAL_MODALITIES

# linc-convert traversal of the acquisition grid: strips of grid_size_y tiles run
# along columns, snaking, starting bottom-left. The same for every scanner.
GRID_TYPE = "snake-by-columns"
GRID_ORDER = "up-right"


def preview_modalities(config):
    """Modalities previewed for this project: the required four plus mapped optional ones."""
    mapped = set(config.acquisition.filename_modality_map.values())
    return REQUIRED_MODALITIES + tuple(m for m in OPTIONAL_MODALITIES if m in mapped)


def naming_error(config):
    """Why the input naming cannot locate enface maps, or None when it can."""
    pattern = config.acquisition.filename_pattern
    if not pattern:
        return "acquisition.filename_pattern is not set"
    if "modality" not in compile_pattern(pattern).groupindex:
        return "acquisition.filename_pattern has no {modality} placeholder"
    mapped = set(config.acquisition.filename_modality_map.values())
    missing = [m for m in REQUIRED_MODALITIES if m not in mapped]
    if missing:
        return f"acquisition.filename_modality_map has no label for {', '.join(missing)}"
    return None


def list_names(root):
    """File names in ``root`` (empty if it does not exist yet)."""
    try:
        return [entry.name for entry in os.scandir(root)]
    except FileNotFoundError:
        return []


def enface_files(config, names, *, acquisition=None, slice_id=None):
    """Map (modality, image number) -> file name for the enface maps in ``names``.

    Names are parsed with acquisition.filename_pattern; the {modality} label is
    translated by filename_modality_map. When the pattern has {acq} or
    {slice}, only names for ``acquisition`` / ``slice_id`` are kept.
    """
    error = naming_error(config)
    if error:
        raise ValueError(f"Enface previews need the input naming to cover them: {error}")
    acq = config.acquisition
    regex = compile_pattern(acq.filename_pattern)
    found = {}
    for name in names:
        match = regex.fullmatch(name)
        if match is None:
            continue
        values = match.groupdict()
        modality = acq.filename_modality_map.get(values["modality"])
        if modality not in MODALITIES:
            continue
        if acquisition is not None and values.get("acq", acquisition) != acquisition:
            continue
        if slice_id is not None and int(values.get("slice", slice_id)) != slice_id:
            continue
        key = (modality, int(values["image"]))
        if key in found:
            raise ValueError(f"Several {modality} files for image {key[1]}: {found[key]}, {name}")
        found[key] = name
    return found


def expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id=None, *,
                   image_offset=0, names=None):
    """Return exact expected paths, never infer completeness from file counts.

    ``image_offset`` shifts filename image numbers for continuously numbered
    acquisitions (tile 1 of this mosaic is image 1 + image_offset).
    ``names`` is an optional pre-read listing of ``input_dir``.
    Missing tiles get a path that does not exist, so callers see them as absent.
    """
    context = mosaic_context_from_ident(mosaic_ident, config)
    rows = context.grid_size_y(config)
    columns = context.grid_size_x(config)
    if batch_id is not None and not 1 <= batch_id <= columns:
        raise ValueError(f"batch_id must be between 1 and {columns}")
    indices = range(columns * rows) if batch_id is None else range((batch_id - 1) * rows, batch_id * rows)
    root = Path(input_dir).resolve()
    if names is None:
        names = list_names(root)
    found = enface_files(config, names, acquisition=acquisition, slice_id=mosaic_ident.slice_id)
    result = {}
    for modality in preview_modalities(config):
        paths = {}
        for index in indices:
            image = index + 1 + image_offset
            paths[index] = root / (found.get((modality, image)) or f"missing_{modality}_image_{image:04d}")
        result[modality] = paths
    return result


def fingerprint(config, files):
    records = []
    for modality, paths in files.items():
        for index, path in paths.items():
            stat = path.stat()
            if not path.is_file() or stat.st_size == 0:
                raise ValueError(f"Empty or invalid tile: {path}")
            records.append((modality, index, str(path), stat.st_size, stat.st_mtime_ns))
    settings = (GRID_TYPE, GRID_ORDER, config.acquisition.model_dump(mode="json"))
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

    per_strip = per_strip or config.acquisition.grid_size_y
    strips = max(files) // per_strip + 1
    generate_tile_config(columns=strips, rows=per_strip, tile_size_x=shape[0], tile_size_y=shape[1],
                         base_dir="", naming_format="{tile_number}", grid_type=GRID_TYPE,
                         order=GRID_ORDER, overlap_percentage=overlap, out=str(out))
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

    Writes only the ``output`` JPEG; the stitched NIfTI mosaic2d produces stays in a
    scratch directory that is removed afterwards. Tiles are copied to that scratch
    directory only when they need converting: radian orientation maps (linc-convert
    expects degrees) and plain 2D maps (mosaic2d writes 3D voxel sizes, so tiles
    keep a singleton third axis).
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
    to_degrees = modality == "ori" and config.acquisition.orientation_units == "radians"
    jpeg = output
    with tempfile.TemporaryDirectory(prefix="enface-preview-", dir=output.parent) as scratch:
        stitched = Path(scratch) / f"{modality}.nii"
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
        mosaic2d(tile_info_file=str(spec), nifti_output=str(stitched),
                 jpeg_output=None if modality == "ori" else str(jpeg),
                 #print_grid=str(output.with_name(f"{output.stem}_grid.jpg")),
                 tile_overlap=overlap if overlap else 0, circular_mean=modality == "ori",
                 voxel_size_xyz=list(config.acquisition.scan_resolution_3d[:2]))
        if not stitched.is_file():
            raise RuntimeError(f"mosaic2d did not produce the stitched {modality} mosaic")
        if modality == "ori":
            # mosaic2d's JPEG is grayscale; orientation previews use an angle colormap.
            # Transpose to match the orientation of mosaic2d's JPEGs.
            # mmap=False: a memory-mapped scratch file cannot be deleted on Windows.
            angles = np.asarray(nib.load(stitched, mmap=False).dataobj).squeeze().T
            convert_image(data=angles, output=jpeg, angle_to_rgb=True, output_format="jpg")
    if not jpeg.is_file():
        raise RuntimeError(f"mosaic2d did not produce {jpeg}")
    return jpeg


def run_preview(config, mosaic_ident, input_dir, acquisition, batch_id=None, *, image_offset=0):
    files =expected_tiles(config, mosaic_ident, input_dir, acquisition, batch_id,
                           image_offset=image_offset)
    signature = fingerprint(config, files)
    progress_file = progress_path(config, mosaic_ident, batch_id)
    progress = read_progress(progress_file, signature)
    modalities = list(files)
    outputs = {m: preview_path(config, mosaic_ident, batch_id, f"{m}.jpg") for m in modalities}
    per_strip = mosaic_context_from_ident(mosaic_ident, config).grid_size_y(config)
    for modality in modalities:
        output = outputs[modality]
        if modality not in progress["stitched"] or not output.exists():
            stitch_preview_modality(config, files[modality], modality, output, batch_id,
                                    per_strip=per_strip)
            if fingerprint(config, files) != signature:
                raise RuntimeError("Acquisition files changed while stitching; wait for stability and retry")
            if modality not in progress["stitched"]:
                progress["stitched"].append(modality)
            save_progress(progress_file, progress)
    if slack_notifications_enabled():
        remaining = [m for m in modalities if m not in progress["uploaded"]]
        if remaining:
            paths = [str(outputs[m]) for m in remaining]
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
    return {m: str(outputs[m]) for m in modalities}


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
