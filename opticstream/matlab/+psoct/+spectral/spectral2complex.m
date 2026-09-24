function [Jones1_3D, Jones2_3D] = spectral2complex(spectralFile, spectralOpts, outputOpts)
%SPECTRAL2COMPLEX Convert spectral data to complex data.
%
%   [Jones1_3D, Jones2_3D] = spectral2complex(spectralFile, spectralOpts)
%
%   Input
%     spectralFile : Path to the spectral data file.
%     spectralOpts : Struct with fields:
%                    - dispCompFile: Path to the dispersion compensation file.
%                    - AlineSize   : Size of the A-line (pixels of X axis).
%                    - BlineSize   : Size of the B-line (pixels of Y axis).
%                    - isRawFormat : Input is headerless packed 12-bit data
%                                    (read into local variable is12bit).
%     outputOpts   : Struct with fields:
%                    - Paths       : Struct with fields:
%                      - complex : Path to the output complex NIfTI file.
%                    - InfoLike    : NIfTI-info-like struct used as write template.
%
%   Output
%     Jones1_3D   : Complex Jones volume for channel 1 (Aline x Bline x Depth).
%     Jones2_3D   : Complex Jones volume for channel 2 (Aline x Bline x Depth).
%
%   This function is designed to be numerically equivalent to the legacy
%   s2c_raw implementation when called with the corresponding parameters.
arguments
    spectralFile {mustBeTextScalar, mustBeNonempty}
    spectralOpts struct = struct()
    outputOpts struct = struct()
end

spectralOpts = psoct.internal.opts.normalizeSpectralOpts(spectralOpts);
outputOpts = psoct.internal.opts.normalizeOutputOpts(outputOpts);

dispCompFile = string(spectralOpts.dispCompFile);
AlineSize = spectralOpts.AlineSize;
BlineSize = spectralOpts.BlineSize;
is12bit = spectralOpts.isRawFormat; % packed 12-bit, headerless (tile_saving_type=spectral_12bit)

% Constants describing the acquisition format.
HEADER_BYTES = 352;          % Header size of Nifti-1 file
BITS_PER_SAMPLE_RAW = 12;    % Packed 12-bit samples for raw format
BYTES_PER_UINT16 = 2;        % 16-bit unsigned integers

warning off MATLAB:polyfit:RepeatedPointsOrRescale %#ok<WNOFF>

Aline = AlineSize;
Bline = BlineSize;
fprintf('AlineSize = %i; BlineSize = %i \n', AlineSize, BlineSize);

% Parameters copied from original script / s2c_raw
AutoCorrPeakCut = 24;                 % multiple of 8
DepthL          = 1024 - AutoCorrPeakCut;  %#ok<NASGU> kept for compatibility
AlineLength = 2048; % Headerless packed raw retains its legacy geometry.
niftiData = [];
if ~is12bit
    info = niftiinfo(spectralFile);
    niftiData = niftiread(info);
    dims = size(niftiData);
    if ndims(niftiData) > 3 || mod(dims(2), 2) ~= 0
        error('spectral2complex:Geometry', ...
            'Expected [spectra, 2*A-lines per channel, B-lines] NIfTI.');
    end
    AlineLength = dims(1);
    AlineSize = dims(2)/2;
    BlineSize = size(niftiData, 3);
    Aline = AlineSize;
    Bline = BlineSize;
    fprintf('NIfTI geometry: %d spectra, %d A-lines/channel, %d B-lines\n', ...
        AlineLength, Aline, Bline);
end
DepthL = floor(AlineLength/2) - AutoCorrPeakCut;
if DepthL < 1
    error('spectral2complex:TooShort', 'Need at least 50 spectral samples.');
end

% Buffer size bookkeeping
numSamplesPerBuffer = AlineLength * AlineSize;
if is12bit
    % Packed 12-bit: 3 bytes per 2 samples
    bytesPerBuffer = numSamplesPerBuffer * (BITS_PER_SAMPLE_RAW / 8);
else
    % 16-bit samples
    bytesPerBuffer = numSamplesPerBuffer * BYTES_PER_UINT16;
end

