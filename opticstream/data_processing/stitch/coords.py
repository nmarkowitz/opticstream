import argparse
import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import nibabel as nib
import numpy as np
import yaml


# --- Helper Function to Parse Coordinate Lines ---
def _parse_line(line: str) -> Optional[Tuple[str, Tuple[float, float]]]:
    line = line.strip()
    if not line:
        return None
    parts = line.split("; ; ")
    if len(parts) != 2:
        print(f"Skipping malformed line: {line}")
        return None
    filename = parts[0].strip()
    coord_str = parts[1].strip()
    try:
        coord = ast.literal_eval(coord_str)
        if not (isinstance(coord, tuple) and len(coord) == 2):
            raise ValueError("Not a 2-tuple coordinate")
        return filename, (float(coord[0]), float(coord[1]))
    except (ValueError, SyntaxError):
        print(f"Skipping malformed coordinate '{coord_str}' in line: {line}")
        return None


# --- Tile Data Class ---
class Tile:
    def __init__(
        self,
        path: str,
        normal_coord: Tuple[float, float],
        stitched_coord: Tuple[float, float],
        avg_signal: Optional[float] = None,
    ):
        self.path = path
        self.normal_coord = normal_coord
        self.stitched_coord = stitched_coord
        self.derived_coord: Optional[Tuple[float, float]] = None
        self.normalized_coord: Optional[Tuple[float, float]] = None

        self._dataobj_cache = None
        self._avg_signal = avg_signal
        self.index = self._extract_index(self.path)

    def _extract_index(self, path: str) -> Optional[int]:
        filename = os.path.basename(path)
        match = re.search(r"_image_(\d+)_", filename)
        if match:
            return int(match.group(1))
        fallback = re.findall(r"(\d+)", filename)
        if fallback:
            try:
                return int(fallback[-1])
            except Exception:
                pass
        print(f"Warning: Could not extract index from {filename}")
        return None

    @property
    def data(self):
        if self._dataobj_cache is None:
            try:
                img = nib.load(self.path)
                self._dataobj_cache = img.dataobj
            except FileNotFoundError:
                print("**************************************************")
                print(f"ERROR: File not found at {self.path}")
                print(
                    "Place your .nii files in the same directory or update "
                    "'image_base_path'."
                )
                print("**************************************************")
                raise
            except Exception as e:
                print(f"Error loading {self.path}: {e}")
                raise
        return self._dataobj_cache

    @property
    def avg_signal(self) -> Optional[float]:
        if self._avg_signal is None:
            try:
                arr = self.data[:]
                self._avg_signal = float(np.mean(arr))
            except Exception:
                self._avg_signal = None
        return self._avg_signal

    @avg_signal.setter
    def avg_signal(self, value: Optional[float]):
        self._avg_signal = value

    def __repr__(self) -> str:
        derived_str = (
            f"derived={self.derived_coord}" if self.derived_coord else "derived=None"
        )
        signal_str = (
            f"avg_sig={self.avg_signal:.1f}"
            if self.avg_signal is not None
            else "avg_sig=None"
        )
        idx = f"{self.index:03d}" if self.index is not None else "None"
        return (
            f"Tile(index={idx}, path='{os.path.basename(self.path)}', normal={self.normal_coord}, "
            f"stitched={self.stitched_coord}, {derived_str}, {signal_str})"
        )


