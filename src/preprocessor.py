"""
Preprocessing pipeline: raw LiDAR points → grid cell indices.

Steps:
  0. (Optional) Sensor-to-ego transform: rotate + translate from sensor frame to ego frame.
  1. Ground removal: drop points below z_ground + margin (in ego frame, ground ≈ z=0).
  2. Height filter:  keep z ∈ [z_min, z_max] (ego frame, above ground up to roof level).
  3. Range filter:   keep |x| ≤ half_range AND |y| ≤ half_range.
  4. 3D → 2D:        drop z coordinate (Bird's Eye View).
  5. Discretize:     map (x, y) → (row, col) on the occupancy grid.

Coordinate frames (nuScenes):
  - Raw .pcd.bin points are in the LiDAR SENSOR frame.
  - Sensor frame → ego frame via calibrated_sensor (rotation + translation).
  - Ego origin is at the rear-axle midpoint on the ground plane (z=0 in ego frame).
  - LIDAR_TOP sensor is ~1.84 m above ego origin.
  - After transform: ground points are at z ≈ 0, not z ≈ -1.84 m.
"""

import numpy as np
from scipy.spatial.transform import Rotation


# Default parameters (all in ego frame after sensor-to-ego transform)
Z_GROUND = 0.0     # m — ego origin is on the ground plane
GROUND_MARGIN = 0.3  # m — drop points within 0.3 m of ground to remove road surface
Z_MIN = 0.3        # m — in ego frame: just above ground (= Z_GROUND + GROUND_MARGIN)
Z_MAX = +3.0       # m — in ego frame: above roof of typical vehicle
HALF_RANGE = 20.0  # m — grid spans ±20 m
GRID_SIZE = 80     # cells per side (40 m / 0.5 m)
CELL_SIZE = 0.5    # m per cell


def transform_to_ego(points: np.ndarray, cs: dict) -> np.ndarray:
    """
    Transform LiDAR points from sensor frame to ego vehicle frame.

    Args:
        points: (N, 5) raw points in sensor frame [x, y, z, intensity, ring]
        cs:     calibrated_sensor dict with keys:
                  'rotation':    [w, x, y, z] quaternion
                  'translation': [x, y, z] in metres

    Returns:
        (N, 5) points with xyz in ego frame, intensity/ring unchanged.
    """
    rot = cs["rotation"]
    if isinstance(rot, np.ndarray) and rot.shape == (3, 3):
        # Pre-built rotation matrix (e.g. from KITTILoader)
        R = rot.astype(np.float64)
    else:
        # nuScenes quaternion [w, x, y, z]; scipy expects [x, y, z, w]
        q = rot
        R = Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()
    t = np.array(cs["translation"], dtype=np.float64)

    xyz = points[:, :3].astype(np.float64)
    xyz_ego = xyz @ R.T + t  # (N, 3)

    result = points.copy().astype(np.float64)
    result[:, :3] = xyz_ego
    return result.astype(np.float32)


def ground_removal(points: np.ndarray, z_min: float = Z_MIN) -> np.ndarray:
    """
    Remove ground-level points (in ego frame, ground is at z ≈ 0).

    Drops all points with z < z_min. Default z_min=0.3 m removes the road
    surface (z ≈ 0) while keeping obstacles like vehicle bodies and walls.
    Call this AFTER transform_to_ego so z is in the ego frame.
    """
    return points[points[:, 2] >= z_min]


def height_filter(points: np.ndarray, z_min: float = Z_MIN, z_max: float = Z_MAX) -> np.ndarray:
    """Keep points with z ∈ [z_min, z_max] (ego frame)."""
    mask = (points[:, 2] >= z_min) & (points[:, 2] <= z_max)
    return points[mask]


MIN_RANGE = 1.5   # m — exclude vehicle body / LiDAR mount self-returns

def range_filter(points: np.ndarray,
                 half_range: float = HALF_RANGE,
                 min_range: float = MIN_RANGE) -> np.ndarray:
    """
    Keep points within ±half_range metres in both x and y,
    and farther than min_range from the ego origin in the BEV plane.

    min_range removes vehicle-body self-returns that cluster near (x=0, y=0)
    after the sensor-to-ego transform. Without this, the ego grid cell gets
    spurious occupied updates from the roof/mount structure.
    """
    radial = np.sqrt(points[:, 0] ** 2 + points[:, 1] ** 2)
    mask = (
        (np.abs(points[:, 0]) <= half_range) &
        (np.abs(points[:, 1]) <= half_range) &
        (radial >= min_range)
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
               grid_size: int = GRID_SIZE,
               cs: dict | None = None) -> np.ndarray:
    """
    Full preprocessing pipeline: raw points → grid cell (row, col) indices.

    Args:
        points: (N, 5) raw LiDAR array from data_loader (in sensor frame)
        cs:     calibrated_sensor dict; if provided, transforms to ego frame first.
                If None, points are assumed to already be in a usable frame.

    Returns:
        cells: (M, 2) int array of (row, col) for M surviving points
    """
    pts = points
    if cs is not None:
        pts = transform_to_ego(pts, cs)
    pts = height_filter(pts, z_min, z_max)   # z_min=0.3 removes ground after ego transform
    pts = range_filter(pts, half_range)
    xy = project_bev(pts)
    cells = discretize(xy, half_range, cell_size, grid_size)
    return cells
