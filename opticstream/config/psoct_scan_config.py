"""
Prefect Blocks for project configuration.

This module defines custom Prefect Blocks for managing project-level configuration.
Blocks provide typed configuration schemas with validation and UI management.

See: https://docs.prefect.io/v3/concepts/blocks
"""

from typing import Any, Dict, List, Literal, Optional
from enum import Enum
from pathlib import Path

from prefect.blocks.core import Block
from prefect.blocks.system import Secret
from pydantic import BaseModel, ConfigDict, Field, field_validator

from niizarr import ZarrConfig
import psoct_toolbox
from opticstream.config.utils import with_positions
from opticstream.config.enface_preview import EnfacePreviewConfig
from opticstream.utils.naming_convention import normalize_project_name
    

class TileSavingType(str, Enum):
    """Enumeration of tile saving types."""

    COMPLEX = "complex"
    SPECTRAL = "spectral"
    SPECTRAL_12bit = "spectral_12bit"
    COMPLEX_WITH_SPECTRAL = "complex_with_spectral"
    PROCESSED_WITH_SPECTRAL = "processed_with_spectral"


class EnfaceModality(str, Enum):
    """Enumeration of enface modalities."""

    AIP = "aip"
    MIP = "mip"
    RET = "ret"
    ORI = "ori"
    BIREF = "biref"
    SURF = "surf"
    MUS = "mus"


class VolumeModality(str, Enum):
    """Enumeration of volume modalities."""

    DBI = "dBI"
    R3D = "R3D"
    O3D = "O3D"


