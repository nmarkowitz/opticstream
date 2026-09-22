function spectral2processed_batch_indexed(filenames, outputDir, mosaicId, tileIndices, spectralOpts, surfaceOpts, enfaceOpts, acquisitionOpts, outputOpts, volumeOpts, optsMatFile, numWorkers, poolType)
%SPECTRAL2PROCESSED_BATCH_INDEXED Batch spectralâ†’processed with explicit mosaic/tile indices.
%
%   spectral2processed_batch_indexed(filenames, outputDir, mosaicId, tileIndices, ...
%       spectralOpts, surfaceOpts, enfaceOpts, acquisitionOpts, outputOpts, ...
%       volumeOpts, optsMatFile, numWorkers, poolType)
%
%   Same as spectral2processed_batch except output stems are built as
%       mosaic_{mosaicId:03d}_image_{tileIndices(k):04d}
%   for file k (no basename parsing). numel(tileIndices) must equal numel(filenames).
%
%   See psoct.file.internal.buildProcessedOutputPrefix.

arguments
    filenames
    outputDir {mustBeText}
    mosaicId (1,1) double {mustBeInteger, mustBeNonnegative}
    tileIndices double
    spectralOpts struct = struct()
    surfaceOpts struct = struct()
    enfaceOpts struct = struct()
    acquisitionOpts struct = struct()
    outputOpts struct = struct()
    volumeOpts struct = struct()
    optsMatFile {mustBeTextScalar} = ""
    numWorkers double = []
    poolType string {mustBeMember(poolType, ["process","thread"])} = "process"
end

files = psoct.file.internal.normalizeFilenames(filenames);
tileIndices = tileIndices(:);
if numel(tileIndices) ~= numel(files)
    error("psoct:file:spectral2processed_batch_indexed:TileIndicesMismatch", ...
        "tileIndices must have length %d (number of files), got %d.", ...
        numel(files), numel(tileIndices));
end

outputDir = string(outputDir);
optsMatFile = string(optsMatFile);

if optsMatFile ~= "" && ~isfile(optsMatFile)
    error("psoct:file:spectral2processed_batch_indexed:OptsFileNotFound", ...
        "Opts .mat file not found: ""%s"".", optsMatFile);
end

psoct.file.internal.ensureParpool(numWorkers, poolType);

modalities = psoct.file.internal.processedModalities();

if ~isempty(numWorkers) && numWorkers == 1
    for idx = 1:numel(files)
        inFile = files{idx};
        try
            prefix = psoct.file.internal.buildProcessedOutputPrefix(mosaicId, tileIndices(idx));
            inDir = string(fileparts(inFile));

            perOutputOpts = outputOpts;
            if ~isfield(perOutputOpts, "Paths") || isempty(perOutputOpts.Paths)
                perOutputOpts.Paths = struct();
            end

            baseDir = outputDir;
            if strlength(baseDir) == 0
                baseDir = inDir;
            end

            for m = 1:numel(modalities)
                key = modalities(m);
                outName = prefix + "_" + key + ".nii";
                perOutputOpts.Paths.(key) = fullfile(baseDir, outName);
            end

            fprintf("spectral2processed_batch_indexed: %s -> %s_*\n", inFile, prefix);

            perOutputOpts = psoct.file.internal.applyVolumeOutputPolicy(perOutputOpts);
            psoct.file.spectral2processed( ...
                inFile, spectralOpts, surfaceOpts, enfaceOpts, ...
                acquisitionOpts, perOutputOpts, volumeOpts, optsMatFile);
        catch ME
            warning("psoct:file:spectral2processed_batch_indexed:FailedFile", ...
                "Failed to process %s: %s", inFile, ME.message);
        end
    end
else
parfor idx = 1:numel(files)
    inFile = files{idx};
    try
        prefix = psoct.file.internal.buildProcessedOutputPrefix(mosaicId, tileIndices(idx));
        inDir = string(fileparts(inFile));

        perOutputOpts = outputOpts;
        if ~isfield(perOutputOpts, "Paths") || isempty(perOutputOpts.Paths)
            perOutputOpts.Paths = struct();
        end

        baseDir = outputDir;
        if strlength(baseDir) == 0
            baseDir = inDir;
        end

        for m = 1:numel(modalities)
            key = modalities(m);
            outName = prefix + "_" + key + ".nii";
            perOutputOpts.Paths.(key) = fullfile(baseDir, outName);
        end

        fprintf("spectral2processed_batch_indexed: %s -> %s_*\n", inFile, prefix);

        perOutputOpts = psoct.file.internal.applyVolumeOutputPolicy(perOutputOpts);
        psoct.file.spectral2processed( ...
            inFile, spectralOpts, surfaceOpts, enfaceOpts, ...
            acquisitionOpts, perOutputOpts, volumeOpts, optsMatFile);
    catch ME
        warning("psoct:file:spectral2processed_batch_indexed:FailedFile", ...
            "Failed to process %s: %s", inFile, ME.message);
    end
end
end

end