# --- Grid Class with Split Normalize & Export ---
class Grid:
    def __init__(
        self,
        tiles_2d: List[List[Optional[Tile]]],
        x_coords: List[float],
        y_coords: List[float],
        x_to_col: Dict[float, int],
        y_to_row: Dict[float, int],
    ):
        self.tiles_2d = tiles_2d
        self.x_coords = x_coords
        self.y_coords = y_coords
        self.x_to_col = x_to_col
        self.y_to_row = y_to_row
        self.num_rows = len(y_coords)
        self.num_cols = len(x_coords)

    @classmethod
    def from_tiles(cls, tiles: List[Tile]) -> "Grid":
        if not tiles:
            return cls([], [], [], {}, {})
        x_coords = sorted({t.normal_coord[0] for t in tiles})
        y_coords = sorted({t.normal_coord[1] for t in tiles})
        x_to_col = {x: i for i, x in enumerate(x_coords)}
        y_to_row = {y: i for i, y in enumerate(y_coords)}
        tiles_2d: List[List[Optional[Tile]]] = [
            [None for _ in x_coords] for _ in y_coords
        ]
        for t in tiles:
            x, y = t.normal_coord
            col = x_to_col[x]
            row = y_to_row[y]
            tiles_2d[row][col] = t
        return cls(tiles_2d, x_coords, y_coords, x_to_col, y_to_row)

    def _tile_at(self, r: int, c: int) -> Optional[Tile]:
        if 0 <= r < self.num_rows and 0 <= c < self.num_cols:
            return self.tiles_2d[r][c]
        return None

    def iter_tiles_row_major(self):
        for r in range(self.num_rows):
            for c in range(self.num_cols):
                yield r, c, self.tiles_2d[r][c]

    def print_tile_map(self):
        if not self.tiles_2d:
            print("No tiles loaded to create a map.")
            return
        total_width = self.num_cols * 6 + 1
        print("\n--- Tile Grid Map (Based on Normal Coordinates) ---")
        print("-" * total_width)
        for row in range(self.num_rows):
            line = "|"
            for col in range(self.num_cols):
                cell = self._tile_at(row, col)
                if cell is None:
                    tile_id = "----"
                else:
                    tile_id = f"{cell.index:04d}" if cell.index is not None else "XXXX"
                line += f" {tile_id} |"
            print(line)
            print("-" * total_width)

    @staticmethod
    def _validated_drift(
        drifts: List[Tuple[float, float]],
        ideal: Tuple[float, float],
        label: str,
        max_deviation: float,
    ) -> Tuple[float, float]:
        """
        Median registered drift, or the ideal grid step if registration is unusable.

        Falls back when no reliable neighbor pairs exist, or when the median
        deviates from the ideal step by more than ``max_deviation`` times the
        ideal step length (e.g. Fiji collapsing tiles onto the same position).
        """
        ideal_len = float(np.hypot(*ideal))
        if not drifts:
            print(
                f"WARNING: no reliable {label} tile pairs; "
                f"falling back to ideal {label} step {ideal}"
            )
            return ideal
        drift = tuple(np.median(np.array(drifts), axis=0).tolist())
        deviation = float(np.hypot(drift[0] - ideal[0], drift[1] - ideal[1]))
        if ideal_len > 0 and deviation > max_deviation * ideal_len:
            print(
                f"WARNING: registered {label} drift {drift} deviates from ideal "
                f"step {ideal} by {deviation:.1f} px (> {max_deviation:.0%} of step); "
                f"registration likely failed, falling back to ideal step"
            )
            return ideal
        return drift

    def compute_registered_offset(
        self, signal_threshold: float = 55.0, max_drift_deviation: float = 0.5
    ):
        horizontal_drifts = []
        vertical_drifts = []

        for r in range(self.num_rows):
            for c in range(self.num_cols - 1):
                a = self._tile_at(r, c)
                b = self._tile_at(r, c + 1)
                if a is None or b is None:
                    continue
                if (a.avg_signal is not None and a.avg_signal >= signal_threshold) and (
                    b.avg_signal is not None and b.avg_signal >= signal_threshold
                ):
                    dx = b.stitched_coord[0] - a.stitched_coord[0]
                    dy = b.stitched_coord[1] - a.stitched_coord[1]
                    horizontal_drifts.append((dx, dy))

        for r in range(self.num_rows - 1):
            for c in range(self.num_cols):
                a = self._tile_at(r, c)
                b = self._tile_at(r + 1, c)
                if a is None or b is None:
                    continue
                if (a.avg_signal is not None and a.avg_signal >= signal_threshold) and (
                    b.avg_signal is not None and b.avg_signal >= signal_threshold
                ):
                    dx = b.stitched_coord[0] - a.stitched_coord[0]
                    dy = b.stitched_coord[1] - a.stitched_coord[1]
                    vertical_drifts.append((dx, dy))

        ideal_horizontal = (
            (self.x_coords[1] - self.x_coords[0], 0.0) if self.num_cols > 1 else (0.0, 0.0)
        )
        ideal_vertical = (
            (0.0, self.y_coords[1] - self.y_coords[0]) if self.num_rows > 1 else (0.0, 0.0)
        )
        horizontal_drift = self._validated_drift(
            horizontal_drifts, ideal_horizontal, "horizontal", max_drift_deviation
        )
        vertical_drift = self._validated_drift(
            vertical_drifts, ideal_vertical, "vertical", max_drift_deviation
        )

        print("\nComputed drifts:")
        print(
            f"  horizontal_drift per column step = (dx={horizontal_drift[0]:.3f}, dy={horizontal_drift[1]:.3f})"
        )
        print(
            f"  vertical_drift   per row    step = (dx={vertical_drift[0]:.3f}, dy={vertical_drift[1]:.3f})"
        )

        for r, c, t in self.iter_tiles_row_major():
            if t is None:
                continue
            reliable = t.avg_signal is not None and t.avg_signal >= signal_threshold
            t.derived_coord = None
            # t.derived_coord = t.stitched_coord if reliable else None
        self._tile_at(0, 0).derived_coord = (0.0, 0.0)
        changed = True
        max_passes = max(1, max(self.num_rows, self.num_cols) * 3)
        pass_num = 0
        while changed and pass_num < max_passes:
            pass_num += 1
            changed = False
            for r in range(self.num_rows):
                for c in range(self.num_cols):
                    t = self._tile_at(r, c)
                    if t is None or t.derived_coord is not None:
                        continue

                    left_idx = next(
                        (
                            cc
                            for cc in range(c - 1, -1, -1)
                            if (
                                self._tile_at(r, cc) is not None
                                and self._tile_at(r, cc).derived_coord is not None
                            )
                        ),
                        None,
                    )
                    right_idx = next(
                        (
                            cc
                            for cc in range(c + 1, self.num_cols)
                            if (
                                self._tile_at(r, cc) is not None
                                and self._tile_at(r, cc).derived_coord is not None
                            )
                        ),
                        None,
                    )

                    if left_idx is not None:
                        neighbor = self._tile_at(r, left_idx)
                        steps = c - left_idx
                        new_x = neighbor.derived_coord[0] + steps * horizontal_drift[0]
                        new_y = neighbor.derived_coord[1] + steps * horizontal_drift[1]
                        t.derived_coord = (new_x, new_y)
                        changed = True
                        continue
                    if right_idx is not None:
                        neighbor = self._tile_at(r, right_idx)
                        steps = right_idx - c
                        new_x = neighbor.derived_coord[0] - steps * horizontal_drift[0]
                        new_y = neighbor.derived_coord[1] - steps * horizontal_drift[1]
                        t.derived_coord = (new_x, new_y)
                        changed = True
                        continue

                    up_idx = next(
                        (
                            rr
                            for rr in range(r - 1, -1, -1)
                            if (
                                self._tile_at(rr, c) is not None
                                and self._tile_at(rr, c).derived_coord is not None
                            )
                        ),
                        None,
                    )
                    down_idx = next(
                        (
                            rr
                            for rr in range(r + 1, self.num_rows)
                            if (
                                self._tile_at(rr, c) is not None
                                and self._tile_at(rr, c).derived_coord is not None
                            )
                        ),
                        None,
                    )

                    if up_idx is not None:
                        neighbor = self._tile_at(up_idx, c)
                        steps = r - up_idx
                        new_x = neighbor.derived_coord[0] + steps * vertical_drift[0]
                        new_y = neighbor.derived_coord[1] + steps * vertical_drift[1]
                        t.derived_coord = (new_x, new_y)
                        changed = True
                        continue
                    if down_idx is not None:
                        neighbor = self._tile_at(down_idx, c)
                        steps = down_idx - r
                        new_x = neighbor.derived_coord[0] - steps * vertical_drift[0]
                        new_y = neighbor.derived_coord[1] - steps * vertical_drift[1]
                        t.derived_coord = (new_x, new_y)
                        changed = True
                        continue
            print(f"Propagation pass {pass_num} completed. Any changes: {changed}")

        remaining_none = 0
        for r, c, t in self.iter_tiles_row_major():
            if t is None:
                continue
            if t.derived_coord is None:
                t.derived_coord = t.stitched_coord
                remaining_none += 1

        if remaining_none:
            print(
                f"Warning: {remaining_none} tiles had no propagation source; set derived_coord = stitched_coord as fallback."
            )

        print("compute_registered_offset finished.")

    def _flatten_tiles(self) -> List[Tile]:
        return [t for _, _, t in self.iter_tiles_row_major() if t is not None]

    def print_derived_grid_map(self, cell_width: int = 10):
        tiles = self._flatten_tiles()
        if not tiles:
            print("No tiles loaded to create a map.")
            return
        num_rows = self.num_rows
        num_cols = self.num_cols
        CELL_WIDTH = cell_width
        empty_cell = [
            ("-" * CELL_WIDTH).center(CELL_WIDTH),
            ("-" * CELL_WIDTH).center(CELL_WIDTH),
            ("-" * CELL_WIDTH).center(CELL_WIDTH),
        ]
        cell_grid: List[List[List[str]]] = [
            [list(empty_cell) for _ in range(num_cols)] for _ in range(num_rows)
        ]
        for t in tiles:
            x_norm, y_norm = t.normal_coord
            row = self.y_to_row[y_norm]
            col = self.x_to_col[x_norm]
            tile_idx = f"IDX: {t.index:04d}" if t.index is not None else "IDX: XXXX"
            if t.derived_coord is not None:
                dx = int(round(t.derived_coord[0]))
                dy = int(round(t.derived_coord[1]))
                x_line = f"X: {dx}"
                y_line = f"Y: {dy}"
            else:
                x_line = "X: N/A"
                y_line = "Y: N/A"
            cell_grid[row][col] = [
                tile_idx.center(CELL_WIDTH),
                x_line.center(CELL_WIDTH),
                y_line.center(CELL_WIDTH),
            ]
        total_width = num_cols * (CELL_WIDTH + 1) + 1
        separator = "-" * total_width
        print("\n--- Tile Grid Map (Displaying Derived/Corrected Coordinates) ---")
        print(separator)
        for row_idx in range(num_rows):
            line_idx = "|"
            for col_idx in range(num_cols):
                line_idx += f"{cell_grid[row_idx][col_idx][0]}|"
            print(line_idx)
            line_x = "|"
            for col_idx in range(num_cols):
                line_x += f"{cell_grid[row_idx][col_idx][1]}|"
            print(line_x)
            line_y = "|"
            for col_idx in range(num_cols):
                line_y += f"{cell_grid[row_idx][col_idx][2]}|"
            print(line_y)
            print(separator)

    def print_derived_coordinates_comparison(self):
        tiles = self._flatten_tiles()
        if not tiles:
            print("No tiles loaded to print comparison table.")
            return
        print("\n--- Coordinate Comparison Table (Stitched vs. Derived) ---")
        header = f"{'Index':<6} | {'Normal X':<10} | {'Stitched X':<12} | {'Stitched Y':<12} | {'Derived X':<11} | {'Derived Y':<11} | {'Signal':<8}"
        print(header)
        print("-" * len(header))
        sorted_tiles = sorted(
            tiles, key=lambda t: t.index if t.index is not None else float("inf")
        )
        for t in sorted_tiles:
            derived_x = (
                f"{t.derived_coord[0]:.3f}" if t.derived_coord is not None else "N/A"
            )
            derived_y = (
                f"{t.derived_coord[1]:.3f}" if t.derived_coord is not None else "N/A"
            )
            signal_str = f"{t.avg_signal:.1f}" if t.avg_signal is not None else "None"
            idx = f"{t.index}" if t.index is not None else "None"
            print(
                f"{idx:<6} | {t.normal_coord[0]:<10.3f} | {t.stitched_coord[0]:<12.3f} | {t.stitched_coord[1]:<12.3f} | {derived_x:<11} | {derived_y:<11} | {signal_str:<8}"
            )

    def normalize_coordinates(self) -> Tuple[float, float]:
        tiles = self._flatten_tiles()
        if not tiles:
            raise ValueError("No tiles to normalize.")
        derived_coords = [
            (t.derived_coord[0], t.derived_coord[1])
            for t in tiles
            if t.derived_coord is not None
        ]
        if not derived_coords:
            raise ValueError(
                "Derived coordinates are missing for all tiles. Cannot normalize."
            )
        xs, ys = zip(*derived_coords)
        min_x = min(xs)
        min_y = min(ys)
        for t in tiles:
            if t.derived_coord is None:
                t.normalized_coord = None
            else:
                norm_x = round(t.derived_coord[0] - min_x, 3)
                norm_y = round(t.derived_coord[1] - min_y, 3)
                t.normalized_coord = (norm_x, norm_y)
        print(
            f"\nNormalized coordinates set on tiles. Minimum offsets found: X={min_x:.3f}, Y={min_y:.3f}"
        )
        return min_x, min_y

    def export_to_yaml(
        self,
        output_filename: str = "tile_coords_export.yaml",
        use_normalized: bool = True,
    ):
        tiles = self._flatten_tiles()
        if not tiles:
            print("No tiles to export.")
            return
        if use_normalized:
            if any(
                t.normalized_coord is None for t in tiles if t.derived_coord is not None
            ):
                try:
                    self.normalize_coordinates()
                except ValueError as e:
                    print(f"Cannot normalize before export: {e}")
                    return
        yaml_list = []
        for t in tiles:
            if t.index is None:
                continue
            if use_normalized:
                coord = t.normalized_coord
                if coord is None:
                    continue
                x_val, y_val = coord
            else:
                if t.derived_coord is None:
                    continue
                x_val = round(t.derived_coord[0], 3)
                y_val = round(t.derived_coord[1], 3)
            avg_sig_int = int(t.avg_signal) if t.avg_signal is not None else None
            tile_data = {
                "filepath": os.path.basename(t.path),
                "tile_number": t.index,
                "x": x_val,
                "y": y_val,
                "avg_signal": avg_sig_int,
            }
            yaml_list.append(tile_data)
        try:
            with open(output_filename, "w") as f:
                yaml.dump(yaml_list, f, sort_keys=False, default_flow_style=False)
            print(f"Successfully exported {len(yaml_list)} tiles to {output_filename}")
        except Exception as e:
            print(f"Error saving YAML file: {e}")


