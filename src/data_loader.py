"""
Loads nuScenes-mini metadata and raw LiDAR scans.

nuScenes LiDAR .pcd.bin format: flat float32 array, 5 values per point:
  (x, y, z, intensity, ring_index)
Points are already in the ego-vehicle coordinate frame.
"""

import json
import os
import numpy as np


class NuScenesLoader:
    def __init__(self, data_root: str):
        """
        data_root: path to the v1.0-mini folder
                   (the one that contains both v1.0-mini/ JSON dir and samples/)
        """
        self.data_root = data_root
        meta_dir = os.path.join(data_root, "v1.0-mini")

        self.scenes = self._load(meta_dir, "scene.json")
        self.samples = self._load(meta_dir, "sample.json")
        self.sample_data = self._load(meta_dir, "sample_data.json")
        self.ego_poses = self._load(meta_dir, "ego_pose.json")
        self.calibrated_sensors = self._load(meta_dir, "calibrated_sensor.json")
        self.annotations = self._load(meta_dir, "sample_annotation.json")

        # Build token → object lookup maps for O(1) access
        self._scene_map = {s["token"]: s for s in self.scenes}
        self._sample_map = {s["token"]: s for s in self.samples}
        self._sd_map = {s["token"]: s for s in self.sample_data}
        self._ego_map = {e["token"]: e for e in self.ego_poses}
        self._cs_map = {c["token"]: c for c in self.calibrated_sensors}

        # Map sample_token → list of sample_data for that sample
        self._sd_by_sample: dict[str, list] = {}
        for sd in self.sample_data:
            self._sd_by_sample.setdefault(sd["sample_token"], []).append(sd)

    def _load(self, directory: str, filename: str) -> list:
        path = os.path.join(directory, filename)
        with open(path) as f:
            return json.load(f)

    def list_scenes(self) -> list[dict]:
        """Return all scenes with their name and description."""
        return [
            {"token": s["token"], "name": s["name"], "description": s["description"],
             "nbr_samples": s["nbr_samples"]}
            for s in self.scenes
        ]

    def get_scene_by_name(self, name: str) -> dict:
        """Get a scene dict by its name, e.g. 'scene-0061'."""
        for s in self.scenes:
            if s["name"] == name:
                return s
        raise ValueError(f"Scene '{name}' not found.")

    def get_lidar_tokens_for_scene(self, scene: dict) -> list[str]:
        """
        Walk the linked-list of samples for a scene and return the
        sample_data token for LIDAR_TOP at each keyframe, in order.
        """
        tokens = []
        token = scene["first_sample_token"]
        while token:
            sample = self._sample_map[token]
            for sd in self._sd_by_sample.get(token, []):
                if "LIDAR_TOP" in sd["filename"] and sd["is_key_frame"]:
                    tokens.append(sd["token"])
                    break
            token = sample.get("next", "")
        return tokens

    def load_lidar_points(self, sd_token: str) -> np.ndarray:
        """
        Load a LiDAR scan by its sample_data token.

        Returns:
            points: np.ndarray of shape (N, 5)
                    columns: x, y, z, intensity, ring_index
                    Already in ego-vehicle frame (x=forward, y=left, z=up).
        """
        sd = self._sd_map[sd_token]
        filepath = os.path.join(self.data_root, sd["filename"])
        points = np.fromfile(filepath, dtype=np.float32).reshape(-1, 5)
        return points

    def get_ego_pose(self, sd_token: str) -> dict:
        """Return the ego pose dict for a sample_data token."""
        sd = self._sd_map[sd_token]
        return self._ego_map[sd["ego_pose_token"]]

    def get_calibrated_sensor(self, sd_token: str) -> dict:
        """
        Return calibrated_sensor dict for a sample_data token (LIDAR_TOP).

        Contains:
          translation: [x, y, z]  — sensor position in ego frame (metres)
          rotation:    [w, x, y, z] — quaternion, sensor orientation in ego frame
        Use this to transform raw LiDAR points from sensor frame to ego frame.
        """
        sd = self._sd_map[sd_token]
        return self._cs_map[sd["calibrated_sensor_token"]]

    def get_sample_token(self, sd_token: str) -> str:
        """Return the sample_token (keyframe) for a given sample_data token."""
        return self._sd_map[sd_token]["sample_token"]

    def get_annotations_for_sample(self, sample_token: str) -> list[dict]:
        """Return all 3D bounding box annotations for a keyframe sample."""
        return [a for a in self.annotations if a["sample_token"] == sample_token]

