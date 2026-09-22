"""
Validate processed outputs from spectral2processed_batch_indexed / complex2processed_batch_indexed.

Expected stems match ``+psoct/+file/+internal/processedModalities.m`` postfixes.
Prefix is ``processed_output_prefix(mosaic_id, ref.tile_number)``, aligned with MATLAB
``psoct.file.internal.buildProcessedOutputPrefix`` (not inferred from input basenames).

Which modalities to require follows ``PSOCTScanConfigModel.volume_modalities`` and
``enface_modalities`` (same basis as ``build_pipeline_opts`` EnfaceComputeFlags).
"""


from pathlib import Path

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.flows.psoct.tile_file_reference import TileFileReference
from opticstream.flows.psoct.utils import processed_output_prefix


def validate_output_files_exist_and_min_size(
    outputs: list[tuple[str, Path]],
    *,
    min_file_size_bytes: int,
    context: str,
) -> None:
    """
    Validate that each output file exists and is at least `min_file_size_bytes`.

    Raises `RuntimeError` with a summarized list of issues.
    """
    issues: list[str] = []
    for label, out_path in outputs:
        out_path = out_path.resolve()
        if not out_path.is_file():
            issues.append(f"missing output ({label}): {out_path}")
            continue
        size = out_path.stat().st_size
        if size < min_file_size_bytes:
            issues.append(f"output too small ({label}): {out_path} ({size} B)")

    if issues:
        raise RuntimeError(
            f"{context} validation failed for {len(issues)} issue(s):\n"
            + "\n".join(issues)
        )


def expected_processed_nifti_suffixes(config: PSOCTScanConfigModel) -> tuple[str, ...]:
    """Ordered, de-duplicated suffixes to validate."""
    return tuple(
        dict.fromkeys(
            m.value
            for modalities in (config.volume_modalities if config.processing.save_volume_outputs else [], config.enface_modalities)
            for m in modalities
        )
    )


def validate_processed_batch_outputs(
    processed_dir: Path,
    file_reference_list: list[TileFileReference],
    *,
    mosaic_id: int,
    config: PSOCTScanConfigModel,
    min_volume_file_size_bytes: int = 100 * 1024 * 1024,
    min_enface_file_size_bytes: int = 100 * 1024,
) -> None:
    """
    Ensure each tile has the configured processed NIfTIs under ``processed_dir``.

    Raises ``RuntimeError`` with a summary if any expected file is missing or too small.
    """
    processed_dir = processed_dir.resolve()

    grouped_suffixes = {
        "volume": tuple(dict.fromkeys(m.value for m in config.volume_modalities)) if config.processing.save_volume_outputs else (),
        "enface": tuple(dict.fromkeys(m.value for m in config.enface_modalities)),
    }

    for group, min_size in (
        ("volume", min_volume_file_size_bytes),
        ("enface", min_enface_file_size_bytes),
    ):
        suffixes = grouped_suffixes[group]
        if not suffixes:
            continue

        outputs = [
            (
                f"tile_id={ref.tile_number}, key={suffix}",
                processed_dir
                / f"{processed_output_prefix(mosaic_id, ref.tile_number)}_{suffix}.nii",
            )
            for ref in file_reference_list
            for suffix in suffixes
        ]

        validate_output_files_exist_and_min_size(
            outputs,
            min_file_size_bytes=min_size,
            context=f"Processed tile outputs ({group})",
        )