# --- Loader (left standalone per request) ---
def load_tile_info(
    ideal_coord_file: str | Path,
    stitched_coord_file: str | Path,
    image_base_path: str | Path = ".",
) -> List[Tile]:
    ideal_coord_file = str(Path(ideal_coord_file))
    stitched_coord_file = str(Path(stitched_coord_file))
    image_base_path = str(Path(image_base_path))
    tiles_data: Dict[str, Dict] = {}
    print(f"Loading ideal coordinates from: {ideal_coord_file}")
    try:
        with open(ideal_coord_file, "r") as f:
            for line in f:
                parsed = _parse_line(line)
                if parsed:
                    filename, normal_coord = parsed
                    tiles_data[filename] = {"normal": normal_coord}
    except FileNotFoundError:
        print(f"Error: Ideal coordinate file not found: {ideal_coord_file}")
        return []
    print(f"Found {len(tiles_data)} tiles in ideal file.")
    print(f"Loading stitched coordinates from: {stitched_coord_file}")
    try:
        with open(stitched_coord_file, "r") as f:
            for line in f:
                parsed = _parse_line(line)
                if parsed:
                    filename, stitched_coord = parsed
                    if filename in tiles_data:
                        tiles_data[filename]["stitched"] = stitched_coord
                    else:
                        print(
                            f"Warning: {filename} in stitched file but not in ideal file. Skipping."
                        )
    except FileNotFoundError:
        print(f"Error: Stitched coordinate file not found: {stitched_coord_file}")
        return []
    tile_objects: List[Tile] = []
    for filename, data in tiles_data.items():
        if "normal" in data and "stitched" in data:
            full_path = os.path.join(image_base_path, filename)
            tile_obj = Tile(
                path=full_path,
                normal_coord=data["normal"],
                stitched_coord=data["stitched"],
                avg_signal=None,
            )
            try:
                _ = tile_obj.avg_signal
                if tile_obj.avg_signal is None:
                    print(f"Warning: Could not compute average signal for {filename}.")
            except Exception:
                pass
            tile_objects.append(tile_obj)
        else:
            print(f"Warning: {filename} is missing coordinate data. Skipping.")
    print(f"Successfully created {len(tile_objects)} merged Tile objects.")
    return tile_objects


