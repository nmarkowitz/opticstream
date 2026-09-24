function outputOpts = applyComplexOutputPolicy(outputOpts, prefix)
% Consume OpticStream's ComplexOutputDir: save the complex (Jones) volume that
% spectral2complex computes anyway as <ComplexOutputDir>/<prefix>_complex.nii.
% An empty or missing directory leaves the complex data in memory only.
if isfield(outputOpts, 'ComplexOutputDir')
    complexDir = string(outputOpts.ComplexOutputDir);
    outputOpts = rmfield(outputOpts, 'ComplexOutputDir');
    if strlength(complexDir) > 0
        if ~isfolder(complexDir)
            mkdir(complexDir);
        end
        outputOpts.Paths.complex = fullfile(complexDir, prefix + "_complex.nii");
    end
end
end
