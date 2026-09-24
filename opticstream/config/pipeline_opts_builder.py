"""Build psoct_toolbox PipelineOpts from PSOCTScanConfigModel."""

from __future__ import annotations

import math
from typing import Optional
from pydantic import Field

from prefect import get_run_logger
from psoct_toolbox.opts_models import (
    AcquisitionOpts,
    EnfaceComputeFlags,
    EnfaceOpts,
    OutputOpts,
    PipelineOpts,
    SpectralOpts,
    SurfaceOpts,
    VolumeOpts,
)

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel, TileSavingType


class VolumeOutputOpts(OutputOpts):
    """Extra fields consumed by OpticStream's bundled indexed batch wrappers."""

    save_volume_outputs: bool = True
    # Directory for per-tile complex volumes (<prefix>_complex.nii); None keeps them in memory.
    complex_output_dir: Optional[str] = None

    def to_matlab_struct(self):
        result = super().to_matlab_struct()
        result["SaveVolumeOutputs"] = self.save_volume_outputs
        if self.complex_output_dir:
            result["ComplexOutputDir"] = self.complex_output_dir
        return result


class CalibratedSpectralOpts(SpectralOpts):
    """OpticStream extensions serialized by the existing toolbox bridge."""

    interpolation_method: str = Field(default="wavelength", alias="interpolationMethod")
    interpolation_path: Optional[str] = Field(default=None, alias="interpolationPath")
    dphase_file: Optional[str] = Field(default=None, alias="dphaseFile")
    lin_phase_file: Optional[str] = Field(default=None, alias="linPhaseFile")
    dsp_phase_file: Optional[str] = Field(default=None, alias="dspPhaseFile")
    phase_calibration_dispersion: bool = Field(default=False, alias="phaseCalibrationDispersion")
    flip_channel2_spectra: bool = Field(default=True, alias="flipChannel2Spectra")


def build_pipeline_opts(
    config: PSOCTScanConfigModel,
    illumination: str = "normal",
    output_opts: Optional[OutputOpts] = None,
    opts_mat_file: Optional[str] = None,
    complex_output_dir: Optional[str] = None,
) -> PipelineOpts:
    """
    Assemble MATLAB-facing PipelineOpts from project scan config.

    Parameters
    ----------
    config
        PS-OCT scan configuration (block model).
    illumination
        ``"normal"`` uses ``tile_size_x_normal``; otherwise tilted A-line size.
    output_opts
        Per-run output paths; omit to let MATLAB fill defaults.
    opts_mat_file
        Optional path to opts .mat cache.
    complex_output_dir
        Save each tile's complex volume here (spectral input only).
    """
    acq = config.acquisition
    proc = config.processing

    aline_size = (
        acq.tile_size_x_normal if illumination == "normal" else acq.tile_size_x_tilted
    )
    if proc.interpolation_method == "phase_calibration" and proc.interpolation_path is None:
        if proc.dphase_file is None or proc.lin_phase_file is None:
            raise ValueError("phase_calibration requires dphase_file and lin_phase_file, or interpolation_path")
        if proc.phase_calibration_dispersion and proc.dsp_phase_file is None:
            raise ValueError("phase_calibration_dispersion requires dsp_phase_file or interpolation_path")
    if proc.phase_calibration_dispersion and proc.interpolation_method != "phase_calibration":
        raise ValueError("phase_calibration_dispersion requires phase_calibration interpolation")
    spectral = CalibratedSpectralOpts(
        disp_comp_file=str(proc.disp_comp_file) if proc.disp_comp_file is not None else None,
        aline_size=aline_size,
        bline_size=acq.tile_size_y,
        is_raw_format=(acq.tile_saving_type == TileSavingType.SPECTRAL_12bit),
        interpolation_method=proc.interpolation_method,
        interpolation_path=str(proc.interpolation_path) if proc.interpolation_path is not None else None,
        dphase_file=str(proc.dphase_file) if proc.dphase_file is not None else None,
        lin_phase_file=str(proc.lin_phase_file) if proc.lin_phase_file is not None else None,
        dsp_phase_file=str(proc.dsp_phase_file) if proc.dsp_phase_file is not None else None,
        phase_calibration_dispersion=proc.phase_calibration_dispersion,
        flip_channel2_spectra=(proc.flip_channel2_spectra if proc.flip_channel2_spectra is not None
                               else proc.interpolation_method == "wavelength"),
    )

    compute = EnfaceComputeFlags(
        **{
            mod: (mod in config.enface_modalities)
            for mod in ["aip", "mip", "ret", "ori", "biref"]
        }
    )
    enface = EnfaceOpts(
        offset=proc.enface_offset,
        depth=proc.enface_depth,
        ori_method=proc.ori_method,  # type: ignore[arg-type]
        ori_method_args=proc.ori_method_args,
        biref_method=proc.biref_method,
        biref_method_args=proc.biref_method_args,
        compute=compute,
        save_2d_as_3d=proc.save_enface_2d_as_3d,
    )

    acquisition_opts = AcquisitionOpts(
        pixel_dimensions_um=[v * 1000 for v in acq.scan_resolution_3d],
        wavelength_um=acq.wavelength_um,
        slice_thickness_um=acq.slice_thickness_um,
    )

    volume = VolumeOpts(
        flip_phase=proc.flip_phase,
        phase_offset=math.radians(proc.phase_offset_deg),
        flip_z=proc.flip_z,
    )

    surface = SurfaceOpts(spec=proc.surface_spec)

    output_opts = VolumeOutputOpts(
        **(output_opts.model_dump(exclude={"save_volume_outputs", "complex_output_dir"})
           if output_opts else {}),
        save_volume_outputs=proc.save_volume_outputs,
        complex_output_dir=complex_output_dir,
    )

    return PipelineOpts(
        spectral=spectral,
        surface=surface,
        enface=enface,
        acquisition=acquisition_opts,
        output=output_opts,
        volume=volume,
        opts_mat_file=opts_mat_file,
    )
