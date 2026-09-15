"""Configurable input names, independent of generated output names."""
import re
from functools import lru_cache
from string import Formatter
from dataclasses import dataclass


@lru_cache(maxsize=64)
def compile_pattern(pattern):
    fields = set()
    parts = []
    for literal, field, spec, conversion in Formatter().parse(pattern):
        parts.append(re.escape(literal))
        if field is None:
            continue
        if field not in {"subject", "slice", "image", "acquisition", "modality", "extension"}:
            raise ValueError(f"Unknown filename placeholder: {field}")
        if field in fields or spec or conversion:
            raise ValueError("Filename placeholders must be unique and have no format specifiers.")
        fields.add(field)
        value = r"[0-9]+" if field in {"slice", "image"} else (r"\.[A-Za-z0-9.]+" if field == "extension" else r"[^/\\]+?")
        parts.append(f"(?P<{field}>{value})")
    if not {"slice", "image", "acquisition"} <= fields:
        raise ValueError("filename_pattern requires {slice}, {image}, and {acquisition}.")
    return re.compile("".join(parts))


@dataclass(frozen=True)
class InputTile:
    source_mosaic_id: int
    image_index: int
    modality: str


def parse_input_name(name, acquisition, mosaics_per_slice):
    match = compile_pattern(acquisition.filename_pattern).fullmatch(name)
    if match is None:
        return None
    values = match.groupdict()
    slot = acquisition.acquisition_mosaic_map.get(values["acquisition"])
    if slot is None:
        return None
    if not 1 <= slot <= mosaics_per_slice:
        raise ValueError("acquisition_mosaic_map values must fit mosaics_per_slice.")
    slice_id, image = int(values["slice"]), int(values["image"])
    if slice_id < 1 or image < 1:
        raise ValueError("Filename slice and image numbers must be positive.")
    label = values.get("modality", acquisition.filename_default_modality)
    modality = acquisition.filename_modality_map.get(label)
    if modality is None:
        return None
    return InputTile((slice_id - 1) * mosaics_per_slice + slot, image, modality)
