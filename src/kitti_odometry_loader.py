"""
KITTI Odometry — data loader.

Provides the same duck-typed interface as NuScenesLoader so that
multiframe.py (load_window / multiframe_t1 / multiframe_t2) works
unchanged on KITTI Odometry sequences.

Directory layout expected
--------------------------
kitti_odometry/
└── dataset/
    ├── sequences/
    │   ├── 00/
    │   │   ├── velodyne/        ← 000000.bin … (float32, N×4: x,y,z,intensity)
    │   │   ├── calib.txt        ← one calib per sequence
    │   │   └── times.txt        ← timestamps (not used by pipeline)
    │   ├── 01/ …
    │   └── …
    └── poses/
        ├── 00.txt               ← one 3×4 matrix per line (cam0→world)
        ├── 01.txt …
        └── …

Only sequences that have a matching poses file are available for
multi-frame evaluation (sequences 00–10 in the standard split).

Token design
------------
Tokens are (seq_name, frame_idx) tuples, e.g. ("00", 42).
load_window() receives tokens from get_lidar_tokens_for_scene() and
passes them straight to load_lidar_points() / get_ego_pose() /
get_calibrated_sensor(), so the tuple carries everything needed.

Coordinate frame
----------------
KITTI calib.txt (odometry) provides:
    Tr: 3×4 float matrix — maps Velodyne sensor → left camera (cam0)
        camera convention: x=right, y=down, z=forward

We compose Tr with the same R_cam_to_ego used in kitti_loader.py:
    R_cam_to_ego = [[1, 0, 0], [0, 0, 1], [0,-1, 0]]
    → z-up ego frame identical to the nuScenes convention

Ego pose
--------
poses/XX.txt stores the cam0-to-world transform as 12 floats (3×4).
We convert that to the rotation and translation of the z-up ego frame
in the world:

    R_pose  =  P[:3,:3]  @  R_cam_to_ego.T
    t_pose  =  P[:3, 3]

The rotation is returned as a [w,x,y,z] quaternion (nuScenes convention)
so that multiframe._pose_R_t() works unchanged.
"""

import os
import numpy as np
from scipy.spatial.transform import Rotation


# Fixed rotation: KITTI camera frame → z-up ego frame (same as kitti_loader.py)
_R_CAM_TO_EGO = np.array([
    [1,  0,  0],
    [0,  0,  1],
    [0, -1,  0],
], dtype=np.float64)


