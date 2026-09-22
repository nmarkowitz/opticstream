"""
This script builds and runs a Fiji/ImageJ macro for grid stitching.
The comment below shows the macro shape produced by this module:
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from cyclopts import App

#DEFAULT_FIJI_PATH = Path("/autofs/cluster/octdata2/users/Hui/Fiji.app/ImageJ-linux64")
DEFAULT_FIJI_PATH = Path("/usr/pubsw/packages/ImageJ/current/ImageJ-linux64")

app = App(name="fiji")


def build_macro(
    directory: Path,
    file_template: str,
    grid_size_x: int,
    grid_size_y: int,
    tile_overlap: float = 20.0,
    first_file_index: int = 1,
    output_textfile_name: str = "TileConfiguration.txt",
    regression_threshold: float = 0.30,
    max_avg_displacement_threshold: float = 2.50,
    absolute_displacement_threshold: float = 3.50,
) -> str:
    """
    Construct the Fiji macro string for Grid/Collection stitching.

    Args:
        directory: Directory containing the tiles.
        file_template: Fiji filename pattern, e.g. "mosaic_001_image_{
        iii}_processed_aip.nii".
        grid_size_x: Number of tiles in X.
        grid_size_y: Number of tiles in Y.
        tile_overlap: Overlap percentage between tiles.
        first_file_index: Starting index for the tile filenames.
        output_textfile_name: Name of the tile configuration text file Fiji produces.
        regression_threshold: Fiji regression threshold.
        max_avg_displacement_threshold: Fiji max/avg displacement threshold.
        absolute_displacement_threshold: Fiji absolute displacement threshold.
    """
    directory_str = str(Path(directory).resolve())
    return (
        'run("Grid/Collection stitching", '
        f'"type=[Grid: snake by columns] order=[Up & Right] '
        f"grid_size_x={grid_size_x} grid_size_y={grid_size_y} "
        f"tile_overlap={tile_overlap} first_file_index_i={first_file_index} "
        f"directory={directory_str} file_names={file_template} "
        f"output_textfile_name={output_textfile_name} "
        f"fusion_method=[Linear Blending] "
        f"regression_threshold={regression_threshold:.2f} "
        f"max/avg_displacement_threshold={max_avg_displacement_threshold:.2f} "
        f"absolute_displacement_threshold={absolute_displacement_threshold:.2f} "
        "compute_overlap computation_parameters=[Save computation time (but use more RAM)] "
        'image_output=[Fuse and display]");'
    )


def run_fiji_macro(fiji_path: Path, macro: str, headless: bool = True) -> None:
    """Write the macro to a temp file and execute it with Fiji."""
    with tempfile.NamedTemporaryFile("w", suffix=".ijm", delete=False) as tmp:
        tmp.write(macro)
        macro_path = Path(tmp.name)

    args = [str(fiji_path)]
    if headless:
        args.append("--headless")
    args += ["-macro", str(macro_path)]

    # Capture output so caller can inspect logs if needed.
    subprocess.run(args, check=True)


@app.default
def main(
    directory: Path,
    file_template: str,
    grid_size_x: int,
    grid_size_y: int,
    fiji_path: Path = DEFAULT_FIJI_PATH,
    tile_overlap: float = 20.0,
    first_file_index: int = 1,
    output_textfile_name: str = "TileConfiguration.txt",
    regression_threshold: float = 0.30,
    max_avg_displacement_threshold: float = 2.50,
    absolute_displacement_threshold: float = 3.50,
    no_headless: bool = False,
) -> None:
    """
    Run Fiji grid stitching.

    Parameters
    ----------
    directory : Path
        Directory containing tile images.
    file_template : str
        Filename template Fiji expects, e.g., mosaic_001_image_{iii}_processed_aip.nii
    grid_size_x : int
        Number of tiles in X.
    grid_size_y : int
        Number of tiles in Y.
    fiji_path : Path
        Path to Fiji executable (default: /autofs/cluster/octdata2/users/Hui/Fiji.app/ImageJ-linux64).
    tile_overlap : float
        Tile overlap percentage (default: 20.0).
    first_file_index : int
        First tile index in the filenames (default: 1).
    output_textfile_name : str
        Name of the output tile configuration file (default: TileConfiguration.txt).
    regression_threshold : float
        Regression threshold for Fiji (default: 0.30).
    max_avg_displacement_threshold : float
        Max/avg displacement threshold for Fiji (default: 2.50).
    absolute_displacement_threshold : float
        Absolute displacement threshold for Fiji (default: 3.50).
    no_headless : bool
        Run Fiji with GUI instead of headless mode (default: False).
    """
    macro = build_macro(
        directory=directory,
        file_template=file_template,
        grid_size_x=grid_size_x,
        grid_size_y=grid_size_y,
        tile_overlap=tile_overlap,
        first_file_index=first_file_index,
        output_textfile_name=output_textfile_name,
        regression_threshold=regression_threshold,
        max_avg_displacement_threshold=max_avg_displacement_threshold,
        absolute_displacement_threshold=absolute_displacement_threshold,
    )
    run_fiji_macro(
        fiji_path=fiji_path,
        macro=macro,
        headless=not no_headless,
    )


if __name__ == "__main__":
    app()
