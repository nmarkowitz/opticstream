from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, computed_field

from opticstream.config.psoct_scan_config import (
    PSOCTScanConfigModel,
    get_psoct_scan_config,
)
from opticstream.state.oct_project_state import (
    OCTBatchId,
    OCTMosaicId,
    OCTSliceId,
)


# ---------------------------------------------------------------------------
# Internal primitives
# ---------------------------------------------------------------------------


def _ensure_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _project_name_from_payload(payload: Mapping[str, Any]) -> str:
    """Extract ``project_name`` from *payload*, trying ident keys as fallback."""
    project_name = payload.get("project_name")
    if project_name is not None:
        return str(project_name)
    for ident_key in ("batch_ident", "mosaic_ident", "slice_ident"):
        raw = payload.get(ident_key)
        if isinstance(raw, (OCTBatchId, OCTMosaicId, OCTSliceId)):
            return raw.project_name
        elif isinstance(raw, Mapping):
            pn = raw.get("project_name")
            if pn is not None:
                return str(pn)
    raise KeyError(
        "payload must include project_name or one of "
        "batch_ident/mosaic_ident/slice_ident with project_name"
    )


def _mosaic_file_path(
    base_dir: str | Path, mosaic_id: int, suffix: str
) -> Path:
    """``{base_dir}/mosaic_{mosaic_id:03d}_{suffix}``"""
    return _ensure_path(base_dir) / f"{mosaic_prefix(mosaic_id)}_{suffix}"


# ---------------------------------------------------------------------------
# Slice / mosaic arithmetic
# ---------------------------------------------------------------------------


