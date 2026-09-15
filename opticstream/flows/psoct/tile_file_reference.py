from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel, TileSavingType
from opticstream.flows.psoct.utils import MosaicContext
from opticstream.utils.filename_utils import (
    extract_processed_index_from_filename,
    extract_spectral_index_from_filename,
    extract_tile_number_from_filename,
)


class TileFileReference(BaseModel):
    """Per input file: path and tile index used for indexed processed output names."""

    spectral_file_path: Path | None = None
    complex_file_path: Path | None = None
    dbi_file_path: Path | None = None
    aip_file_path: Path | None = None
    mip_file_path: Path | None = None
    ori_file_path: Path | None = None
    ret_file_path: Path | None = None
    surf_file_path: Path | None = None
    tile_number: int = Field(
        default=0,
        description=(
            "1-based tile index in processed stems mosaic_*_image_{tile:04d}. "
            "For mosaics_per_slice==2, matches image_<n> in the filename. "
            "For mosaics_per_slice==3 (processed_* layout), logical grid index from "
            "processed index and grid_size_x (may differ from any image_ token)."
        ),
    )


def build_tile_file_reference_list(
    file_list: list[Path],
    *,
    config: PSOCTScanConfigModel,
    mosaic_context: MosaicContext,
) -> dict[int, TileFileReference]:
    mosaics_per_slice = config.mosaics_per_slice
    grid_size_x = mosaic_context.grid_size_x(config)
    refs: dict[int, TileFileReference] = defaultdict(TileFileReference)
    for p in file_list:
        if config.acquisition.filename_pattern:
            from opticstream.utils.oct_input_naming import parse_input_name
            parsed = parse_input_name(p.name, config.acquisition, mosaics_per_slice)
            if parsed is None:
                raise ValueError(f"Filename does not match configured input naming: {p.name}")
            tile_number = parsed.image_index
            modality = parsed.modality
            if modality == "processed":
                modality = "complex" if config.acquisition.tile_saving_type in (TileSavingType.COMPLEX_WITH_SPECTRAL, TileSavingType.COMPLEX) else "dbi"
            field = f"{modality}_file_path"
            ref = refs[tile_number]
            if getattr(ref, field) is not None:
                raise ValueError(f"Duplicate {modality} input for tile {tile_number}: {p}")
            ref.tile_number = tile_number
            setattr(ref, field, p)
            continue
        if mosaics_per_slice == 2:
            tile_number = extract_tile_number_from_filename(str(p))
        else:
            try:
                i = extract_processed_index_from_filename(str(p))
            except ValueError:
                i = extract_spectral_index_from_filename(str(p))
            j = grid_size_x
            if j < 1:
                raise ValueError(f"grid_size_x must be >= 1, got {j}")
            tile_number = (i - 1) % j + 1
        
        refs[tile_number].tile_number = tile_number
        if "spectral" in p.name:
            refs[tile_number].spectral_file_path = p
        elif "aip" in p.name:
            refs[tile_number].aip_file_path = p
        elif "mip" in p.name:
            refs[tile_number].mip_file_path = p
        elif "ori" in p.name:
            refs[tile_number].ori_file_path = p
        elif "ret" in p.name:
            refs[tile_number].ret_file_path = p
        elif "surf" in p.name:
            refs[tile_number].surf_file_path = p
        elif "processed" in p.name:
            if config.acquisition.tile_saving_type in (TileSavingType.COMPLEX_WITH_SPECTRAL, TileSavingType.COMPLEX):
                refs[tile_number].complex_file_path = p
            else:
                refs[tile_number].dbi_file_path = p
    return refs
