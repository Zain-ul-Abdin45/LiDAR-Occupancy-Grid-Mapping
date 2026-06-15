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