def process_tile_coordinate(
    ideal_coord_file: str | Path,
    stitched_coord_file: str | Path,
    image_dir: str | Path = ".",
    export: Optional[str | Path] = None,
    export_raw: bool = False,
    threshold: float = 55.0,
    verbose: bool = True,
) -> Tuple[Grid, List[Tile]]:
    """
    Programmatic API mirroring the CLI.

    Parameters
    ----------
    ideal_coord_file: str
        Path to ideal (normal) coordinate file.
    stitched_coord_file: str
        Path to stitched (registered) coordinate file.
    image_dir: str
        Directory where .nii image files live.
    export: Optional[str]
        If provided (string), export YAML to this filename. If None, no export.
    export_raw: bool
        If True, export raw derived coordinates (not normalized).
    threshold: float
        Average-signal threshold for determine reliable tiles (passed to compute_registered_offset).
    verbose: bool
        If True prints progress to stdout.

    Returns
    -------
    (grid, tiles)
        The created Grid and the list of Tile objects.
    """
    if verbose:
        print("--- Starting Tile Processor (run_pipeline) ---")
    ideal_coord_file = str(Path(ideal_coord_file))
    stitched_coord_file = str(Path(stitched_coord_file))
    image_dir = str(Path(image_dir))
    export = str(Path(export)) if export is not None else None

    tiles = load_tile_info(ideal_coord_file, stitched_coord_file, image_dir)
    grid = Grid.from_tiles(tiles)
    if verbose:
        grid.print_tile_map()

    # compute offsets using the provided threshold
    grid.compute_registered_offset(signal_threshold=threshold)

    # normalize (store on tiles) and optionally export
    try:
        grid.normalize_coordinates()
    except ValueError as e:
        if verbose:
            print(f"Normalization skipped / failed: {e}")

    if export:
        # use provided filename or default if empty string passed
        out_name = export if export.strip() else "tile_coords_export.yaml"
        grid.export_to_yaml(output_filename=out_name, use_normalized=not export_raw)

    if verbose:
        grid.print_derived_grid_map()
        grid.print_derived_coordinates_comparison()

    return grid, tiles


