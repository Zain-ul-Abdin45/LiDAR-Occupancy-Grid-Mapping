# KITTI Dataset — Setup Guide

## What to Download

From the **KITTI 3D Object Detection** benchmark (not raw, not odometry):

```
https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d
```

Download these three files:
- `data_object_velodyne.zip` (~29 GB) — LiDAR point clouds
- `data_object_calib.zip` (~16 MB) — per-frame calibration matrices
- `data_object_label_2.zip` (~5 MB) — 3D bounding box labels

> For quick testing you can work with the **first 100 frames only**: no partial download
> option exists on the site, but after extraction you can keep only `000000–000099.*`
> in each subfolder.

---

## Directory Layout

Extract into `kitti/` inside `lidar_gap_mapping/`:

```
lidar_gap_mapping/
└── kitti/
    └── training/
        ├── velodyne/       # 000000.bin … 007480.bin  (float32, N×4 each)
        ├── calib/          # 000000.txt … 007480.txt
        └── label_2/        # 000000.txt … 007480.txt
```

The `testing/` split has no labels and is not used here.

---

## Verify the Download

```bash
cd lidar_gap_mapping/

# Point cloud: expect shape (N, 4) with N ~ 100 000 (64-beam LiDAR)
~/.pyenv/versions/3.11.9/bin/python3 -c "
import numpy as np
d = np.fromfile('kitti/training/velodyne/000000.bin', dtype=np.float32).reshape(-1, 4)
print('Points shape:', d.shape)
print('XYZ range  :', d[:, :3].min(axis=0), d[:, :3].max(axis=0))
"

# Calibration: expect Tr_velo_to_cam line
head -7 kitti/training/calib/000000.txt

# Labels: expect Car/Van/Pedestrian lines
head -5 kitti/training/label_2/000000.txt
```

---

## Format Differences vs nuScenes

| Property | nuScenes | KITTI |
|---|---|---|
| LiDAR | Velodyne HDL-32E (32 beams) | Velodyne HDL-64E (64 beams) |
| Point format | `float32 [N, 5]` (x y z intensity ring) | `float32 [N, 4]` (x y z intensity) |
| Calibration | JSON quaternion + translation | `calib.txt` matrix files |
| Ego pose | per-frame JSON | not available in object-detection split |
| Annotation | global-frame 3D boxes (quaternion yaw) | camera-frame boxes (rotation_y) |

Multi-frame accumulation is **not available** for KITTI object-detection frames
(no ego pose). Only single-frame evaluation is run.

---

## Coordinate Frame After Transform

`KITTILoader.get_calibrated_sensor()` returns a combined rotation + translation
that maps LiDAR sensor → z-up ego frame (identical interface to nuScenes):

```
LiDAR sensor → camera (Tr_velo_to_cam) → z-up ego (R_cam_to_ego)
```

where:
```
R_cam_to_ego = [[1, 0,  0],
                [0, 0,  1],
                [0, -1, 0]]
```

After this transform `z` is "up", so `height_filter(z ∈ [0.3, 3.0])` and all
downstream pipeline stages work without any changes.

---

## Running the Benchmark

```bash
cd lidar_gap_mapping/

# Quick sanity check (5 frames)
~/.pyenv/versions/3.11.9/bin/python3 run_kitti_benchmark.py --n-frames 5 --verbose

# Full 50-frame benchmark (default)
~/.pyenv/versions/3.11.9/bin/python3 run_kitti_benchmark.py

# Custom frame range
~/.pyenv/versions/3.11.9/bin/python3 run_kitti_benchmark.py --n-frames 20 --start-frame 0
```

Outputs written to `output/`:
- `kitti_benchmark.png` — NMSE box plots (T1 vs T2 β=0 vs T2 β=1)
- `kitti_iobb_overlay_XXXXXX.png` — GT box overlay for first frame
- `output/kitti_table.txt` — per-frame + aggregate table

Results appended to `results/results_log.md`.
