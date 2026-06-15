"""
Tier 1: Classical Bayesian occupancy grid mapping.

Two modes:
  single_scan  — one keyframe only (ego-centric, no motion smearing)
  accumulated  — all keyframes in world-frame coordinates (ego_pose applied)

Why two modes:
  LiDAR data is in the ego-vehicle frame at each scan's timestamp.
  Stacking scans in ego-frame while the vehicle moves causes free/occupied
  evidence to be written at contradictory grid positions → smearing.

  Single-scan: cleanest demo of the Bayesian ISM for a presentation.
  World-frame: correct multi-scan accumulation using ego_pose translation.
"""

import numpy as np

from .data_loader import NuScenesLoader
from .preprocessor import (
    height_filter, range_filter, project_bev, discretize, preprocess,
)
from .occupancy_grid import OccupancyGrid
from .sensor_model import update_grid_from_scan


def run_single_scan(
    loader: NuScenesLoader,
    scene_name: str,
    grid: OccupancyGrid,
    scan_index: int = 0,
    verbose: bool = True,
) -> OccupancyGrid:
    """
    Build an occupancy grid from ONE keyframe LiDAR scan (ego-centric).

    This is the cleanest demo of the Bayesian ISM: one snapshot of the world,
    no motion smearing. Use this for the June 01 presentation.

    Args:
        scan_index: which keyframe to use (0 = first frame of the scene)
    """
    scene = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)

    if scan_index >= len(lidar_tokens):
        raise ValueError(f"scan_index {scan_index} out of range "
                         f"(scene has {len(lidar_tokens)} scans)")

    grid.reset()

    sd_token = lidar_tokens[scan_index]
    points = loader.load_lidar_points(sd_token)
    cs = loader.get_calibrated_sensor(sd_token)
    cells = preprocess(points, cs=cs)

    if verbose:
        print(f"Single scan {scan_index+1}/{len(lidar_tokens)} — "
              f"{len(cells)} points after filter (sensor→ego transform applied)")

    update_grid_from_scan(grid, cells)

    if verbose:
        prob = grid.get_probability()
        occupied = (prob > 0.5).sum()
        print(f"Occupied cells: {occupied} / {grid.grid_size**2} "
              f"({100*occupied/grid.grid_size**2:.1f}%)")

    return grid


def run_bayesian_ogm(
    loader: NuScenesLoader,
    scene_name: str,
    grid: OccupancyGrid,
    world_frame: bool = True,
    verbose: bool = True,
) -> OccupancyGrid:
    """
    Build an occupancy grid by accumulating all LiDAR scans for a scene.

    Args:
        world_frame: If True (recommended), subtract each scan's ego_pose
                     translation so all scans are accumulated in a common
                     world-centred grid centered on the trajectory centroid.
                     If False, all scans stack in ego-frame → smearing.
    """
    scene = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)

    grid.reset()
    half_range = grid.grid_size * grid.cell_size / 2  # metres (e.g. 80*0.5/2 = 20 m)

    if world_frame:
        # Center the map on trajectory centroid so all scans land in the grid
        all_xy = np.array([
            loader.get_ego_pose(t)["translation"][:2]
            for t in lidar_tokens
        ])
        origin_xy = all_xy.mean(axis=0)

    for i, sd_token in enumerate(lidar_tokens):
        points = loader.load_lidar_points(sd_token)
        cs = loader.get_calibrated_sensor(sd_token)

        from .preprocessor import transform_to_ego
        pts = transform_to_ego(points, cs)
        pts = height_filter(pts)
        pts = range_filter(pts, half_range)
        xy = project_bev(pts)

        if world_frame:
            if len(xy) == 0:
                continue
            ego_pose = loader.get_ego_pose(sd_token)
            ego_xy = np.array(ego_pose["translation"][:2])
            offset = ego_xy - origin_xy
            xy_world = xy + offset
            mask = (
                (np.abs(xy_world[:, 0]) <= half_range) &
                (np.abs(xy_world[:, 1]) <= half_range)
            )
            xy_world = xy_world[mask]
            cells = discretize(xy_world, half_range, grid.cell_size, grid.grid_size)
            if len(cells) == 0:
                continue
            ego_col = int(np.clip((offset[0] + half_range) / grid.cell_size, 0, grid.grid_size - 1))
            ego_row = int(np.clip((half_range - offset[1]) / grid.cell_size, 0, grid.grid_size - 1))
            update_grid_from_scan(grid, cells, ego_row=ego_row, ego_col=ego_col)
        else:
            cells = discretize(xy, half_range, grid.cell_size, grid.grid_size)
            if len(cells) == 0:
                continue
            update_grid_from_scan(grid, cells)

        if verbose:
            print(f"  Scan {i+1:3d}/{len(lidar_tokens)} — "
                  f"{len(cells):5d} points")

    if verbose:
        prob = grid.get_probability()
        occupied = (prob > 0.5).sum()
        print(f"\nScene '{scene_name}': {occupied} cells occupied "
              f"({100*occupied/grid.grid_size**2:.1f}% of grid)")

    return grid
