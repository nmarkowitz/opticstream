OpticStream MATLAB overlay
=========================

The execution helper prepends this directory after loading psoct_toolbox.
The package override and interpolation helper are adapted from psoct-toolbox
commit 5182337044d2b7f8337ca205a33c9da3ddd69bda.

Spectral NIfTI layout: [spectral samples, two concatenated channel A-line
blocks, B-lines]. Header dimensions override AlineSize/BlineSize for NIfTI.
All spectral samples are retained, including nonmultiples of 1024.
Headerless packed raw retains the original 2048-sample geometry.

Dispersion formats:
- .txt/.csv/.tsv: one phase angle in radians per row, two real/imaginary
  columns, or one complex multiplier per row (e.g. 0.98+0.12i).
- Other extensions: native-endian binary float64 phase angles.

The file must contain exactly two finite coefficients per spectral sample:
the first half applies to channel 1 and the second half applies to channel 2.
Complex multipliers are applied directly; phase angles use exp(-1i*phase).
No coefficients are silently truncated, padded or recalibrated.

The legacy wavelength calibration and channel-2 reversal remain in use;
support for additional lengths does not validate that calibration for a
different scanner. Output depth is floor(samples/2)-24.

Orientation helpers (top level, unpackaged, from chaos_scientific_report_scripts):
- angle2tensor.m: orientation angle in degrees -> 4-component orientation tensor.
- tensor2angle.m: orientation tensor -> angle in radians.
psoct.registration.thruplane_registration calls both, unqualified, to warp
orientation maps; the toolbox does not ship them.

Complex outputs (processing.save_complex_outputs):
- The indexed spectral batch wrapper reads OutputOpts.ComplexOutputDir
  (+psoct/+file/+internal/applyComplexOutputPolicy.m) and sets Paths.complex to
  <dir>/<prefix>_complex.nii, so spectral2complex saves the volume it already computes.
- The file is single, [4*A-lines, B-lines, depth]: real(J1), imag(J1), real(J2),
  imag(J2) stacked along the first axis. Its header is resized from the spectral
  input's (default 10x10x2.5 um voxels) because that header's size differs.
