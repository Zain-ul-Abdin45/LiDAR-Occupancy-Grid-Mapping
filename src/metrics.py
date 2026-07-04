"""
Evaluation metrics for LiDAR Occupancy Grid Mapping.

Metrics implemented:
  IoBB  — Intersection over Bounding Box (recall within annotated boxes)
  NMSE  — Normalized Mean Squared Error, angular-scan version (Önen 2024)

Ground truth construction:
  - nuScenes sample_annotation.json provides 3D bounding boxes (per keyframe).
  - Boxes are yaw-rotated (not axis-aligned) and must be rasterized to BEV cells.
  - We use matplotlib.path.Path.contains_points for rotated rectangle rasterization.

IoBB:
  For each annotated object:
    IoBB_obj = |{cells: P(occ) > threshold} ∩ {box cells}| / |{box cells}|
  Report: mean IoBB across all objects and all scenes.

Angular NMSE (Önen 2024 definition):
  For each bearing angle θ_i:
    d[i]   = actual max free-space distance (from GT or LiDAR range)
    d̂[i]  = estimated free-space distance from the occupancy map
  NMSE = ||d - d̂||² / ||d||²
"""

import numpy as np
from matplotlib.path import Path

# KITTI eval classes (imported by run_kitti_benchmark for consistency)
KITTI_EVAL_CLASSES = {"Car", "Van", "Truck", "Pedestrian", "Cyclist", "Person_sitting"}


# ─────────────────────────── Ground truth construction ──────────────────────

def _box_corners_bev(annotation: dict) -> np.ndarray:
    """
    Compute the 4 BEV (x, y) corners of an annotated 3D bounding box.

    nuScenes annotation fields used:
      translation: [cx, cy, cz]      — box centre in global frame
      size:        [w, l, h]         — width (y), length (x), height
      rotation:    [w, x, y, z]      — quaternion (yaw around z-axis)

    Returns:
        corners: (4, 2) float array of (x, y) positions in global frame.
    """
    cx, cy = annotation["translation"][0], annotation["translation"][1]
    # nuScenes size = [width, length, height] (width=y axis, length=x axis)
    w, l = annotation["size"][0], annotation["size"][1]

    # Extract yaw from quaternion [w, x, y, z]
    q = annotation["rotation"]
    qw, qx, qy, qz = q[0], q[1], q[2], q[3]
    yaw = np.arctan2(2.0 * (qw * qz + qx * qy),
                     1.0 - 2.0 * (qy * qy + qz * qz))

    cos_y, sin_y = np.cos(yaw), np.sin(yaw)

    # Half-extents along local x (length) and y (width)
    hl, hw = l / 2.0, w / 2.0

    # Four corners in local box frame, then rotated + translated
    local = np.array([
        [ hl,  hw],
        [ hl, -hw],
        [-hl, -hw],
        [-hl,  hw],
    ])
    rot = np.array([[cos_y, -sin_y], [sin_y, cos_y]])
    global_corners = local @ rot.T + np.array([cx, cy])
    return global_corners


def build_gt_map(annotations: list[dict],
                 grid_size: int,
                 cell_size: float,
                 origin_xy: np.ndarray | None = None) -> np.ndarray:
    """
    Rasterize 3D bounding boxes to a binary BEV occupancy map.

    Args:
        annotations: list of annotation dicts (from sample_annotation.json)
        grid_size:   cells per side (e.g. 80)
        cell_size:   metres per cell (e.g. 0.5)
        origin_xy:   (2,) world-frame origin for the grid centre.
                     For single-scan mode pass the ego_pose translation[:2].
                     For world-frame mode pass the trajectory centroid.

    Returns:
        gt_map: (grid_size, grid_size) bool array — True inside any box.
    """
    half = grid_size * cell_size / 2.0
    gt_map = np.zeros((grid_size, grid_size), dtype=bool)

    if origin_xy is None:
        origin_xy = np.zeros(2)

    # Build a meshgrid of cell centre positions in the world frame
    xs = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    ys = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    gx, gy = np.meshgrid(xs, ys)
    # Row 0 is the top of the grid (y = +half), so flip y
    cell_centres = np.stack([
        gx.ravel() + origin_xy[0],
        (np.flipud(gy)).ravel() + origin_xy[1],
    ], axis=1)  # (N_cells, 2)

    for ann in annotations:
        corners = _box_corners_bev(ann)  # (4, 2) global frame
        path = Path(corners)
        inside = path.contains_points(cell_centres)  # (N_cells,)
        gt_map |= inside.reshape(grid_size, grid_size)

    return gt_map