% Depth / interpolation configuration
PaddingFactor       = 1;
PaddingLength       = AlineLength * PaddingFactor;
OriginalLineLength1 = AlineLength;
OriginalLineLength2 = AlineLength;
Start1              = 1;
Start2              = 1;

InterpolationParameters = [ ...
    PaddingFactor,      PaddingLength, ...
    OriginalLineLength1, Start1, ...
    OriginalLineLength2, Start2];

phaseMode = spectralOpts.interpolationMethod == "phase_calibration";
Wavelengths_l = []; Wavelengths_r = []; InterpolatedWavelengths2 = [];
calibration = struct();
if phaseMode
    calibration = psoct.spectral.loadPhaseCalibration( ...
        spectralOpts.interpolationPath, AlineLength, spectralOpts.phaseCalibrationDispersion, ...
        spectralOpts.dphaseFile, spectralOpts.linPhaseFile, spectralOpts.dspPhaseFile);
else
    [Wavelengths_l, Wavelengths_r, InterpolatedWavelengths2, Ks] = ...
        opticstream_interpolationwave(InterpolationParameters); %#ok<ASGLU>
end

% Package parameters that are shared across B-lines
params = struct();
params.phaseMode = phaseMode;
params.calibration = calibration;
params.AlineSize              = AlineSize;
params.BlineSize              = BlineSize;
params.AlineLength            = AlineLength;
params.AutoCorrPeakCut        = AutoCorrPeakCut;
params.PaddingFactor          = PaddingFactor;
params.PaddingLength          = PaddingLength;
params.OriginalLineLength1    = OriginalLineLength1;
params.OriginalLineLength2    = OriginalLineLength2;
params.Start1                 = Start1;
params.Start2                 = Start2;
params.numSamplesPerBuffer    = numSamplesPerBuffer;
params.bytesPerBuffer         = bytesPerBuffer;
params.Wavelengths_l          = Wavelengths_l;
params.Wavelengths_r          = Wavelengths_r;
params.InterpolatedWavelengths = InterpolatedWavelengths2;

% Read the channel-specific corrections from the two halves of one file.
if phaseMode && spectralOpts.phaseCalibrationDispersion && ~isempty(calibration.dispersion)
    phaseCorrection1 = calibration.dispersion;
    phaseCorrection2 = calibration.dispersion;
else
    [phaseCorrection1, phaseCorrection2] = ...
        opticstream_read_dispersion(dispCompFile, AlineLength);
end

% Replicate along A-line dimension to match interpolated buffer size
phaseCorrection1 = repmat(phaseCorrection1, 1, Aline);
phaseCorrection2 = repmat(phaseCorrection2, 1, Aline);

% Preallocate complex stacks for Jones1 and Jones2:
% dimensions: Bline x Aline x DepthL (complex)
Jones1_3D = complex(zeros(Bline, Aline, DepthL));
Jones2_3D = complex(zeros(Bline, Aline, DepthL));

% Basic validation of input file size (best-effort check)
fileInfo = dir(spectralFile);
if ~isempty(fileInfo)
    expectedBytes = Bline * (2 * bytesPerBuffer);
    if ~is12bit
        expectedBytes = expectedBytes + HEADER_BYTES;
    end
    if fileInfo.bytes < expectedBytes
        warning('spectral2complex:FileTooSmall', ...
            'Spectral file "%s" appears smaller (%d bytes) than expected (~%d bytes).', ...
            spectralFile, fileInfo.bytes, expectedBytes);
    end
end

fid = fopen(spectralFile, 'rb');
if fid == -1
    error('spectral2complex:FileOpenFailed', ...
        'Could not open spectral data file "%s".', spectralFile);
end
cleanupObj = onCleanup(@() fclose(fid)); %#ok<NASGU>

