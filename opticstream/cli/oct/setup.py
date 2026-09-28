"""
OCT setup: create or update the PSOCTScanConfig Prefect block for a project.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Literal

from prefect.blocks.system import Secret

from opticstream.cli.oct import oct_cli
from opticstream.cli.setup_common import default_zarr_config
from opticstream.config.psoct_scan_config import get_psoct_scan_config_block_name
from opticstream.config.psoct_scan_config import PSOCTScanConfig
from opticstream.state.oct_project_state import ensure_lock

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger(__name__)

_DEFAULT_MASK_NORMAL = 60.0
_DEFAULT_MASK_TILTED = 55.0


def parse_acq_mosaic_map(value: str) -> dict[str, int]:
    """
    Parse an acquisition -> mosaic slot map from the command line.

    Accepts ``normal=1,tilted=2``, ``normal:1,tilted:2`` or
    ``{normal: 1, tilted: 2}`` (quotes around labels are optional).
    """
    body = value.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    mapping: dict[str, int] = {}
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        sep = "=" if "=" in item else ":"
        label, _, slot = item.partition(sep)
        label = label.strip().strip("'\"")
        slot = slot.strip().strip("'\"")
        if not label or not slot:
            raise ValueError(
                f"Invalid acq-mosaic-map entry {item!r}; expected LABEL=SLOT"
            )
        try:
            mapping[label] = int(slot)
        except ValueError:
            raise ValueError(
                f"Invalid mosaic slot {slot!r} for acquisition {label!r}"
            ) from None
    if not mapping:
        raise ValueError("acq-mosaic-map is empty")
    return mapping


@oct_cli.command
def update_block() -> None:
    """
    Update the PSOCTScanConfig block.
    """
    PSOCTScanConfig.register_type_and_schema()


@oct_cli.command
def create_lock(
    project_name: str,
) -> None:
    """
    Create the OCT project state lock.
    """
    ensure_lock(project_name)


@oct_cli.command
def setup(
    project_name: str,
    *,
    project_base_path: Path | None = None,
    grid_size_x_normal: int = 1,
    grid_size_x_tilted: int = 1,
    grid_size_y: int = 1,
    dandi: str | None = None,
    dandi_path: Path | None = None,
    archive_path: Path | None = None,
    interpolation: Literal["wavelength", "phase_calibration"] | None = None,
    num_workers: int | None = None,
    linphase_file: Path | None = None,
    dspphase_file: Path | None = None,
    dispcomp_file: Path | None = None,
    dphase_file: Path | None = None,
    acq_mosaic_map: str | None = None,
    filename_pattern: str | None = None,
) -> None:
    """
    Create or update the PSOCTScanConfig block for a project.

    Run this before ``opticstream oct watch``. The block name is derived from
    ``project_name`` (e.g. ``myproject`` -> ``myproject-psoct-config``).

    Defaults keep CLI input minimal: grid sizes are ``1``; options left unset
    use the PSOCTScanConfig defaults.

    Parameters
    ----------
    dandi
        Name of the Prefect Secret block holding the DANDI API key.
    dandi_path
        Local dandiset path (``dandiset_path``).
    archive_path
        Archive root for raw tiles; unset disables archiving.
    interpolation
        Spectral interpolation method.
    num_workers
        MATLAB parallel pool size for spectral-to-processed batches.
    linphase_file
        .mat file with variable ``linPhase``.
    dspphase_file
        .mat file with variable ``dspPhase``; also enables
        ``phase_calibration_dispersion``.
    dispcomp_file
        Dispersion compensation file.
    dphase_file
        .mat file with variable ``dphase``.
    acq_mosaic_map
        Acquisition label -> mosaic slot, e.g. ``normal=1,tilted=2`` or
        ``"{normal: 1, tilted: 2}"``.
    filename_pattern
        Input filename template; unset keeps legacy ``mosaic_<M>_image_<N>_*``
        naming. Requires ``{image}``; optional ``{slice}``, ``{acquisition}``,
        ``{subject}``, ``{modality}``, ``{extension}``. Quote it in the shell,
        e.g. ``'spectral_{image}.nii'``.
    """
    update_block()
    ensure_lock(project_name)

    block_name = get_psoct_scan_config_block_name(project_name)

    if project_base_path is None:
        logger.warning("project_base_path is not set, please set it using prefect UI")

    acquisition: dict = {
        "grid_size_x_normal": grid_size_x_normal,
        "grid_size_x_tilted": grid_size_x_tilted,
        "grid_size_y": grid_size_y,
    }
    if acq_mosaic_map is not None:
        acquisition["acquisition_mosaic_map"] = parse_acq_mosaic_map(acq_mosaic_map)
    if filename_pattern:
        acquisition["filename_pattern"] = filename_pattern

    processing_overrides = {
        "interpolation_method": interpolation,
        "matlab_num_workers": num_workers,
        "lin_phase_file": linphase_file,
        "dsp_phase_file": dspphase_file,
        "disp_comp_file": dispcomp_file,
        "dphase_file": dphase_file,
    }
    processing = {k: v for k, v in processing_overrides.items() if v is not None}
    if dspphase_file is not None:
        processing["phase_calibration_dispersion"] = True

    scan_config = PSOCTScanConfig(
        project_name=project_name if project_name else Path("."),
        project_base_path=project_base_path if project_base_path else Path("."),
        acquisition=acquisition,
        processing=processing,
        mask_threshold_normal=_DEFAULT_MASK_NORMAL,
        mask_threshold_tilted=_DEFAULT_MASK_TILTED,
        dandiset_path=dandi_path,
        dandi_api_key=Secret.load(dandi) if dandi else None,
        archive_path=archive_path,
        zarr_config=default_zarr_config(),
    )
    # Prefect serializes the block through Pydantic, whose warning message is
    # multiline when list-annotated dependency defaults contain tuples.  Keep
    # the suppression local to this serialization operation so unrelated
    # Pydantic warnings remain visible.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"(?s).*PydanticSerializationUnexpectedValue.*",
            category=UserWarning,
        )
        scan_config.save(block_name, overwrite=True)
    logger.info("Saved PSOCTScanConfig block as '%s'", block_name)

    created: list[Path] = []
    verified: list[Path] = []

    def _ensure_dir(path: Path | str | None) -> None:
        if not path:
            return
        p = Path(path)
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(p)
        else:
            verified.append(p)

    _ensure_dir(scan_config.project_base_path)
    _ensure_dir(scan_config.archive_path)
    _ensure_dir(scan_config.dandiset_path)

    if created:
        print("Created directories:")
        for p in created:
            print(f"  - {p}")
    if verified:
        print("Verified existing directories:")
        for p in verified:
            print(f"  - {p}")
    if not created and not verified:
        print("No directories to create or verify from PSOCTScanConfig.")