def slice_from_mosaic(mosaic_id: int, mosaics_per_slice: int) -> int:
    """Return the 1-based slice id for a 1-based mosaic id."""
    if mosaic_id < 1:
        raise ValueError(f"mosaic_id must be >= 1, got {mosaic_id}")
    return ((mosaic_id - 1) // mosaics_per_slice) + 1


def first_mosaic_for_slice(slice_id: int, mosaics_per_slice: int) -> int:
    """Return the first mosaic id belonging to a 1-based slice id."""
    if slice_id < 1:
        raise ValueError(f"slice_id must be >= 1, got {slice_id}")
    return mosaics_per_slice * (slice_id - 1) + 1


def mosaic_position_in_slice(mosaic_id: int, mosaics_per_slice: int) -> int:
    """Return the 1-based mosaic position within the slice."""
    if mosaic_id < 1:
        raise ValueError(f"mosaic_id must be >= 1, got {mosaic_id}")
    return ((mosaic_id - 1) % mosaics_per_slice) + 1


def logical_mosaic_from_source_mosaic(
    source_mosaic_id: int,
    *,
    mosaics_per_slice: int,
    slice_offset: int,
) -> tuple[int, int]:
    """Convert a source mosaic id into ``(logical_slice_id, logical_mosaic_id)``."""
    source_slice_id = slice_from_mosaic(source_mosaic_id, mosaics_per_slice)
    pos_in_slice = mosaic_position_in_slice(source_mosaic_id, mosaics_per_slice)
    logical_slice_id = source_slice_id + slice_offset
    if logical_slice_id < 1:
        raise ValueError(
            f"slice_offset={slice_offset} produces invalid logical_slice_id={logical_slice_id} "
            f"from source_mosaic_id={source_mosaic_id}"
        )
    logical_mosaic_id = first_mosaic_for_slice(logical_slice_id, mosaics_per_slice) + (
        pos_in_slice - 1
    )
    return logical_slice_id, logical_mosaic_id


def logical_first_mosaic_from_source_slice(
    source_slice_id: int,
    *,
    mosaics_per_slice: int,
    slice_offset: int,
) -> tuple[int, int]:
    """Convert a source slice id into ``(logical_slice_id, logical first mosaic id)``."""
    if source_slice_id < 1:
        raise ValueError(f"source_slice_id must be >= 1, got {source_slice_id}")
    logical_slice_id = source_slice_id + slice_offset
    if logical_slice_id < 1:
        raise ValueError(
            f"slice_offset={slice_offset} produces invalid logical_slice_id={logical_slice_id} "
            f"from source_slice_id={source_slice_id}"
        )
    logical_mosaic_id = first_mosaic_for_slice(logical_slice_id, mosaics_per_slice)
    return logical_slice_id, logical_mosaic_id


# ---------------------------------------------------------------------------
# Identity builders
# ---------------------------------------------------------------------------


def oct_batch_ident(project_name: str, mosaic_id: int, batch_id: int) -> OCTBatchId:
    """Build canonical ``OCTBatchId`` (``slice_id`` derived from ``mosaic_id``)."""
    cfg = get_psoct_scan_config(project_name)
    return OCTBatchId(
        project_name=project_name,
        slice_id=slice_from_mosaic(mosaic_id, cfg.mosaics_per_slice),
        mosaic_id=mosaic_id,
        batch_id=batch_id,
    )


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def _model_from_payload(payload: Mapping[str, Any], key: str, model_type: Any) -> Any:
    if key not in payload:
        raise KeyError(f"payload must include {key}")
    value = payload[key]
    if isinstance(value, model_type):
        return value
    if isinstance(value, Mapping):
        return model_type(**value)
    raise TypeError(f"payload[{key}] must be a mapping or {model_type.__name__}")


def batch_ident_from_payload(payload: Mapping[str, Any]) -> OCTBatchId:
    return _model_from_payload(payload, "batch_ident", OCTBatchId)


def mosaic_ident_from_payload(payload: Mapping[str, Any]) -> OCTMosaicId:
    return _model_from_payload(payload, "mosaic_ident", OCTMosaicId)


def slice_ident_from_payload(payload: Mapping[str, Any]) -> OCTSliceId:
    return _model_from_payload(payload, "slice_ident", OCTSliceId)


def load_scan_config_for_payload(payload: Mapping[str, Any]) -> PSOCTScanConfigModel:
    project_name = _project_name_from_payload(payload)
    override = payload.get("override_config")
    cfg = get_psoct_scan_config(project_name, override_config_name=override)
    return PSOCTScanConfigModel.model_validate(cfg.model_dump())


def path_list_from_payload(
    payload: Mapping[str, Any], key: str = "file_list"
) -> list[Path]:
    values = payload.get(key, [])
    if not isinstance(values, list):
        raise TypeError(f"payload[{key}] must be a list")
    return [Path(v) for v in values]


# ---------------------------------------------------------------------------
# Output helpers (enface / path extraction)
# ---------------------------------------------------------------------------


def nifti_paths_from_enface_outputs(
    enface_outputs: Mapping[str, Any],
) -> list[str]:
    paths: list[str] = []
    for v in enface_outputs.values():
        if isinstance(v, str):
            paths.append(v)
        elif isinstance(v, dict) and "nifti" in v:
            paths.append(str(v["nifti"]))
    return paths


def non_empty_paths_from_mapping(paths_by_key: Mapping[str, Any]) -> list[str]:
    return [str(p) for p in paths_by_key.values() if p]


def normalize_float_sequence(
    value: Any,
    *,
    default: Sequence[float] | None = None,
) -> list[float]:
    if value is None:
        return list(default or [0.01, 0.01, 0.0025])
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    raise TypeError(f"expected list or tuple, got {type(value)}")


# ---------------------------------------------------------------------------
# Path building
# ---------------------------------------------------------------------------


def get_slice_paths(
    project_base_path: str | Path, slice_id: int
) -> tuple[Path, Path, Path, Path]:
    """
    Standard slice directory structure.

    Returns ``(slice_path, processed_path, stitched_path, complex_path)``.
    """
    slice_path = _ensure_path(project_base_path) / f"slice-{slice_id:02d}"
    return (
        slice_path,
        slice_path / "processed",
        slice_path / "stitched",
        slice_path / "complex",
    )


def get_dandi_slice_path(dandiset_path: str, slice_id: int) -> Path:
    """``{dandiset_path}/sample-slice{slice_id:02d}/``"""
    return _ensure_path(dandiset_path) / f"sample-slice{slice_id:02d}"


def mosaic_prefix(mosaic_id: int) -> str:
    """Format the standard mosaic prefix: ``mosaic_001``, ``mosaic_002``, etc."""
    return f"mosaic_{mosaic_id:03d}"


def processed_output_prefix(mosaic_id: int, tile_index: int) -> str:
    """
    Basename stem for per-tile processed NIfTIs.

    Matches MATLAB ``psoct.file.internal.buildProcessedOutputPrefix`` and Fiji-style
    ``{mosaic_prefix}_image_{tile:04d}`` (no extension).
    """
    return f"{mosaic_prefix(mosaic_id)}_image_{tile_index:04d}"

def get_processed_tile_path(processed_path: Path, mosaic_id:int, tile_id:int, modality:str, compressed: bool = True) -> Path:
    return processed_path / (f"{processed_output_prefix(mosaic_id, tile_id)}_{modality}.nii" + (".gz" if compressed else ""))

def get_mosaic_tile_info_path(
    stitched_path: str | Path, mosaic_id: int, modality: str
) -> Path:
    """``{stitched_path}/mosaic_{mosaic_id:03d}_{modality}.yaml``"""
    return _mosaic_file_path(stitched_path, mosaic_id, f"{modality}.yaml")


def get_mosaic_nifti_path(
    stitched_path: str | Path, mosaic_id: int, modality: str
) -> Path:
    """``{stitched_path}/mosaic_{mosaic_id:03d}_{modality}.nii.gz``"""
    return _mosaic_file_path(stitched_path, mosaic_id, f"{modality}.nii.gz")


def get_mosaic_tile_coords_export_path(
    stitched_path: str | Path, mosaic_id: int
) -> Path:
    """``{stitched_path}/mosaic_{mosaic_id:03d}_tile_coords_export.yaml``"""
    return _mosaic_file_path(stitched_path, mosaic_id, "tile_coords_export.yaml")


def get_mosaic_fiji_file_template(mosaic_id: int) -> str:
    """Fiji file template: ``mosaic_{mosaic_id:03d}_image_{iiii}_aip.nii``."""
    return f"{mosaic_prefix(mosaic_id)}_image_{{iiii}}_aip.nii"


# ---------------------------------------------------------------------------
# Acquisition semantics
# ---------------------------------------------------------------------------

_ACQ_LAYOUT: dict[int, dict[int, tuple[str, str, int]]] = {
    2: {
        1: ("normal", "normal", 1),
        2: ("tilted", "tilted", 2),
    },
    3: {
        1: ("normal", "normal", 1),
        2: ("tiltPos", "normal", 2),
        3: ("tiltNeg", "normal", 3),
    },
}


def acquisition_info_for_position(
    mosaics_per_slice: int,
    pos_in_slice: int,
) -> tuple[str, str, int]:
    """
    Resolve per-position acquisition semantics.

    Returns ``(acq, config_illumination, base_mosaic_id)``.
    """
    positions = _ACQ_LAYOUT.get(mosaics_per_slice)
    if positions is None or pos_in_slice not in positions:
        raise ValueError(
            f"Unsupported pos_in_slice={pos_in_slice} "
            f"for mosaics_per_slice={mosaics_per_slice}"
        )
    return positions[pos_in_slice]


# ---------------------------------------------------------------------------
# MosaicContext
# ---------------------------------------------------------------------------


class MosaicContext(BaseModel):
    """
    Resolved per-mosaic runtime context.

    - ``acquisition_label``: naming / template selection.
    - ``config_illumination``: config bucket for illumination-dependent fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slice_id: int
    mosaic_id: int
    acquisition_label: str
    config_illumination: str
    base_mosaic_id: int

    @computed_field
    @property
    def template_name(self) -> str:
        return f"tile_info_{self.acquisition_label}.j2"

    @computed_field
    @property
    def is_first_slice(self) -> bool:
        return self.slice_id == 1

    @computed_field
    @property
    def is_normal_config(self) -> bool:
        return self.config_illumination == "normal"

    @computed_field
    @property
    def is_tilted_config(self) -> bool:
        return self.config_illumination == "tilted"

    def grid_size_x(self, config: PSOCTScanConfigModel) -> int:
        return (
            config.acquisition.grid_size_x_normal
            if self.is_normal_config
            else config.acquisition.grid_size_x_tilted
        )

    def grid_size_y(self, config: PSOCTScanConfigModel) -> int:
        tilted = config.acquisition.grid_size_y_tilted
        if self.is_normal_config or tilted is None:
            return config.acquisition.grid_size_y
        return tilted

    def tile_size_x(self, config: PSOCTScanConfigModel) -> int:
        return (
            config.acquisition.tile_size_x_normal
            if self.is_normal_config
            else config.acquisition.tile_size_x_tilted
        )

    def tile_size_y(self, config: PSOCTScanConfigModel) -> int:
        return config.acquisition.tile_size_y

    def mask_threshold(self, config: PSOCTScanConfigModel) -> float:
        return (
            config.mask_threshold_normal
            if self.is_normal_config
            else config.mask_threshold_tilted
        )


def mosaic_context_from_ids(
    *,
    slice_id: int,
    mosaic_id: int,
    mosaics_per_slice: int,
) -> MosaicContext:
    """Build MosaicContext from resolved ids and layout size."""
    pos_in_slice = mosaic_position_in_slice(mosaic_id, mosaics_per_slice)
    acquisition_label, config_illumination, base_mosaic_id = (
        acquisition_info_for_position(mosaics_per_slice, pos_in_slice)
    )
    return MosaicContext(
        slice_id=slice_id,
        mosaic_id=mosaic_id,
        acquisition_label=acquisition_label,
        config_illumination=config_illumination,
        base_mosaic_id=base_mosaic_id,
    )


def mosaic_context_from_ident(
    mosaic_ident: OCTMosaicId,
    config: PSOCTScanConfigModel,
    *,
    validate_slice_consistency: bool = False,
) -> MosaicContext:
    """
    Build MosaicContext from canonical mosaic identity + project config.

    Parameters
    ----------
    validate_slice_consistency:
        If True, verify that mosaic_ident.slice_id matches the slice implied by
        mosaic_ident.mosaic_id and config.mosaics_per_slice.
    """
    if validate_slice_consistency:
        expected = slice_from_mosaic(
            mosaic_ident.mosaic_id, config.mosaics_per_slice
        )
        if expected != mosaic_ident.slice_id:
            raise ValueError(
                "Inconsistent OCTMosaicId: "
                f"slice_id={mosaic_ident.slice_id}, mosaic_id={mosaic_ident.mosaic_id}, "
                f"expected_slice_id={expected} for mosaics_per_slice={config.mosaics_per_slice}"
            )
    return mosaic_context_from_ids(
        slice_id=mosaic_ident.slice_id,
        mosaic_id=mosaic_ident.mosaic_id,
        mosaics_per_slice=config.mosaics_per_slice,
    )