# ─────────────────────────── IoBB metric ────────────────────────────────────

def compute_iobb(prob_map: np.ndarray,
                 annotations: list[dict],
                 grid_size: int,
                 cell_size: float,
                 origin_xy: np.ndarray | None = None,
                 threshold: float = 0.5) -> dict:
    """
    Compute Intersection over Bounding Box.

    IoBB = |occupied_cells ∩ box_cells| / |box_cells|
    Higher is better. Range [0, 1].

    Args:
        prob_map:    (grid_size, grid_size) float — P(occupied) per cell
        annotations: list of annotation dicts for this keyframe
        grid_size, cell_size: grid parameters
        origin_xy:  world-frame grid centre (see build_gt_map)
        threshold:  P threshold for "occupied" decision (default 0.5)

    Returns:
        dict with keys: iobb_mean, iobb_per_object (list), n_objects
    """
    if not annotations:
        return {"iobb_mean": float("nan"), "iobb_per_object": [], "n_objects": 0}

    half = grid_size * cell_size / 2.0
    occupied = prob_map > threshold

    if origin_xy is None:
        origin_xy = np.zeros(2)

    xs = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    ys = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    gx, gy = np.meshgrid(xs, ys)
    cell_centres = np.stack([
        gx.ravel() + origin_xy[0],
        (np.flipud(gy)).ravel() + origin_xy[1],
    ], axis=1)

    iobb_values = []
    for ann in annotations:
        corners = _box_corners_bev(ann)
        path = Path(corners)
        box_mask = path.contains_points(cell_centres).reshape(grid_size, grid_size)
        n_box = box_mask.sum()
        if n_box == 0:
            continue
        n_hit = (occupied & box_mask).sum()
        iobb_values.append(n_hit / n_box)

    if not iobb_values:
        return {"iobb_mean": float("nan"), "iobb_per_object": [], "n_objects": 0}

    return {
        "iobb_mean": float(np.mean(iobb_values)),
        "iobb_per_object": iobb_values,
        "n_objects": len(iobb_values),
    }


# ─────────────────────────── Precision metric ───────────────────────────────

def compute_precision(prob_map: np.ndarray,
                      annotations: list[dict],
                      grid_size: int,
                      cell_size: float,
                      origin_xy: np.ndarray | None = None,
                      threshold: float = 0.5) -> dict:
    """
    Compute map precision: fraction of predicted-occupied cells that fall inside GT boxes.

    Precision = |{cells: P > threshold} ∩ {box cells}| / |{cells: P > threshold}|

    Higher is better. Rewards sparse, clean maps (PC-SBL's natural advantage over
    the dense Bayesian baseline which has many free-space false positives).

    Args:
        prob_map:    (grid_size, grid_size) float — P(occupied) per cell
        annotations: list of annotation dicts for this keyframe
        grid_size, cell_size: grid parameters
        origin_xy:  world-frame grid centre
        threshold:  P threshold for "occupied" decision (default 0.5)

    Returns:
        dict with keys: precision, n_predicted, n_true_positive
    """
    if not annotations:
        return {"precision": float("nan"), "n_predicted": 0, "n_true_positive": 0}

    half = grid_size * cell_size / 2.0
    occupied = prob_map > threshold
    n_predicted = int(occupied.sum())

    if n_predicted == 0:
        return {"precision": float("nan"), "n_predicted": 0, "n_true_positive": 0}

    if origin_xy is None:
        origin_xy = np.zeros(2)

    gt_map = build_gt_map(annotations, grid_size, cell_size, origin_xy)
    n_true_positive = int((occupied & gt_map).sum())
    precision = n_true_positive / n_predicted

    return {
        "precision": float(precision),
        "n_predicted": n_predicted,
        "n_true_positive": n_true_positive,
    }


# ─────────────────────────── Angular NMSE metric ────────────────────────────