@with_positions
class PSOCTAcquisitionParams(BaseModel):
    """Physical / hardware facts about the acquisition."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    filename_pattern: Optional[str] = Field(
        default=None,
        description=(
            "Optional full input filename template; null preserves legacy naming. "
            "Example: sub-{subject}_sample-slice{slice}_chunk-{image}_acq-{acquisition}_{modality}{extension}. "
            "slice037/chunk-0041/acq-normal0deg means slice 37, image 41, and the mosaic slot mapped from normal0deg. "
            "Required placeholders: {slice}, {image}, {acquisition}; optional: {subject}, {modality}, {extension}. "
            "Numbers accept zero padding; do not use :04d format specifiers. Literals and labels are case-sensitive. "
            "Suffixes/extensions may be literals or placeholders; matching an extension does not add support for its file format."
        ),
    )
    acquisition_mosaic_map: Dict[str, int] = Field(
        default_factory=lambda: {"normal0deg": 1, "tilted15deg": 2},
        description="Exact acquisition labels mapped to 1-based mosaic slots within each slice. Edit angles freely, e.g. normal5deg: 1. Slot 1 is normal; remaining slots follow the project's existing mosaic ordering. Source mosaic = (slice - 1) * mosaics_per_slice + slot.",
    )
    filename_default_modality: str = Field(
        default="spectral", description="Modality label when filename_pattern omits {modality}, including patterns with a literal suffix.",
    )
    filename_modality_map: Dict[str, Literal["spectral", "complex", "processed", "aip", "mip", "ori", "ret", "surf", "dbi"]] = Field(
        default_factory=lambda: {key: key for key in ("spectral", "complex", "processed", "aip", "mip", "ori", "ret", "surf", "dbi")},
        description="Map filename modality labels to processing types; e.g. rawscan: spectral. Unknown labels are ignored. No fixed _spectral.nii suffix is required.",
    )

    @field_validator("filename_pattern")
    @classmethod
    def validate_filename_pattern(cls, value):
        if value is not None:
            from opticstream.utils.oct_input_naming import compile_pattern
            compile_pattern(value)
        return value

    @field_validator("acquisition_mosaic_map")
    @classmethod
    def validate_acquisition_mosaic_map(cls, value):
        if any(not label or slot < 1 for label, slot in value.items()):
            raise ValueError("Acquisition labels must be nonempty and mosaic slots positive.")
        return value
    grid_size_x_normal: int = Field(
        ...,
        ge=1,
        description=(
            "Number of batches (columns) per mosaic for normal illumination (required)"
        ),
    )
    grid_size_x_tilted: int = Field(
        ...,
        ge=1,
        description=(
            "Number of batches (columns) per mosaic for tilted illumination (required)"
        ),
    )
    grid_size_y: int = Field(
        ...,
        ge=1,
        description="Number of tiles per batch (rows) - determines batch size (required)",
    )

    tile_size_x_normal: int = Field(
        default=350,
        ge=1,
        description=(
            "Number of pixels in the x-direction for normal illumination tiles"
        ),
    )
    tile_size_x_tilted: int = Field(
        default=200,
        ge=1,
        description=(
            "Number of pixels in the x-direction for tilted illumination tiles"
        ),
    )
    tile_size_y: int = Field(
        default=350,
        ge=1,
        description="Number of pixels in the y-direction for tiles",
    )
    tile_overlap: float = Field(
        default=20.0,
        ge=0.0,
        description="Overlap between tiles in percentage (default: 20.0)",
    )
    scan_resolution_3d: List[float] = Field(
        (
            0.01,
            0.01,
            0.0025,
        ),  # Intentional default value, default factory does not work with prefect blocks UI
        description=(
            "Scan resolution for 3D volumes [x, y, z] in millimeters "
            "(default: [0.01, 0.01, 0.0025])"
        ),
    )
    tile_saving_type: TileSavingType = Field(
        default=TileSavingType.SPECTRAL,
        description="Type of tile saving format",
    )
    wavelength_um: float = Field(
        default=1.3,
        gt=0.0,
        description="Center wavelength in microns",
    )
    slice_thickness_um: float = Field(
        default=500.0,
        gt=0.0,
        description="Physical slice thickness in microns",
    )

    @field_validator("scan_resolution_3d")
    @classmethod
    def validate_scan_resolution_3d(cls, value: List[float]) -> List[float]:
        if len(value) != 3:
            raise ValueError(
                "scan_resolution_3d must contain exactly 3 values [x, y, z]"
            )
        if any(v <= 0 for v in value):
            raise ValueError("scan_resolution_3d values must be > 0")
        return value


class PSOCTProcessingParams(BaseModel):
    """MATLAB processing parameters that are project-level constants."""

    interpolation_method: Literal["wavelength", "phase_calibration"] = Field(
        default="wavelength", description="wavelength preserves the existing wave-pixel calibration. phase_calibration interpolates from dphase onto linPhase loaded from interpolation_path.",
    )
    interpolation_path: Path | None = Field(
        default=None, description="Calibration directory containing dphase.mat (variable dphase) and linPhase.mat (variable linPhase). Both must be real, finite vectors matching the spectral sample count; linPhase must be uniformly spaced.",
    )
    dphase_file: Path | None = Field(
        default=None, description="Path to the .mat file containing variable dphase. Overrides interpolation_path/dphase.mat. Real finite vector matching the spectral sample count.",
    )
    lin_phase_file: Path | None = Field(
        default=None, description="Path to the .mat file containing variable linPhase. Overrides interpolation_path/linPhase.mat. Uniformly spaced real finite vector matching the spectral sample count.",
    )
    dsp_phase_file: Path | None = Field(
        default=None, description="Path to the .mat file containing variable dspPhase (radians). Overrides interpolation_path/dspPhase.mat. Used only when phase_calibration_dispersion is true; vector length must match the spectral sample count.",
    )
    phase_calibration_dispersion: bool = Field(
        default=False, description="In phase_calibration mode, use dspPhase.mat (variable dspPhase, radians) from interpolation_path for both channels instead of disp_comp_file. False preserves the existing channel-specific dispersion correction.",
    )
    flip_channel2_spectra: bool | None = Field(
        default=None, description="Reverse channel 2 along the spectral axis before interpolation. null selects true for wavelength mode (legacy behavior), false for phase_calibration. Calibration vectors must describe the selected sample ordering.",
    )

    save_volume_outputs: bool = Field(
        default=True,
        description=("Save per-tile dBI, R3D and O3D volume files. Disable for enface-only "
                     "disk output; intermediate volumes are still computed in memory. "
                     "Also skips volume validation, mosaic volume stitching and volume uploads. "
                     "Existing files are not deleted; raw archives and enface workflows are unaffected."),
    )

    disp_comp_file: Path | None = Field(
        default=None,
        description=(
            "Path to dispersion compensation file"
            "if None, MATLAB toolbox default is used"
        ),
    )

    enface_offset: int = Field(
        default=0,
        ge=0,
        description="Offset from the surface for enface computation",
    )
    enface_depth: int = Field(
        default=70,
        ge=0,
        description="Number of pixels below the surface for enface computation",
    )
    ori_method: str = Field(
        default="circularMean",
        description=("Orientation estimation method"),
    )
    ori_method_args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra args for orientation method",
    )
    biref_method: str = Field(
        default="legacy",
        description="Birefringence method",
    )
    biref_method_args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra args for birefringence method",
    )
    save_enface_2d_as_3d: bool = Field(
        default=True,
        description="Save 2D enface outputs as 3D Volume with third dimension = 1",
    )

    flip_phase: bool = Field(
        default=False,
        description="Flip phase sign convention when converting from complex to volume",
    )
    phase_offset_deg: float = Field(
        default=100.0,
        description="Phase offset in degrees before conversion to radians for MATLAB ",
    )
    flip_z: bool = Field(
        default=True,
        description="Flip z-axis ordering when converting from complex to volume",
    )

    surface_spec: Optional[str] = Field(
        default="gradient",
        description="Surface extraction spec string",
    )
    matlab_num_workers: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional MATLAB parallel worker count for spectral-to-processed batch. "
            "If None, MATLAB default pool size is used."
        ),
    )
    matlab_pool_type: Literal["process", "thread"] = Field(
        default="process",
        description="MATLAB pool type for spectral-to-processed batch execution.",
    )


@with_positions
class PSOCTScanConfigModel(BaseModel):
    """
    Project-level configuration block.

    Stores all project-specific parameters needed for processing workflows.
    Block instances should be saved with name: "{project_name}-config"

    Please repect the validation rules and constraints defined in the models, some validation rules are not enforced by the Prefect blocks UI but will be enforced by
    """

    model_config = ConfigDict(
        validate_assignment=True, revalidate_instances="always"
    )

    project_name: str = Field(
        ...,
        min_length=1,
        description="Unique project identifier, follow BIDS naming convention if possible with all common fields (e.g. sub-\<subject_id\>_ses-\<session_id\>)",
    )
    project_base_path: Path = Field(
        ...,
        description="Base filesystem path for project data. All intermediate and output files should be stored under this path.",
    )

    matlab_processing_enabled: bool = Field(
        default=True,
        description=(
            "Enable MATLAB-based tile-batch processing and slice registration for this block. "
            "When false, these flows skip before processing, archiving within the tile flow, "
            "or updating processing milestones. Standalone archive/upload and non-MATLAB "
            "flows are unaffected. Does not stop already-running MATLAB sessions."
        ),
    )

    mosaics_per_slice: Literal[2, 3] = Field(
        default=2,
        ge=1,
        description="Number of mosaics, use 2 for human systems with normal and tilted illuminations, use 3 for macaque systems",
    )

    acquisition: PSOCTAcquisitionParams = Field(
        ...,
        description="Physical and hardware facts about the acquisition",
    )
    processing: PSOCTProcessingParams = Field(
        default_factory=PSOCTProcessingParams,
        description="Project-level MATLAB processing options",
    )

    enface_preview: EnfacePreviewConfig = Field(
        default_factory=EnfacePreviewConfig,
        description="Acquisition-produced AIP/MIP/orientation/retardance previews for watch-enface; independent of MATLAB and spectral processing state.",
    )

    mask_threshold_normal: float = Field(
        default=60.0,
        ge=0.0,
        description="Threshold for mask generation and coordinate processing for normal illumination (default: 60.0)",
    )
    mask_threshold_tilted: float = Field(
        default=55.0,
        ge=0.0,
        description="Threshold for mask generation and coordinate processing for tilted illumination (default: 55.0)",
    )

    dandiset_path: Path | None = Field(
        default=None,
        description="Root path of the target DANDI dataset for upload, if not set, output will not be uploaded to DANDI",
    )
    dandi_api_key: Secret | None = Field(
        default=None,
        description=(
            "Prefect Secret block containing the DANDI/LINC API key. "
            "If not set, the uploader uses its instance-specific default Secret block."
        ),
    )
    archive_path: Path | None = Field(
        default=None,
        description="Archive root path where raw tiles will be archived to, if not set, raw tiles will not be archived",
    )
    archive_tile_name_format: str = Field(
        default=(
            "{project_name}_sample-slice{slice_id:02d}_chunk-{tile_id:04d}_acq-{acq}_OCT.nii.gz"
        ),
        description=(
            "Filename template for archived tile outputs "
            "(supports placeholders: project_name, slice_id, tile_id, acq)"
        ),
    )
    mosaic_volume_format: str = Field(
        default=(
            "{project_name}_sample-slice{slice_id:02d}_acq-{acq}_proc-{modality}_OCT.ome.zarr"
        ),
        description=(
            "Filename template for stitched 3D volume outputs "
            "(supports placeholders: project_name, slice_id, acq, modality)"
        ),
    )
    mosaic_enface_format: str = Field(
        default=(
            "{project_name}_sample-slice{slice_id:02d}_acq-{acq}_proc-{modality}_OCT.nii.gz"
        ),
        description=(
            "Filename template for stitched enface outputs "
            "(supports placeholders: project_name, slice_id, acq, modality)"
        ),
    )
    mosaic_mask_format: str = Field(
        default=("{project_name}_sample-slice{slice_id:03d}_acq-{acq}_OCT_mask.nii.gz"),
        description=(
            "Filename template for stitched mask outputs "
            "(supports placeholders: project_name, slice_id, acq)"
        ),
    )
    slice_registered_format: str = Field(
        default=("{project_name}_sample-slice{slice_id:03d}_proc-3daxis_OCT.nii.gz"),
        description=(
            "Filename template for slice-registered axis outputs "
            "(supports placeholders: project_name, slice_id)"
        ),
    )

    enface_modalities: List[EnfaceModality] = Field(
        default=[
            EnfaceModality.RET,
            EnfaceModality.ORI,
            EnfaceModality.BIREF,
            EnfaceModality.MIP,
            EnfaceModality.SURF,
            EnfaceModality.AIP,
        ],
        description="Enface modalities to computed, exported, and stitched, aip is always included",
    )
    volume_modalities: List[VolumeModality] = Field(
        default=[VolumeModality.DBI, VolumeModality.R3D, VolumeModality.O3D],
        description="3D volume modalities to exported",
    )

    stitch_3d_volumes: bool = Field(
        default=True,
        description="Enable stitching of 3D volume outputs",
    )

    crop_focus_plane_depth: int = Field(
        default=500,
        ge=1,
        description="Focus-plane crop depth for 3D volume stitching (default: 500)",
    )
    crop_focus_plane_offset: int = Field(
        default=30,
        ge=0,
        description="Focus-plane crop offset for 3D volume stitching (default: 30)",
    )
    matlab_root: Path | None = Field(
        default_factory=psoct_toolbox.get_matlab_root,
        description="MATLAB installation root used by psoct_toolbox execution",
    )
    zarr_config: ZarrConfig = Field(
        ...,
        description="OME-Zarr writer configuration for stitched outputs",
    )


class PSOCTScanConfig(PSOCTScanConfigModel, Block):
    """
    Project-level configuration block.

    Stores all project-specific parameters needed for processing workflows.
    Block instances should be saved with name: "{project_name}-config"
    """

def get_psoct_scan_config_block_name(project_name: str) -> str:
    return f"{normalize_project_name(project_name)}-psoct-config"

def get_psoct_scan_config(
    project_name: str, override_config_name: Optional[str] = None
) -> PSOCTScanConfig:
    """
    Get the scan configuration for a project.
    """
    return PSOCTScanConfig.load(
        override_config_name or f"{normalize_project_name(project_name)}-psoct-config"
    )
