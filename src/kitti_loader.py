"""
KITTI 3D Object Detection — data loader.

Interface mirrors NuScenesLoader so that all downstream pipeline modules
(preprocessor, bayesian_ogm, pc_sbl, metrics) work unchanged.

Coordinate frame fix
--------------------
KITTI calib.txt provides Tr_velo_to_cam (LiDAR → left camera):
  camera frame: x=right, y=down, z=forward

We need z-up (same as nuScenes ego frame). A fixed rotation is composed
into the returned calibration dict so that transform_to_ego() in
preprocessor.py produces a z-up point cloud automatically:

  R_cam_to_ego = [[1,  0,  0],   x stays x
                   [0,  0,  1],   z(fwd) → y(fwd)
                   [0, -1,  0]]   -y(down) → z(up)

Annotations
-----------
label_2/*.txt format (one object per line):
  class truncated occluded alpha [2D box x4] height width length x y z rotation_y

(x, y, z) is the box centre in camera coordinates (camera 2, left colour).
After cam→ego rotation: ego_x = x_cam, ego_y = z_cam (forward).
"""

import os
import numpy as np


# Fixed rotation: KITTI camera frame → z-up ego frame
_R_CAM_TO_EGO = np.array([
    [1,  0,  0],
    [0,  0,  1],
    [0, -1,  0],
], dtype=np.float64)

# Only annotate these KITTI classes (skip DontCare, Misc, etc.)
KITTI_EVAL_CLASSES = {"Car", "Van", "Truck", "Pedestrian", "Cyclist", "Person_sitting"}


class KITTILoader:
    """
    Loader for KITTI 3D Object Detection training split.

    data_root: path to kitti/training/ directory that contains
               velodyne/, calib/, and label_2/ subdirectories.
    """

    def __init__(self, data_root: str):
        self.data_root = data_root
        self._velodyne_dir = os.path.join(data_root, "velodyne")
        self._calib_dir = os.path.join(data_root, "calib")
        self._label_dir = os.path.join(data_root, "label_2")
        self._frame_ids = None  # lazy

    def list_frames(self) -> list[str]:
        """
        Return sorted list of frame IDs that have both a velodyne scan
        and a label file (i.e. training frames with annotations).
        """
        if self._frame_ids is not None:
            return self._frame_ids

        velo_ids = {
            os.path.splitext(f)[0]
            for f in os.listdir(self._velodyne_dir)
            if f.endswith(".bin")
        }
        label_ids = {
            os.path.splitext(f)[0]
            for f in os.listdir(self._label_dir)
            if f.endswith(".txt")
        }
        self._frame_ids = sorted(velo_ids & label_ids)
        return self._frame_ids

    def load_lidar_points(self, frame_id: str) -> np.ndarray:
        """
        Load raw LiDAR point cloud in sensor frame.

        Returns:
            (N, 4) float32 — columns: x, y, z, intensity
            Points are in the Velodyne LiDAR sensor frame.
            Pass to preprocessor.transform_to_ego() with get_calibrated_sensor()
            to convert to ego frame before any filtering.
        """
        path = os.path.join(self._velodyne_dir, f"{frame_id}.bin")
        return np.fromfile(path, dtype=np.float32).reshape(-1, 4)

    def get_calibrated_sensor(self, frame_id: str) -> dict:
        """
        Parse calib.txt and return a calibration dict compatible with
        preprocessor.transform_to_ego().

        The returned dict has:
            'rotation':    (3, 3) ndarray — maps LiDAR sensor → z-up ego
            'translation': (3,)   ndarray — in metres

        Internally this is Tr_velo_to_cam composed with R_cam_to_ego so
        that the downstream height_filter (z ∈ [0.3, 3.0]) works correctly.
        """
        calib = self._parse_calib(frame_id)
        Tr = calib["Tr_velo_to_cam"]  # (3, 4)
        Tr_R = Tr[:, :3]              # (3, 3)
        Tr_t = Tr[:, 3]               # (3,)

        # Compose: LiDAR → camera → z-up ego
        R_combined = _R_CAM_TO_EGO @ Tr_R
        t_combined = _R_CAM_TO_EGO @ Tr_t

        return {"rotation": R_combined, "translation": t_combined}

    def get_ego_pose(self, frame_id: str) -> None:
        """
        KITTI object-detection split has no per-frame ego pose.
        Returns None to guard against multi-frame code paths.
        """
        return None

    def get_annotations_for_frame(self, frame_id: str) -> list[dict]:
        """
        Parse label_2/XXXXXX.txt and return list of annotation dicts.

        Each dict contains:
            'class':      str   — object category
            'dimensions': (h, w, l) float — height, width, length in metres
            'location':   (x, y, z) float — box centre in KITTI camera frame
            'rotation_y': float — yaw in camera frame (rotation around y-axis)
            'truncated':  float — truncation level 0..1
            'occluded':   int   — occlusion level 0..3

        location is in camera coordinates (x=right, y=down, z=forward).
        Use compute_iobb_kitti() which handles the cam→ego projection.
        Only objects in KITTI_EVAL_CLASSES are returned (DontCare excluded).
        """
        path = os.path.join(self._label_dir, f"{frame_id}.txt")
        annotations = []
        with open(path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 15:
                    continue
                cls = parts[0]
                if cls not in KITTI_EVAL_CLASSES:
                    continue
                annotations.append({
                    "class":      cls,
                    "truncated":  float(parts[1]),
                    "occluded":   int(parts[2]),
                    "dimensions": (float(parts[8]), float(parts[9]), float(parts[10])),
                    "location":   (float(parts[11]), float(parts[12]), float(parts[13])),
                    "rotation_y": float(parts[14]),
                })
        return annotations

    # ─────────────────────────── internal helpers ────────────────────────────

    def _parse_calib(self, frame_id: str) -> dict:
        """Parse calib.txt into a dict of named (3, 4) matrices."""
        path = os.path.join(self._calib_dir, f"{frame_id}.txt")
        calib = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, vals = line.split(":", 1)
                nums = np.array([float(v) for v in vals.split()], dtype=np.float64)
                if nums.size == 12:
                    calib[key.strip()] = nums.reshape(3, 4)
                elif nums.size == 9:
                    calib[key.strip()] = nums.reshape(3, 3)
                else:
                    calib[key.strip()] = nums
        return calib