for blineIndex = 1:Bline
    if mod(blineIndex, 10) == 0 || blineIndex == Bline
        pct = 100 * blineIndex / Bline;
        fprintf('\rProcessing bline: %d / %d (%.1f%%)', blineIndex, Bline, pct);
    end
    
    % Read raw wavelength buffers for both polarization channels
    if is12bit
        [WavelengthBuffer1, WavelengthBuffer2] = readBlineBuffers( ...
            fid, blineIndex, params, is12bit, HEADER_BYTES);
    else
        WavelengthBuffer1 = double(niftiData(:, 1:Aline, blineIndex));
        WavelengthBuffer2 = double(niftiData(:, Aline+1:2*Aline, blineIndex));
    end
    if spectralOpts.flipChannel2Spectra
        WavelengthBuffer2 = flipud(WavelengthBuffer2);
    end

    % Convert spectral buffers into Jones vectors for this B-line
    [Jones1, Jones2] = processBlineBuffers( ...
        WavelengthBuffer1, WavelengthBuffer2, params, ...
        phaseCorrection1, phaseCorrection2);

    % Store into the stack. Jones returned as Depth x Aline -> transpose to Aline x Depth
    % We want shape Bline x Aline x Depth, so transpose Jones and assign:
    Jones1_3D(blineIndex,:,:) = Jones1';   % Bline x Aline x Depth
    Jones2_3D(blineIndex,:,:) = Jones2';
end % for blineIndex

Jones1_3D = permute(Jones1_3D, [2 1 3]);
Jones2_3D = permute(Jones2_3D, [2 1 3]);

Jones1_3d = flip(Jones1_3D, 3);
Jones2_3d = flip(Jones2_3D, 3);

outputPath = outputOpts.Paths.complex;
if outputPath ~= ""
    % real_J1: Bline x Aline x Depth  -> we'll stack along dim 1,
    % so cat(1, real, imag) -> (2*Bline) x Aline x Depth
    Jstack_all = cat(1, ...
        real(Jones1_3D), imag(Jones1_3D), ...
        real(Jones2_3D), imag(Jones2_3D));

    fprintf('Saving output to: %s\n', outputPath);
    Jstack_all = single(Jstack_all);
    futures = {};
    futures = psoct.internal.nifti.appendWriteFuture(futures, ...
        psoct.internal.nifti.writeNiftiIfPath(outputPath, Jstack_all, outputOpts.InfoLike));
    psoct.internal.nifti.waitWriteFutures(futures);
end

end

% -------------------------------------------------------------------------
% Local helpers
% -------------------------------------------------------------------------

function [WavelengthBuffer1, WavelengthBuffer2] = readBlineBuffers( ...
    fid, blineIndex, params, is12bit, headerBytes)
%READBLINEBUFFERS Read both polarization buffers for a single B-line.

offsetBase = (blineIndex - 1) * (2 * params.bytesPerBuffer); % two buffers (channels) per B-line
if ~is12bit
    offsetBase = offsetBase + headerBytes; % header is present once at the beginning
end

fseekStatus = fseek(fid, offsetBase, 'bof');
if fseekStatus ~= 0
    error('spectral2complex:SeekFailed', ...
        'Failed to seek to offset %d in spectral file.', offsetBase);
end

if ~is12bit
    data1 = fread(fid, params.numSamplesPerBuffer, 'uint16');
    data2 = fread(fid, params.numSamplesPerBuffer, 'uint16');
else
    raw1 = fread(fid, params.bytesPerBuffer, 'uint8=>uint8');
    raw2 = fread(fid, params.bytesPerBuffer, 'uint8=>uint8');
    if numel(raw1) < params.bytesPerBuffer || numel(raw2) < params.bytesPerBuffer
        error('spectral2complex:UnexpectedEOF', ...
            'Reached end of file while reading raw buffers for B-line %d.', blineIndex);
    end
    data1 = psoct.spectral.unpack12bits(raw1);
    data2 = psoct.spectral.unpack12bits(raw2);
end

if numel(data1) ~= params.numSamplesPerBuffer || numel(data2) ~= params.numSamplesPerBuffer
    error('spectral2complex:UnexpectedSampleCount', ...
        'Expected %d samples per buffer but read %d and %d.', ...
        params.numSamplesPerBuffer, numel(data1), numel(data2));
end

WavelengthBuffer1 = reshape(data1, params.AlineLength, []);
WavelengthBuffer2 = reshape(data2, params.AlineLength, []);
end

