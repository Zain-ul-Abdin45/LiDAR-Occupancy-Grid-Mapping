"""
Preprocessing pipeline: raw LiDAR points → grid cell indices.

Steps (from presentation slide 8):
  1. Height filter:  keep z ∈ [z_min, z_max]
  2. Range filter:   keep |x| ≤ half_range AND |y| ≤ half_range
  3. 3D → 2D:        drop z coordinate (Bird's Eye View)
  4. Discretize:     map (x, y) → (row, col) on the occupancy grid
"""

import numpy as np


# Default parameters matching the presentation spec
Z_MIN = -2.0       # m — remove ground clutter
Z_MAX = +3.0       # m — remove aerial noise
HALF_RANGE = 20.0  # m — grid spans ±20 m
GRID_SIZE = 80     # cells per side (40 m / 0.5 m)
CELL_SIZE = 0.5    # m per cell


def height_filter(points: np.ndarray, z_min: float = Z_MIN, z_max: float = Z_MAX) -> np.ndarray:
    """Keep points with z ∈ [z_min, z_max]."""
    mask = (points[:, 2] >= z_min) & (points[:, 2] <= z_max)
    return points[mask]


def range_filter(points: np.ndarray, half_range: float = HALF_RANGE) -> np.ndarray:
    """Keep points within ±half_range metres in both x and y."""
    mask = (
        (np.abs(points[:, 0]) <= half_range) &
        (np.abs(points[:, 1]) <= half_range)
    )
    return points[mask]


def project_bev(points: np.ndarray) -> np.ndarray:
    """Drop z; return (N, 2) array of (x, y) pairs."""
    return points[:, :2].copy()


def discretize(xy: np.ndarray,
               half_range: float = HALF_RANGE,
               cell_size: float = CELL_SIZE,
               grid_size: int = GRID_SIZE) -> np.ndarray:
    """
    Map continuous (x, y) coordinates to integer (row, col) grid indices.

    Coordinate convention:
      x = forward  → increasing col
      y = left     → decreasing row
    Ego vehicle sits at grid center: (row=40, col=40) for an 80×80 grid.

    Returns:
        cells: (N, 2) int array of (row, col), clipped to [0, grid_size-1]
    """
    col = np.floor((xy[:, 0] + half_range) / cell_size).astype(int)
    row = np.floor((half_range - xy[:, 1]) / cell_size).astype(int)

    # Clip to valid grid range
    col = np.clip(col, 0, grid_size - 1)
    row = np.clip(row, 0, grid_size - 1)

    return np.stack([row, col], axis=1)


def preprocess(points: np.ndarray,
               z_min: float = Z_MIN,
               z_max: float = Z_MAX,
               half_range: float = HALF_RANGE,
               cell_size: float = CELL_SIZE,
               grid_size: int = GRID_SIZE) -> np.ndarray:
    """
    Full preprocessing pipeline: raw points → grid cell (row, col) indices.

    Args:
        points: (N, 5) raw LiDAR array from data_loader

    Returns:
        cells: (M, 2) int array of (row, col) for M surviving points
    """
    pts = height_filter(points, z_min, z_max)
    pts = range_filter(pts, half_range)
    xy = project_bev(pts)
    cells = discretize(xy, half_range, cell_size, grid_size)
    return cells
