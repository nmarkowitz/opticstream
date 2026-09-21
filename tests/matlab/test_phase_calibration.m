function test_phase_calibration
folder = tempname;
mkdir(folder);
cleanup = onCleanup(@() rmdir(folder, 's'));
N = 1152;
dphase = linspace(0, 1, N).^1.3; % Nonuniform measured phase, row vector
linPhase = linspace(0, 1, N)';
dspPhase = linspace(0, 0.2, N);
save(fullfile(folder, 'dphase.mat'), 'dphase');
save(fullfile(folder, 'linPhase.mat'), 'linPhase');
save(fullfile(folder, 'dspPhase.mat'), 'dspPhase');
cal = psoct.spectral.loadPhaseCalibration(folder, N, true);
assert(isequal(size(cal.dphase), [N 1]));
assert(max(abs(cal.dispersion - exp(-1i*dspPhase(:)))) < 1e-12);
save(fullfile(folder, 'custom-d.mat'), 'dphase');
save(fullfile(folder, 'custom-l.mat'), 'linPhase');
save(fullfile(folder, 'custom-s.mat'), 'dspPhase');
explicit = psoct.spectral.loadPhaseCalibration("", N, true, ...
    fullfile(folder, 'custom-d.mat'), fullfile(folder, 'custom-l.mat'), fullfile(folder, 'custom-s.mat'));
assert(isequal(cal, explicit));
mixed = psoct.spectral.loadPhaseCalibration(folder, N, true, fullfile(folder, 'custom-d.mat'));
assert(isequal(cal, mixed));
defaults = psoct.internal.opts.normalizeSpectralOpts(struct());
assert(defaults.flipChannel2Spectra);
for reversed = [false true]
    rng(42);
    data = int16(randi([-1000 1000], N, 8, 2));
    input = fullfile(folder, 'input.nii');
    niftiwrite(data, input);
    opts = struct('interpolationMethod', 'phase_calibration', 'interpolationPath', folder, ...
        'phaseCalibrationDispersion', true, 'flipChannel2Spectra', reversed);
    if reversed
        opts.interpolationPath = "";
        opts.dphaseFile = fullfile(folder, 'custom-d.mat');
        opts.linPhaseFile = fullfile(folder, 'custom-l.mat');
        opts.dspPhaseFile = fullfile(folder, 'custom-s.mat');
    end
    [actual1, actual2] = psoct.spectral.spectral2complex(input, opts, struct());
    expected = complex(zeros(4, 2, N/2-24, 2));
    for b = 1:2
        for channel = 1:2
            buffer = double(data(:, (channel-1)*4+(1:4), b));
            if channel == 2 && reversed, buffer = flipud(buffer); end
            buffer = buffer - mean(buffer, 2);
            buffer = interp1(dphase(:), buffer, linPhase(:), 'linear', 'extrap');
            buffer = buffer - median(buffer, 2);
            buffer = buffer .* exp(-1i*dspPhase(:));
            depth = fft(buffer);
            % Existing toolbox uses a conjugate transpose when storing Jones data.
            expected(:,b,:,channel) = depth(25:N/2,:)';
        end
    end
    delta = abs(actual1 - expected(:,:,:,1)); assert(max(delta(:)) < 1e-8);
    delta = abs(actual2 - expected(:,:,:,2)); assert(max(delta(:)) < 1e-8);
end
% Invalid vectors fail explicitly.
dphase(2) = dphase(1);
save(fullfile(folder, 'dphase.mat'), 'dphase');
rejected = false;
try
    psoct.spectral.loadPhaseCalibration(folder, N, false);
catch ME
    rejected = strcmp(ME.identifier, 'psoct:calibration:NonMonotonic');
end
assert(rejected);
disp('Phase calibration tests passed');
end
