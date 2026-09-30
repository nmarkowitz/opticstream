"""Configurable input names, independent of generated output names."""
import re
from functools import lru_cache
from string import Formatter
from dataclasses import dataclass

# Matched after a pattern that names no extension, e.g. "spectral_{image}_{modality}".
IMPLICIT_EXTENSIONS = (".nii.gz", ".nii", ".mat", ".raw")


def _names_extension(literal):
    """True when the pattern's trailing literal already names a file extension."""
    return "." in literal


@lru_cache(maxsize=64)
def compile_pattern(pattern):
    """Regex for a filename_pattern.

    Without {extension} and without a literal extension at the end (e.g.
    ``sub-{subject}_tile-{image}_{modality}``), the name may end in any of
    IMPLICIT_EXTENSIONS, captured as the ``extension`` group.
    """
    fields = set()
    parts = []
    trailing = ""
    for literal, field, spec, conversion in Formatter().parse(pattern):
        parts.append(re.escape(literal))
        trailing = literal
        if field is None:
            continue
        trailing = ""
        if field not in {"subject", "slice", "image", "acq", "modality", "extension"}:
            raise ValueError(f"Unknown filename placeholder: {field}")
        if field in fields or spec or conversion:
            raise ValueError("Filename placeholders must be unique and have no format specifiers.")
        fields.add(field)
        value = r"[0-9]+" if field in {"slice", "image"} else (r"\.[A-Za-z0-9.]+" if field == "extension" else r"[^/\\]+?")
        parts.append(f"(?P<{field}>{value})")
    # {slice} and {acq} may instead be supplied by the watcher
    # (ops oct watch --slice/--acquisition/--mosaic), e.g. "spectral_{image}.nii".
    if "image" not in fields:
        raise ValueError("filename_pattern requires {image}.")
    if "extension" not in fields and not _names_extension(trailing):
        choices = "|".join(re.escape(ext) for ext in IMPLICIT_EXTENSIONS)
        parts.append(f"(?P<extension>{choices})")
    return re.compile("".join(parts))


@dataclass(frozen=True)
class InputTile:
    # None when the filename has no {slice}/{acq} and none was supplied.
    source_mosaic_id: int | None
    image_index: int
    modality: str


def pattern_names_mosaic(pattern):
    """True when filenames carry their own {slice} or {acq}."""
    return bool({"slice", "acq"} & set(compile_pattern(pattern).groupindex))


def parse_input_name(name, acquisition, mosaics_per_slice, *, slice_id=None, mosaic_slot=None,
                     sequence=None):
    """
    Parse an input filename with the configured pattern.

    ``slice_id`` / ``mosaic_slot`` fill in values the filename lacks. When the
    filename also contains them, they act as filters: a mismatch returns None.
    ``sequence`` (an AcquisitionSequence) places names with neither {slice} nor
    {acq} by their continuous image number; image_index is then the tile
    number within that mosaic.
    """
    match = compile_pattern(acquisition.filename_pattern).fullmatch(name)
    if match is None:
        return None
    values = match.groupdict()
    if sequence is not None and "slice" not in values and "acq" not in values:
        image = int(values["image"])
        if image < 1:
            raise ValueError("Filename image numbers must be positive.")
        modality = acquisition.filename_modality_map.get(
            values.get("modality", acquisition.filename_default_modality)
        )
        if modality is None:
            return None
        position = sequence.locate(image)
        return InputTile(position.source_mosaic_id, position.tile, modality)
    slot = mosaic_slot
    if "acq" in values:
        slot = acquisition.acquisition_mosaic_map.get(values["acq"])
        if slot is None or (mosaic_slot is not None and slot != mosaic_slot):
            return None
    if slot is not None and not 1 <= slot <= mosaics_per_slice:
        raise ValueError("acquisition_mosaic_map values must fit mosaics_per_slice.")
    file_slice = slice_id
    if "slice" in values:
        file_slice = int(values["slice"])
        if slice_id is not None and file_slice != slice_id:
            return None
    image = int(values["image"])
    if (file_slice is not None and file_slice < 1) or image < 1:
        raise ValueError("Filename slice and image numbers must be positive.")
    label = values.get("modality", acquisition.filename_default_modality)
    modality = acquisition.filename_modality_map.get(label)
    if modality is None:
        return None
    source_mosaic_id = None
    if file_slice is not None and slot is not None:
        source_mosaic_id = (file_slice - 1) * mosaics_per_slice + slot
    return InputTile(source_mosaic_id, image, modality)
