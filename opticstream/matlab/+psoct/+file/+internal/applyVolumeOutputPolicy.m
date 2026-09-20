function outputOpts = applyVolumeOutputPolicy(outputOpts)
% Consume OpticStream's flag after indexed paths have been assigned.
% Empty volume paths disable disk writes, not intermediate reconstruction.
if isfield(outputOpts, 'SaveVolumeOutputs')
    enabled = outputOpts.SaveVolumeOutputs;
    validateattributes(enabled, {'logical'}, {'scalar'});
    outputOpts = rmfield(outputOpts, 'SaveVolumeOutputs');
    if ~enabled
        outputOpts.Paths.dBI = '';
        outputOpts.Paths.R3D = '';
        outputOpts.Paths.O3D = '';
    end
end
end
