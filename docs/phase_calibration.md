# Phase-calibrated spectral interpolation

PSOCTScanConfig now exposes these per-project `processing` settings:

| Field | Default | Meaning |
| --- | --- | --- |
| interpolation_method | wavelength | Existing wavelength calibration, or phase_calibration |
| interpolation_path | null | Directory containing dphase.mat and linPhase.mat |
| phase_calibration_dispersion | false | Also load dspPhase.mat instead of disp_comp_file |
| flip_channel2_spectra | null | Automatic: true for wavelength, false for phase_calibration. Set explicitly to override. |

MAT files must contain variables with the matching names: `dphase`, `linPhase`,
and optionally `dspPhase`. Each must be a real finite vector with one entry per
acquired spectral sample (1152 entries for 1152-sample input). Row and column
vectors are accepted. dphase must be strictly monotonic; linPhase must be strictly
monotonic and uniformly spaced. The target length currently must equal the source
length. Extrapolation is permitted and produces a warning.

The new interpolation is applied to all A-lines in each B-line:

```matlab
interp1(dphase(:), backgroundSubtractedBuffer, linPhase(:), 'linear', 'extrap')
```

It bypasses wave-pixel.mat and the legacy ZeroPadBuffer helper. Both channels use
the same phase coordinates. They must describe the data ordering after the optional
channel-2 reversal. This implementation does not assume that the shared calibration
is valid for both detectors; verify that against your calibration procedure.

When phase_calibration_dispersion is true, dspPhase must contain phase angles in
radians; both channels receive `exp(-1i*dspPhase(:))`. Otherwise the existing
disp_comp_file reader and its separate channel corrections remain in use. Ensure
those coefficients match the new target sampling grid.

This changes interpolation, not the entire reconstruction algorithm. Reference
subtraction across A-lines, post-interpolation median subtraction, the 24-bin
autocorrelation crop, and existing Jones storage conventions remain unchanged.
The output depth for 1152 samples remains 552. It does not reproduce the supplied
script's B-line reference averaging or zero-bin crop.

## Enable for a project

Stop the watcher and serve processes. Register the schema using the project's
virtual environment and your existing PREFECT_API_URL:

```bat
".venv\Scripts\opticstream.exe" oct update-block
```

For an existing block, use this in a notebook connected to the same Prefect server:

```python
from pathlib import Path
from opticstream.config.psoct_scan_config import PSOCTScanConfig

block = await PSOCTScanConfig.load("human-10um-psoct-config")
block.processing.interpolation_method = "phase_calibration"
block.processing.interpolation_path = Path(r"D:\data\calibration")
block.processing.phase_calibration_dispersion = True  # Use your dspPhase.mat
block.processing.flip_channel2_spectra = False         # Original acquisition order
await block.save("human-10um-psoct-config", overwrite=True)
```

Refresh the dashboard and restart watcher/serve processes. Calibration files must
be readable on the machine executing MATLAB. Existing completed batches are not
automatically reprocessed. Set interpolation_method back to wavelength and
phase_calibration_dispersion to false to restore the old route; use null for the
automatic channel-order selection.

All MATLAB changes are bundled beneath `opticstream/matlab/+psoct`; no installed
toolbox files are modified. For direct MATLAB use, add the installed toolbox root,
then add `opticstream/matlab` with `'-begin'`, followed by `clear functions; rehash`.
# Individual calibration files

Under the block's `processing` settings, set `dphase_file`, `lin_phase_file`,
and optionally `dsp_phase_file` to full paths accessible to the MATLAB machine.
Each explicit path overrides the corresponding file in `interpolation_path`.
The directory can be omitted when all required individual paths are supplied.
Filenames may differ, but the variables inside must remain `dphase`, `linPhase`,
and `dspPhase`, respectively. Set `interpolation_method="phase_calibration"`;
`dsp_phase_file` is used only with `phase_calibration_dispersion=true`.
Existing directory-based configurations remain supported.