class KittiLoader:
    def __init__(self, data_root: str):
        """
        Supports KITTI object dataset layout.

        Expected layout:
          kitti_data/
          ├── training/
          │   ├── velodyne/
          │   ├── calib/
          │   └── label_2/
          └── testing/
              └── velodyne/
        """
        self.data_root = data_root
        self.training_dir = os.path.join(data_root, "training")
        self.testing_dir = os.path.join(data_root, "testing")

        self._velodyne_dirs: dict[str, str] = {}
        self._image_dirs: dict[str, str] = {}

        if os.path.isdir(os.path.join(self.training_dir, "velodyne")):
            self._velodyne_dirs["training"] = os.path.join(self.training_dir, "velodyne")
        if os.path.isdir(os.path.join(self.training_dir, "image_2")):
            self._image_dirs["training"] = os.path.join(self.training_dir, "image_2")
        if os.path.isdir(os.path.join(self.testing_dir, "image_2")):
            self._image_dirs["testing"] = os.path.join(self.testing_dir, "image_2")
        if os.path.isdir(os.path.join(self.testing_dir, "velodyne")):
            self._velodyne_dirs["testing"] = os.path.join(self.testing_dir, "velodyne")

        for scene_name, vel_path in self._velodyne_dirs.items():
            if not os.path.isdir(vel_path):
                continue
            for filename in os.listdir(vel_path):
                if filename.endswith('.bin'):
                    frame_id = os.path.splitext(filename)[0]

        self._label_dir = os.path.join(self.training_dir, "label_2")
        self._calib_cache: dict[str, dict] = {}


    def _load_calibration(self, frame_id: str) -> dict | None:
        if frame_id in self._calib_cache:
            return self._calib_cache[frame_id]

        calib_path = os.path.join(self.training_dir, "calib", f"{frame_id}.txt")
        if not os.path.isfile(calib_path):
            self._calib_cache[frame_id] = None
            return None

        calib = {}
        with open(calib_path, 'r') as f:
            for line in f:
                if ':' not in line:
                    continue
                key, values = line.split(':', 1)
                calib[key.strip()] = np.fromstring(values, sep=' ')

        if 'Tr_velo_to_cam' in calib:
            calib['Tr_velo_to_cam'] = calib['Tr_velo_to_cam'].reshape(3, 4)
        if 'R0_rect' in calib:
            calib['R0_rect'] = calib['R0_rect'].reshape(3, 3)

        self._calib_cache[frame_id] = calib
        return calib

    def _camera_to_velo_transform(self, calib: dict) -> tuple[np.ndarray, np.ndarray]:
        """Return rotation and translation from rectified camera to velodyne coordinates."""
        R0_rect = calib['R0_rect']
        Tr_velo_to_cam = calib['Tr_velo_to_cam']

        R_velo_to_cam = Tr_velo_to_cam[:, :3]
        t_velo_to_cam = Tr_velo_to_cam[:, 3]

        R_cam_to_velo = (R0_rect @ R_velo_to_cam).T
        t_cam_to_velo = -R_cam_to_velo @ (R0_rect @ t_velo_to_cam)
        return R_cam_to_velo, t_cam_to_velo

    def list_scenes(self) -> list[dict]:
        """Return available KITTI scenes or sets."""
        scenes = []
        scene_items = sorted(self._velodyne_dirs.items())

        for name, path in scene_items:
            nbr_samples = len([f for f in os.listdir(path) if f.endswith('.bin')])
            scenes.append({
                "name": name,
                "description": f"KITTI {name}",
                "nbr_samples": nbr_samples,
            })
        return scenes

    def get_scene_by_name(self, name: str) -> dict:
        for s in self.list_scenes():
            if s["name"] == name:
                return s

        raise ValueError(f"KITTI Scene '{name}' not found.")

    def get_lidar_tokens_for_scene(self, scene: dict) -> list[str]:
        if scene["name"] in self._velodyne_dirs:
            seq_path = self._velodyne_dirs[scene["name"]]
            files = sorted([f for f in os.listdir(seq_path) if f.endswith('.bin')])
            return [f"{scene['name']}|{f.split('.')[0]}" for f in files]

        raise ValueError(f"KITTI Scene '{scene['name']}' has no lidar files.")

    def load_lidar_points(self, token: str) -> np.ndarray:
        scene_name, frame_id = token.split('|')
        if scene_name not in self._velodyne_dirs:
            raise ValueError(f"KITTI scene '{scene_name}' not found for token '{token}'.")

        seq_path = self._velodyne_dirs[scene_name]
        filepath = os.path.join(seq_path, f"{frame_id}.bin")
        points = np.fromfile(filepath, dtype=np.float32).reshape(-1, 4)
        return points

    def get_calibrated_sensor(self, token: str) -> dict:
        """Return a simple transform from KITTI Velodyne sensor frame to ego frame."""
        return {
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "translation": [0.0, 0.0, 1.73],
        }

    def get_sample_token(self, sd_token: str) -> str:
        return sd_token

    def get_camera_image_path(self, sd_token: str) -> str | None:
        scene_name, frame_id = sd_token.split('|')
        if scene_name not in self._image_dirs:
            return None

        for ext in ['.png', '.jpg', '.jpeg']:
            path = os.path.join(self._image_dirs[scene_name], f"{frame_id}{ext}")
            if os.path.isfile(path):
                return path
        return None

    def get_annotations_for_sample(self, sample_token: str) -> list[dict]:
        scene_name, frame_id = sample_token.split('|')
        if scene_name != 'training':
            return []

        label_path = os.path.join(self._label_dir, f"{frame_id}.txt")
        if not os.path.isfile(label_path):
            return []

        calib = self._load_calibration(frame_id)
        if calib is None:
            return []

        R_cam_to_velo, t_cam_to_velo = self._camera_to_velo_transform(calib)
        sensor_offset = np.array([0.0, 0.0, 1.73], dtype=np.float64)

        annotations = []
        with open(label_path, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if not parts or parts[0] == 'DontCare':
                    continue
                try:
                    h = float(parts[8])
                    w = float(parts[9])
                    l = float(parts[10])
                    x_cam = float(parts[11])
                    y_cam = float(parts[12])
                    z_cam = float(parts[13])
                    rot_y = float(parts[14])
                except (ValueError, IndexError):
                    continue

                loc_cam = np.array([x_cam, y_cam, z_cam], dtype=np.float64)
                loc_velo = R_cam_to_velo @ loc_cam + t_cam_to_velo
                loc_ego = loc_velo + sensor_offset

                dir_cam = np.array([np.sin(rot_y), 0.0, np.cos(rot_y)], dtype=np.float64)
                dir_velo = R_cam_to_velo @ dir_cam
                yaw_velo = np.arctan2(dir_velo[1], dir_velo[0])

                qw = float(np.cos(yaw_velo / 2.0))
                qz = float(np.sin(yaw_velo / 2.0))

                annotations.append({
                    "translation": loc_ego.tolist(),
                    "size": [w, l, h],
                    "rotation": [qw, 0.0, 0.0, qz],
                })

        return annotations

    def get_ego_pose(self, token: str) -> dict:
        """Return a default ego pose for KITTI object scans."""
        return {
            "translation": [0.0, 0.0, 0.0],
            "rotation": [1.0, 0.0, 0.0, 0.0],
        }