function [Jones1, Jones2] = processBlineBuffers( ...
    WavelengthBuffer1, WavelengthBuffer2, params, ...
    phaseCorrection1, phaseCorrection2)
%PROCESSBLINEBUFFERS Convert spectral buffers into Jones vectors.

Aline               = params.AlineSize;
PaddingFactor       = params.PaddingFactor;
OriginalLineLength1 = params.OriginalLineLength1;
OriginalLineLength2 = params.OriginalLineLength2;
Start1              = params.Start1;
Start2              = params.Start2;

% Remove reference (mean across A-lines)
refdata1  = mean(WavelengthBuffer1, 2);
refdata2  = mean(WavelengthBuffer2, 2);
MeanScan1 = WavelengthBuffer1 - refdata1;
MeanScan2 = WavelengthBuffer2 - refdata2;

% Crop to original line length
OriginalBuffer1 = MeanScan1(Start1:OriginalLineLength1 - 1 + Start1, :);
OriginalBuffer2 = MeanScan2(Start2:OriginalLineLength2 - 1 + Start2, :);

if params.phaseMode
    % Each column is an A-line. Calibration is shared by both channels.
    InterpolatedBuffer1 = interp1(params.calibration.dphase, OriginalBuffer1, ...
        params.calibration.linPhase, 'linear', 'extrap');
    InterpolatedBuffer2 = interp1(params.calibration.dphase, OriginalBuffer2, ...
        params.calibration.linPhase, 'linear', 'extrap');
else
% Zero padding
ZeroPaddedBuffer1 = OriginalBuffer1; % PaddingFactor is one; preserve arbitrary sample counts.
ZeroPaddedBuffer2 = OriginalBuffer2;
% Retain legacy even-length numerical behavior; its helper cannot index odd lengths.
if mod(size(OriginalBuffer1, 1), 2) == 0
    ZeroPaddedBuffer1 = psoct.legacy.ZeroPadBuffer(OriginalBuffer1, PaddingFactor);
    ZeroPaddedBuffer2 = psoct.legacy.ZeroPadBuffer(OriginalBuffer2, PaddingFactor);
end

% Interpolation onto k-space grid
InterpolatedBuffer1 = interp1(params.Wavelengths_l, ZeroPaddedBuffer1, ...
    params.InterpolatedWavelengths, 'linear', 'extrap');
InterpolatedBuffer2 = interp1(params.Wavelengths_r, ZeroPaddedBuffer2, ...
    params.InterpolatedWavelengths, 'linear', 'extrap');
end

% Remove DC component using median across A-lines
InterpolatedBuffer1 = InterpolatedBuffer1 - median(InterpolatedBuffer1, 2);
InterpolatedBuffer2 = InterpolatedBuffer2 - median(InterpolatedBuffer2, 2);

% Apply dispersion correction
InterpolatedBuffer1 = InterpolatedBuffer1 .* phaseCorrection1;
InterpolatedBuffer2 = InterpolatedBuffer2 .* phaseCorrection2;

% Obtain Jones vectors (complex) for this B-line (size: Depth x Aline)
Jones1 = buffer2jones(InterpolatedBuffer1, PaddingFactor, params.AutoCorrPeakCut);
Jones2 = buffer2jones(InterpolatedBuffer2, PaddingFactor, params.AutoCorrPeakCut);
end

function [Jones]=buffer2jones(OriginalBuffer, PaddingFactor, AutoCorrPeakCut)
% [Jones]=Buffer2JonesDispComp(OriginalBuffer, PaddingFactor, AutoCorrPeakCut)

% % upsampling
% AlineLength = size(OriginalBuffer, 1) / (2*PaddingFactor)*4;
% Jones = fft(OriginalBuffer,4*size(OriginalBuffer,1));
AlineLength = floor(size(OriginalBuffer, 1) / (2*PaddingFactor));
Jones = (fft(OriginalBuffer,size(OriginalBuffer,1)));
Jones(AlineLength+1:end, :) = [];
Jones(1:AutoCorrPeakCut, :) = []; % Cut out autocorrelation peak
end