class KITTIOdometryLoader:
    """
    Loader for KITTI Odometry dataset.

    Args:
        data_root: path to kitti_odometry/dataset/ directory that contains
                   sequences/ and poses/ subdirectories.
    """

    def __init__(self, data_root: str):
        self.data_root = data_root
        self._seq_dir   = os.path.join(data_root, "sequences")
        self._poses_dir = os.path.join(data_root, "poses")
        # calib.txt files land in sequences-calib/ when extracted separately
        # to avoid overwriting the velodyne sub-directories in sequences/
        _calib_candidate = os.path.join(data_root, "sequences-calib")
        self._calib_dir  = _calib_candidate if os.path.isdir(_calib_candidate) \
                           else self._seq_dir
        self._calib_cache: dict[str, dict] = {}
        self._poses_cache: dict[str, list[np.ndarray]] = {}

    # ── sequence / scene interface ────────────────────────────────────────────

    def list_sequences(self) -> list[str]:
        """Return sorted sequence names that have both scans and pose files."""
        seq_names = [
            d for d in os.listdir(self._seq_dir)
            if os.path.isdir(os.path.join(self._seq_dir, d))
        ]
        available = [
            s for s in seq_names
            if os.path.exists(os.path.join(self._poses_dir, f"{s}.txt"))
        ]
        return sorted(available)

    def get_scene_by_name(self, seq_name: str) -> dict:
        """Mirror NuScenesLoader.get_scene_by_name — returns a scene/sequence dict."""
        velo_dir = os.path.join(self._seq_dir, seq_name, "velodyne")
        if not os.path.isdir(velo_dir):
            raise FileNotFoundError(f"Sequence '{seq_name}' not found at {velo_dir}")
        n_frames = len([f for f in os.listdir(velo_dir) if f.endswith(".bin")])
        return {"name": seq_name, "n_frames": n_frames}

    def get_lidar_tokens_for_scene(self, scene: dict) -> list[tuple]:
        """
        Return ordered list of (seq_name, frame_idx) tokens for every frame
        in this sequence.  Compatible with load_window()'s token usage.
        """
        seq_name = scene["name"]
        n = scene["n_frames"]
        return [(seq_name, i) for i in range(n)]

    # ── per-frame interface (token = (seq_name, frame_idx)) ──────────────────

    def load_lidar_points(self, token: tuple) -> np.ndarray:
        """
        Load raw LiDAR point cloud in sensor frame.

        Returns:
            (N, 4) float32 — columns: x, y, z, intensity
            Points are in the Velodyne sensor frame.
            Pass to preprocessor.transform_to_ego() with get_calibrated_sensor().
        """
        seq_name, frame_idx = token
        path = os.path.join(
            self._seq_dir, seq_name, "velodyne", f"{frame_idx:06d}.bin"
        )
        return np.fromfile(path, dtype=np.float32).reshape(-1, 4)

    def get_calibrated_sensor(self, token: tuple) -> dict:
        """
        Return calibration dict compatible with preprocessor.transform_to_ego().

        Dict contains:
            'rotation':    (3, 3) ndarray — LiDAR sensor → z-up ego
            'translation': (3,)   ndarray — in metres

        Built from calib.txt's Tr matrix composed with R_cam_to_ego.
        Cached per sequence (single calib.txt shared across all frames).
        """
        seq_name, _ = token
        calib = self._load_calib(seq_name)
        Tr   = calib["Tr"]          # (3, 4)
        Tr_R = Tr[:, :3]
        Tr_t = Tr[:, 3]
        return {
            "rotation":    _R_CAM_TO_EGO @ Tr_R,
            "translation": _R_CAM_TO_EGO @ Tr_t,
        }

    def get_ego_pose(self, token: tuple) -> dict:
        """
        Return ego pose dict compatible with multiframe._pose_R_t().

        Dict contains:
            'rotation':    [w, x, y, z]  quaternion (nuScenes convention)
            'translation': [x, y, z]     in metres

        The pose represents the z-up ego frame's position+orientation in
        the world frame, derived from the cam0-to-world matrix in poses.txt.
        """
        seq_name, frame_idx = token
        poses = self._load_poses(seq_name)
        if frame_idx >= len(poses):
            raise IndexError(
                f"Frame {frame_idx} out of range for sequence '{seq_name}' "
                f"({len(poses)} poses available)"
            )
        P = poses[frame_idx]             # (4, 4) cam0 → world

        # Map to z-up ego frame: R_pose transforms ego→world in the same way
        # that nuScenes quaternion rotation does in _pose_R_t().
        R_pose = P[:3, :3] @ _R_CAM_TO_EGO.T
        t_pose = P[:3, 3]

        # Convert rotation matrix to [w, x, y, z] quaternion
        q_xyzw = Rotation.from_matrix(R_pose).as_quat()   # scipy → [x,y,z,w]
        q_wxyz = [float(q_xyzw[3]), float(q_xyzw[0]),
                  float(q_xyzw[1]), float(q_xyzw[2])]

        return {
            "rotation":    q_wxyz,
            "translation": t_pose.tolist(),
        }

    # ── private helpers ───────────────────────────────────────────────────────

    def _load_calib(self, seq_name: str) -> dict:
        """Parse calib.txt for a sequence.  Cached."""
        if seq_name in self._calib_cache:
            return self._calib_cache[seq_name]

        path = os.path.join(self._calib_dir, seq_name, "calib.txt")
        calib: dict[str, np.ndarray] = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, vals = line.split(":", 1)
                nums = np.array([float(v) for v in vals.split()], dtype=np.float64)
                # Odometry calib uses "Tr" (12 values → 3×4)
                # Some versions use "Tr_velo_to_cam"; accept both
                k = key.strip()
                if nums.size == 12:
                    calib[k] = nums.reshape(3, 4)
                elif nums.size == 9:
                    calib[k] = nums.reshape(3, 3)
                else:
                    calib[k] = nums

        # Normalise the velo-to-cam key to "Tr"
        for alias in ("Tr_velo_to_cam", "Tr_velo_cam"):
            if alias in calib and "Tr" not in calib:
                calib["Tr"] = calib[alias]

        if "Tr" not in calib:
            raise KeyError(
                f"calib.txt for sequence '{seq_name}' has no 'Tr' or "
                "'Tr_velo_to_cam' entry.  Keys found: " + str(list(calib.keys()))
            )

        self._calib_cache[seq_name] = calib
        return calib

    def _load_poses(self, seq_name: str) -> list[np.ndarray]:
        """
        Load poses/XX.txt.  Each line is 12 floats (row-major 3×4 cam0→world).
        Returns list of (4, 4) float64 homogeneous matrices.  Cached.
        """
        if seq_name in self._poses_cache:
            return self._poses_cache[seq_name]

        path = os.path.join(self._poses_dir, f"{seq_name}.txt")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Pose file not found: {path}\n"
                f"Only sequences with pose ground truth support multi-frame."
            )

        poses = []
        with open(path) as f:
            for line in f:
                vals = np.array(line.split(), dtype=np.float64)
                if vals.size != 12:
                    continue
                T = np.eye(4, dtype=np.float64)
                T[:3, :] = vals.reshape(3, 4)
                poses.append(T)

        self._poses_cache[seq_name] = poses
        return poses
