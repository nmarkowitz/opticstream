"""Build psoct_toolbox PipelineOpts from PSOCTScanConfigModel."""

from __future__ import annotations

import math
from typing import Optional

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
    """Extra flag consumed by OpticStream's bundled indexed batch wrappers."""

    save_volume_outputs: bool = True

    def to_matlab_struct(self):
        result = super().to_matlab_struct()
        result["SaveVolumeOutputs"] = self.save_volume_outputs
        return result


def build_pipeline_opts(
    config: PSOCTScanConfigModel,
    illumination: str = "normal",
    output_opts: Optional[OutputOpts] = None,
    opts_mat_file: Optional[str] = None,
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
    """
    acq = config.acquisition
    proc = config.processing

    aline_size = (
        acq.tile_size_x_normal if illumination == "normal" else acq.tile_size_x_tilted
    )
    spectral = SpectralOpts(
        disp_comp_file=str(proc.disp_comp_file),
        aline_size=aline_size,
        bline_size=acq.tile_size_y,
        is_raw_format=(acq.tile_saving_type == TileSavingType.SPECTRAL_12bit),
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
        **(output_opts.model_dump(exclude={"save_volume_outputs"}) if output_opts else {}),
        save_volume_outputs=proc.save_volume_outputs,
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