# --- CLI wiring ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load and process tile coordinate information from two files."
    )
    parser.add_argument(
        "ideal_coord_file", help="Path to the file with ideal (normal) coordinates."
    )
    parser.add_argument(
        "stitched_coord_file",
        help="Path to the file with stitched (registered) coordinates.",
    )
    parser.add_argument(
        "--image_dir",
        default=".",
        help="Directory path where the .nii files are stored (default: current directory).",
    )

    # New options:
    parser.add_argument(
        "--export",
        nargs="?",
        const="tile_coords_export.yaml",
        default=None,
        help="If supplied, export results to YAML. Optionally provide filename. "
        "If no filename provided, defaults to 'tile_coords_export.yaml'.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=55.0,
        help="Average-signal threshold to mark tiles as reliable (default: 55.0).",
    )
    parser.add_argument(
        "--export-raw",
        action="store_true",
        help="Export raw derived coordinates instead of normalized (use with --export).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose prints (still prints warnings/errors).",
    )

    args = parser.parse_args()

    grid, tiles = process_tile_coordinate(
        args.ideal_coord_file,
        args.stitched_coord_file,
        image_dir=args.image_dir,
        export=args.export,
        export_raw=args.export_raw,
        threshold=args.threshold,
        verbose=not args.quiet,
    )