def compute_angular_nmse(prob_map: np.ndarray,
                          points_xy: np.ndarray,
                          grid_size: int,
                          cell_size: float,
                          n_angles: int = 360,
                          ego_xy: np.ndarray | None = None,
                          threshold: float = 0.5) -> dict:
    """
    Angular-scan NMSE as defined in Önen 2024.

    For each angular bin θ_i:
      d[i]  = max free-space range from LiDAR hit (ground truth)
      d̂[i] = estimated free-space range from occupancy map
              (distance to first cell with P > 0.5)

    NMSE = ||d - d̂||² / ||d||²    (lower is better)

    Args:
        prob_map:   (grid_size, grid_size) float — P(occupied)
        points_xy:  (M, 2) float — BEV (x, y) positions of LiDAR hits (ego frame)
        grid_size, cell_size: grid parameters
        n_angles:   number of angular bins (default 360 → 1° resolution)
        ego_xy:     (2,) ego position in the coordinate frame of points_xy.
                    Use np.zeros(2) for single-scan ego-centric mode.

    Returns:
        dict with keys: nmse, d_gt (n_angles,), d_est (n_angles,)
    """
    if ego_xy is None:
        ego_xy = np.zeros(2)

    # LiDAR hit angles and ranges from ego
    rel = points_xy - ego_xy
    angles_hit = np.arctan2(rel[:, 1], rel[:, 0])      # (-π, π)
    ranges_hit = np.linalg.norm(rel, axis=1)

    # Bin angle into [0, n_angles) slots
    bin_size = 2 * np.pi / n_angles
    bins = ((angles_hit + np.pi) / bin_size).astype(int) % n_angles

    half = grid_size * cell_size / 2.0

    # Ground truth: max range per angular bin (take closest hit as range proxy)
    d_gt = np.zeros(n_angles)
    for b in range(n_angles):
        mask = bins == b
        if mask.any():
            d_gt[b] = ranges_hit[mask].min()   # closest LiDAR return = free-space limit

    # Estimated free-space: walk each angular ray through the grid until P > 0.5
    ego_col = int(np.clip((ego_xy[0] + half) / cell_size, 0, grid_size - 1))
    ego_row = int(np.clip((half - ego_xy[1]) / cell_size, 0, grid_size - 1))

    d_est = np.zeros(n_angles)
    occupied = prob_map > threshold
    # Start walker beyond vehicle body (same min_range as preprocessor)
    start_step = 1.5

    for b in range(n_angles):
        if d_gt[b] == 0.0:
            continue
        theta = -np.pi + b * bin_size
        # Walk along ray until occupied or out of bounds
        for step in np.arange(start_step, half * 1.42, cell_size / 2):
            x_w = ego_xy[0] + step * np.cos(theta)
            y_w = ego_xy[1] + step * np.sin(theta)
            r = int(np.clip((half - y_w) / cell_size, 0, grid_size - 1))
            c = int(np.clip((x_w + half) / cell_size, 0, grid_size - 1))
            if occupied[r, c]:
                d_est[b] = step
                break
        else:
            d_est[b] = half * 1.42  # ray reached map boundary

    # Only evaluate bins that have GT measurements
    valid = d_gt > 0.0
    if not valid.any():
        return {"nmse": float("nan"), "d_gt": d_gt, "d_est": d_est}

    dg = d_gt[valid]
    de = d_est[valid]
    nmse = float(np.sum((dg - de) ** 2) / np.sum(dg ** 2))

    return {"nmse": nmse, "d_gt": d_gt, "d_est": d_est}


# ─────────────────────────── KITTI IoBB metric ──────────────────────────────

def _kitti_box_bev(cx_ego: float, cy_ego: float,
                   w: float, l: float, rot_y: float) -> np.ndarray:
    """
    Compute 4 BEV corners of a KITTI box in z-up ego frame.

    KITTI rotation_y is the rotation around the camera y-axis (= yaw in BEV).
    After cam→ego rotation (R_cam_to_ego): ego_x = x_cam, ego_y = z_cam,
    so the BEV plane is (ego_x, ego_y) and yaw direction is preserved.

    Args:
        cx_ego, cy_ego: box centre in ego BEV (metres from ego origin)
        w, l:           KITTI width and length in metres
        rot_y:          KITTI rotation_y (radians)

    Returns:
        (4, 2) ndarray — corners in ego BEV frame
    """
    cos_r, sin_r = np.cos(rot_y), np.sin(rot_y)
    hw, hl = w / 2.0, l / 2.0
    # KITTI local box: length along z (forward), width along x (right)
    local = np.array([
        [ hl,  hw],
        [ hl, -hw],
        [-hl, -hw],
        [-hl,  hw],
    ])
    rot = np.array([[cos_r, -sin_r], [sin_r, cos_r]])
    corners = local @ rot.T + np.array([cx_ego, cy_ego])
    return corners


