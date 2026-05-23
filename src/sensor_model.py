"""
Inverse sensor model: maps each LiDAR hit to log-odds updates on the grid.

Algorithm (per point):
  1. Bresenham line from ego center → terminal cell (the LiDAR hit)
  2. All cells along the ray (excluding terminal): FREE update
  3. Terminal cell: OCCUPIED update

Log-odds values from Stachniss SLAM lecture:
  l_occ  = log(0.7 / 0.3) ≈ +0.847
  l_free = log(0.3 / 0.7) ≈ −0.847
"""

import numpy as np
from .occupancy_grid import OccupancyGrid

L_OCC = np.log(0.7 / 0.3)   # +0.847
L_FREE = np.log(0.3 / 0.7)  # -0.847


def bresenham(r0: int, c0: int, r1: int, c1: int):
    """
    Yield (row, col) for every cell on the line from (r0,c0) to (r1,c1).
    Standard integer Bresenham algorithm — no floating point in the inner loop.
    """
    dr = abs(r1 - r0)
    dc = abs(c1 - c0)
    sr = 1 if r1 > r0 else -1
    sc = 1 if c1 > c0 else -1
    err = dr - dc

    r, c = r0, c0
    while True:
        yield r, c
        if r == r1 and c == c1:
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc
            r += sr
        if e2 < dr:
            err += dr
            c += sc


def update_grid_from_scan(grid: OccupancyGrid, cells: np.ndarray):
    """
    Apply the inverse sensor model for an entire preprocessed scan.

    Args:
        grid:  OccupancyGrid to update in-place
        cells: (M, 2) int array of (row, col) LiDAR hit locations
    """
    ego_r = grid.ego_row
    ego_c = grid.ego_col
    grid_size = grid.grid_size

    free_rows = []
    free_cols = []

    for row, col in cells:
        ray = list(bresenham(ego_r, ego_c, int(row), int(col)))

        # All cells along ray except the terminal one are free
        for r, c in ray[:-1]:
            if 0 <= r < grid_size and 0 <= c < grid_size:
                free_rows.append(r)
                free_cols.append(c)

        # Terminal cell is occupied
        if 0 <= row < grid_size and 0 <= col < grid_size:
            grid.update(
                np.array([row], dtype=int),
                np.array([col], dtype=int),
                L_OCC
            )

    # Batch all free updates for efficiency
    if free_rows:
        grid.update(
            np.array(free_rows, dtype=int),
            np.array(free_cols, dtype=int),
            L_FREE
        )
