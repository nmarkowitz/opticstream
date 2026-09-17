function test_spectral_overlay
% Run with the upstream toolbox and opticstream/matlab on the MATLAB path.
folder = tempname;
mkdir(folder);
cleanup = onCleanup(@() rmdir(folder, 's'));
for n = [1152 1153 2048]
    file = fullfile(folder, 'disp.txt');
    fid = fopen(file, 'w');
    for k = 1:n
        fprintf(fid, '0.8+0.6i\n');
    end
    for k = 1:n
        fprintf(fid, '0.6+0.8i\n');
    end
    fclose(fid);
    [c1,c2] = opticstream_read_dispersion(file, n);
    assert(all(abs(c1-(0.8+0.6i)) < 1e-12));
    assert(all(abs(c2-(0.6+0.8i)) < 1e-12));
    rejected = false;
    try
        opticstream_read_dispersion(file, n+1);
    catch ME
        rejected = strcmp(ME.identifier, 'spectral2complex:BadDispersionLength');
    end
    assert(rejected);
    data = int16(randi([-100 100], n, 8, 2));
    input = fullfile(folder, 'input.nii');
    niftiwrite(data, input);
    [a,b] = psoct.spectral.spectral2complex(input, ...
        struct('dispCompFile', file, 'isRawFormat', false), struct());
    assert(isequal(size(a), [4 2 floor(n/2)-24]));
    assert(isequal(size(a), size(b)));
    assert(all(isfinite(a(:))) && all(isfinite(b(:))));
end
disp('Spectral overlay tests passed');
end