def compute_iobb_kitti(prob_map: np.ndarray,
                       kitti_labels: list[dict],
                       grid_size: int,
                       cell_size: float,
                       threshold: float = 0.5) -> dict:
    """
    Compute IoBB for KITTI annotations.

    Box centres are expressed in KITTI camera frame (x_cam, y_cam, z_cam).
    After the cam→ego rotation applied in KITTILoader.get_calibrated_sensor():
        ego_x = x_cam   (right)
        ego_y = z_cam   (forward)
    No additional origin_xy shift needed — ego IS the camera at each frame.

    Args:
        prob_map:     (grid_size, grid_size) float — P(occupied) per cell
        kitti_labels: list of dicts from KITTILoader.get_annotations_for_frame()
        grid_size, cell_size: grid parameters (must match how prob_map was built)
        threshold:    P decision threshold (default 0.5)

    Returns:
        dict with keys: iobb_mean, iobb_per_object (list), n_objects
    """
    if not kitti_labels:
        return {"iobb_mean": float("nan"), "iobb_per_object": [], "n_objects": 0}

    half = grid_size * cell_size / 2.0
    occupied = prob_map > threshold

    # Build cell-centre grid (ego frame, same convention as nuScenes metrics)
    xs = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    ys = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    gx, gy = np.meshgrid(xs, ys)
    cell_centres = np.stack([
        gx.ravel(),
        (np.flipud(gy)).ravel(),
    ], axis=1)  # (N_cells, 2)

    iobb_values = []
    for lbl in kitti_labels:
        x_cam, _, z_cam = lbl["location"]  # y_cam is height (down), not used
        # cam→ego: ego_x = x_cam, ego_y = z_cam (forward)
        cx_ego, cy_ego = float(x_cam), float(z_cam)

        # Skip boxes outside the grid
        if abs(cx_ego) > half or abs(cy_ego) > half:
            continue

        _, w, l = lbl["dimensions"]   # (h, w, l)
        rot_y = lbl["rotation_y"]
        corners = _kitti_box_bev(cx_ego, cy_ego, w, l, rot_y)

        path = Path(corners)
        box_mask = path.contains_points(cell_centres).reshape(grid_size, grid_size)
        n_box = int(box_mask.sum())
        if n_box == 0:
            continue
        n_hit = int((occupied & box_mask).sum())
        iobb_values.append(n_hit / n_box)

    if not iobb_values:
        return {"iobb_mean": float("nan"), "iobb_per_object": [], "n_objects": 0}

    return {
        "iobb_mean": float(np.mean(iobb_values)),
        "iobb_per_object": iobb_values,
        "n_objects": len(iobb_values),
    }


def compute_precision_kitti(prob_map: np.ndarray,
                             kitti_labels: list[dict],
                             grid_size: int,
                             cell_size: float,
                             threshold: float = 0.5) -> dict:
    """
    Precision for KITTI: fraction of predicted-occupied cells inside GT boxes.

    Returns:
        dict with keys: precision, n_predicted, n_true_positive
    """
    if not kitti_labels:
        return {"precision": float("nan"), "n_predicted": 0, "n_true_positive": 0}

    half = grid_size * cell_size / 2.0
    occupied = prob_map > threshold
    n_predicted = int(occupied.sum())

    if n_predicted == 0:
        return {"precision": float("nan"), "n_predicted": 0, "n_true_positive": 0}

    xs = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    ys = np.linspace(-half + cell_size / 2, half - cell_size / 2, grid_size)
    gx, gy = np.meshgrid(xs, ys)
    cell_centres = np.stack([
        gx.ravel(),
        (np.flipud(gy)).ravel(),
    ], axis=1)

    gt_map = np.zeros((grid_size, grid_size), dtype=bool)
    for lbl in kitti_labels:
        x_cam, _, z_cam = lbl["location"]
        cx_ego, cy_ego = float(x_cam), float(z_cam)
        if abs(cx_ego) > half or abs(cy_ego) > half:
            continue
        _, w, l = lbl["dimensions"]
        corners = _kitti_box_bev(cx_ego, cy_ego, w, l, lbl["rotation_y"])
        path = Path(corners)
        box_mask = path.contains_points(cell_centres).reshape(grid_size, grid_size)
        gt_map |= box_mask

    n_true_positive = int((occupied & gt_map).sum())
    return {
        "precision": float(n_true_positive / n_predicted),
        "n_predicted": n_predicted,
        "n_true_positive": n_true_positive,
    }
