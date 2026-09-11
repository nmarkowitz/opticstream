function correction = opticstream_read_dispersion(filename, spectra)
% Text: N complex rows, N-by-2 real/imaginary pairs, or N phase angles.
% Binary (.dat/.bin): N native-endian float64 phase angles, legacy convention.
% A complex pair counts as ONE spectral coefficient.
[~,~,ext] = fileparts(filename);
if any(strcmpi(ext, {'.txt', '.csv', '.tsv'}))
    lines = strip(splitlines(string(fileread(filename))));
    lines(lines == "") = [];
    if any(contains(lower(lines), ["i", "j"]))
        % str2double parses complex literals without evaluating MATLAB code.
        correction = str2double(lines);
        if any(isnan(correction))
            error('spectral2complex:DispersionFormat', ...
                'Expected one complex number per row, e.g. 0.98+0.12i.');
        end
    else
    values = readmatrix(filename, 'FileType', 'text');
    if size(values, 2) == 2
        correction = complex(values(:,1), values(:,2));
    elseif isvector(values)
        correction = exp(-1i .* values(:));
    else
        error('spectral2complex:DispersionFormat', ...
            'Text dispersion must contain phase angles or two real/imaginary columns.');
    end
    end
else
    fid = fopen(filename, 'rb');
    if fid < 0
        error('spectral2complex:DispersionOpenFailed', 'Cannot open %s.', filename);
    end
    cleanup = onCleanup(@() fclose(fid));
    bytes = dir(filename);
    if mod(bytes.bytes, 8) ~= 0
        error('spectral2complex:DispersionFormat', 'Binary phase file must contain float64 values.');
    end
    correction = exp(-1i .* fread(fid, inf, 'double'));
end
if numel(correction) ~= spectra || any(~isfinite(correction(:)))
    error('spectral2complex:BadDispersionLength', ...
        'Dispersion file "%s" has %d coefficients; NIfTI requires exactly %d finite coefficients.', ...
        filename, numel(correction), spectra);
end
correction = correction(:);
end
