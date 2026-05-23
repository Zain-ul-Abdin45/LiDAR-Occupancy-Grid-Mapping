"""
Tier 1: Classical Bayesian occupancy grid mapping.

Accumulates log-odds updates from every LiDAR scan in a scene,
applying the inverse sensor model scan by scan (sequential update).
"""

from .data_loader import NuScenesLoader
from .preprocessor import preprocess
from .occupancy_grid import OccupancyGrid
from .sensor_model import update_grid_from_scan


def run_bayesian_ogm(
    loader: NuScenesLoader,
    scene_name: str,
    grid: OccupancyGrid,
    verbose: bool = True,
) -> OccupancyGrid:
    """
    Build an occupancy grid for the named scene using classical Bayesian OGM.

    Args:
        loader:      Initialised NuScenesLoader
        scene_name:  e.g. 'scene-0061'
        grid:        OccupancyGrid (will be reset before use)
        verbose:     Print progress per scan

    Returns:
        The updated OccupancyGrid (same object, modified in-place)
    """
    scene = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)

    grid.reset()

    for i, sd_token in enumerate(lidar_tokens):
        points = loader.load_lidar_points(sd_token)
        cells = preprocess(points)

        if len(cells) == 0:
            continue

        update_grid_from_scan(grid, cells)

        if verbose:
            print(f"  Scan {i+1:3d}/{len(lidar_tokens)} — "
                  f"{len(cells):5d} points after filter")

    if verbose:
        prob = grid.get_probability()
        occupied = (prob > 0.5).sum()
        print(f"\nScene '{scene_name}': {occupied} cells occupied "
              f"({100*occupied/grid.grid_size**2:.1f}% of grid)")

    return grid
