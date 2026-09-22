function calibration = loadPhaseCalibration(folder, sampleCount, useDispersion, dphaseFile, linPhaseFile, dspPhaseFile)
% Shared calibration for both channels, in their selected spectral ordering.
arguments
    folder (1,1) string
    sampleCount (1,1) double {mustBeInteger, mustBePositive}
    useDispersion (1,1) logical = false
    dphaseFile (1,1) string = ""
    linPhaseFile (1,1) string = ""
    dspPhaseFile (1,1) string = ""
end
calibration.dphase = readVector(folder, 'dphase', sampleCount, dphaseFile);
calibration.linPhase = readVector(folder, 'linPhase', sampleCount, linPhaseFile);
delta = diff(calibration.dphase);
if ~(all(delta > 0) || all(delta < 0))
    error('psoct:calibration:NonMonotonic', 'dphase must be strictly monotonic with no repeated samples.');
end
delta = diff(calibration.linPhase);
if ~(all(delta > 0) || all(delta < 0))
    error('psoct:calibration:NonMonotonic', 'linPhase must be strictly monotonic.');
end
tolerance = max(abs(mean(delta))*1e-5, 100*eps(max(abs(calibration.linPhase))));
if any(abs(delta - mean(delta)) > tolerance)
    error('psoct:calibration:NonUniform', 'linPhase must be uniformly spaced.');
end
if min(calibration.linPhase) < min(calibration.dphase) || max(calibration.linPhase) > max(calibration.dphase)
    warning('psoct:calibration:Extrapolation', 'linPhase extends beyond dphase; linear extrapolation will be used.');
end
calibration.dispersion = [];
if useDispersion
    dspPhase = readVector(folder, 'dspPhase', sampleCount, dspPhaseFile);
    calibration.dispersion = exp(-1i .* dspPhase);
end
end

function vector = readVector(folder, name, sampleCount, filename)
if strlength(filename) == 0
    if strlength(folder) == 0
        error('psoct:calibration:MissingPath', 'Specify a file path for %s or a calibration directory.', name);
    end
    filename = fullfile(folder, [name '.mat']);
end
if ~isfile(filename)
    error('psoct:calibration:MissingFile', 'Calibration file not found: %s', filename);
end
loaded = load(filename, name);
if ~isfield(loaded, name)
    error('psoct:calibration:MissingVariable', '%s must contain variable %s.', filename, name);
end
vector = loaded.(name);
if ~isnumeric(vector) || ~isreal(vector) || ~isvector(vector) || numel(vector) ~= sampleCount || any(~isfinite(vector(:)))
    error('psoct:calibration:InvalidVector', '%s must contain a real finite vector of %d samples.', name, sampleCount);
end
vector = double(vector(:));
end
