function spectralOpts = normalizeSpectralOpts(spectralOpts)
% OpticStream spectral options; legacy defaults remain unchanged.
nargs = namedargs2cell(spectralOpts);
spectralOpts = iNormalizeSpectralOpts(nargs{:});
if isempty(spectralOpts.flipChannel2Spectra)
    spectralOpts.flipChannel2Spectra = spectralOpts.interpolationMethod == "wavelength";
end
if spectralOpts.interpolationMethod == "phase_calibration" && strlength(spectralOpts.interpolationPath) == 0
    if strlength(spectralOpts.dphaseFile) == 0 || strlength(spectralOpts.linPhaseFile) == 0
        error('psoct:calibration:MissingPath', 'Specify dphaseFile and linPhaseFile, or interpolationPath.');
    end
    if spectralOpts.phaseCalibrationDispersion && strlength(spectralOpts.dspPhaseFile) == 0
        error('psoct:calibration:MissingPath', 'Specify dspPhaseFile or interpolationPath for phase dispersion.');
    end
end
if spectralOpts.phaseCalibrationDispersion && spectralOpts.interpolationMethod ~= "phase_calibration"
    error('psoct:calibration:InvalidMode', 'phaseCalibrationDispersion requires phase_calibration.');
end
end

function nargs = iNormalizeSpectralOpts(nargs)
arguments
    nargs.dispCompFile {mustBeTextScalar} = psoct.internal.getDataFile("LSM03_mineral_oil_placecorrectionmeanall2.dat")
    nargs.AlineSize (1,1) int32 {mustBeInteger, mustBePositive} = 200
    nargs.BlineSize (1,1) int32 {mustBeInteger, mustBePositive} = 350
    nargs.isRawFormat (1,1) logical = false
    nargs.interpolationMethod (1,1) string {mustBeMember(nargs.interpolationMethod, ["wavelength", "phase_calibration"])} = "wavelength"
    nargs.interpolationPath (1,1) string = ""
    nargs.dphaseFile (1,1) string = ""
    nargs.linPhaseFile (1,1) string = ""
    nargs.dspPhaseFile (1,1) string = ""
    nargs.phaseCalibrationDispersion (1,1) logical = false
    nargs.flipChannel2Spectra logical = []
end
if ~isempty(nargs.flipChannel2Spectra) && ~isscalar(nargs.flipChannel2Spectra)
    error('psoct:calibration:InvalidFlip', 'flipChannel2Spectra must be a scalar logical.');
end
end
