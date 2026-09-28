function corrections = loadSpectralCorrections(spectralOpts, sampleCount)
%LOADSPECTRALCORRECTIONS Read the calibration/dispersion files spectral2complex needs.
%
%   corrections = psoct.spectral.loadSpectralCorrections(spectralOpts, sampleCount)
%
%   Reads the phase calibration (dphase/linPhase[/dspPhase]) or the dispersion
%   compensation file for SAMPLECOUNT spectral samples. Batch wrappers call this
%   once and pass the result as spectralOpts.preloadedCorrections, so each tile
%   does not re-read the same files.
%
%   Output struct fields:
%     sampleCount      : Spectral sample count the corrections were read for.
%     calibration      : Phase calibration struct (empty struct in wavelength mode).
%     phaseCorrection1 : Channel 1 dispersion correction (sampleCount x 1).
%     phaseCorrection2 : Channel 2 dispersion correction (sampleCount x 1).
arguments
    spectralOpts struct
    sampleCount (1,1) double {mustBeInteger, mustBePositive}
end

spectralOpts = psoct.internal.opts.normalizeSpectralOpts(spectralOpts);
phaseMode = spectralOpts.interpolationMethod == "phase_calibration";

calibration = struct();
if phaseMode
    calibration = psoct.spectral.loadPhaseCalibration( ...
        spectralOpts.interpolationPath, sampleCount, spectralOpts.phaseCalibrationDispersion, ...
        spectralOpts.dphaseFile, spectralOpts.linPhaseFile, spectralOpts.dspPhaseFile);
end

% Read the channel-specific corrections from the two halves of one file.
if phaseMode && spectralOpts.phaseCalibrationDispersion && ~isempty(calibration.dispersion)
    phaseCorrection1 = calibration.dispersion;
    phaseCorrection2 = calibration.dispersion;
else
    [phaseCorrection1, phaseCorrection2] = ...
        opticstream_read_dispersion(string(spectralOpts.dispCompFile), sampleCount);
end

corrections = struct();
corrections.sampleCount = sampleCount;
corrections.calibration = calibration;
corrections.phaseCorrection1 = phaseCorrection1;
corrections.phaseCorrection2 = phaseCorrection2;
end
