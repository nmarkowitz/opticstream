function test_volume_output_policy()
paths = struct('dBI', 'dBI.nii', 'R3D', 'R3D.nii', 'O3D', 'O3D.nii', 'aip', 'aip.nii');
for enabled = [false, true]
    opts = psoct.file.internal.applyVolumeOutputPolicy(struct('Paths', paths, 'SaveVolumeOutputs', enabled));
    assert(~isfield(opts, 'SaveVolumeOutputs'));
    assert(strcmp(opts.Paths.aip, 'aip.nii'));
    for name = ["dBI", "R3D", "O3D"]
        if enabled
            assert(strcmp(opts.Paths.(name), paths.(name)));
        else
            assert(isempty(opts.Paths.(name)));
            future = psoct.internal.nifti.writeNiftiIfPath(opts.Paths.(name), zeros(2,2,2), struct());
            assert(isempty(future));
        end
    end
    normalized = psoct.internal.opts.normalizeOutputOpts(opts);
    assert(strcmp(normalized.Paths.aip, 'aip.nii'));
end
opts = psoct.file.internal.applyVolumeOutputPolicy(struct('Paths', paths));
assert(isequal(opts.Paths, paths));
disp('Volume output policy tests passed');
end
