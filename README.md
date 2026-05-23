# LiDAR Occupancy Grid Mapping — TH OWL, Team 02

**Team:** Michael Ketler · Joani Gaxhi · Zain ul Abdin Khoso  
**Course:** Autonomous Vehicles, Semester 1  
**Interim presentation:** 2026-06-01  
**Final presentation:** 2026-07-03

---

## What This Project Does

We convert raw LiDAR point clouds from the nuScenes-mini dataset into a 2D occupancy grid map — a probabilistic representation of which cells in a 40×40 m environment are occupied by obstacles and which are free. This is a core perception component for autonomous driving.

**Two-tier approach:**
- **Tier 1 (Classical Bayesian):** Sequential log-odds updates using an inverse sensor model. Output: probability per cell.
- **Tier 2 (PC-SBL, Önen 2024):** EM algorithm with sparsity-promoting Gamma prior and pattern-coupled spatial correlation. Output: binary map via thresholding.

---

## Literature

| Source | Key Idea |
|---|---|
| Elfes 1989 | Foundational OGM: probabilistic sensor model + Binary Bayes filter |
| Stachniss SLAM Lecture (Freiburg) | Log-odds formulation, LiDAR inverse sensor model, full pseudocode |
| Önen et al. 2024 (IEEE) | PC-SBL EM algorithm — fixes sparsity + spatial correlation for automotive LiDAR |

---

## Dataset — nuScenes-mini

Located at `v1.0-mini/`. This is the official [nuScenes](https://www.nuscenes.org/) mini split.

```
v1.0-mini/
├── v1.0-mini/          # JSON metadata
│   ├── scene.json          # 10 driving scenes
│   ├── sample.json         # keyframe samples per scene
│   ├── sample_data.json    # sensor file pointers per sample
│   ├── ego_pose.json       # vehicle position + orientation per timestamp
│   ├── calibrated_sensor.json  # sensor-to-ego transforms
│   ├── sample_annotation.json  # 3D bounding boxes (ground truth for IoBB)
│   └── ...
├── samples/
│   └── LIDAR_TOP/      # .pcd.bin files — raw LiDAR scans
└── maps/               # BEV map tiles (PNG)
```

**LiDAR format:** Each `.pcd.bin` file is a flat binary array of `float32` values, 5 per point: `(x, y, z, intensity, ring_index)`. Points are in the **ego-vehicle coordinate frame** (x = forward, y = left, z = up). One scan contains ~34,000 points.

**Key numbers:** 10 scenes · 404 keyframe samples · 3,935 LiDAR frames.

---

## Grid Parameters

| Parameter | Value |
|---|---|
| Area | 40 × 40 m (ego vehicle at center) |
| Resolution | 0.5 × 0.5 m per cell |
| Grid size | 80 × 80 = 6,400 cells |
| Representation | log-odds per cell, clamped to [−5, +5] |
| Ego center | grid cell (40, 40) — row 40, col 40 |

---

## Preprocessing Pipeline (4 Steps)

Applied to every raw LiDAR scan before grid update:

1. **Height filter** — keep points with z ∈ [−2 m, +3 m]  
   Removes ground clutter below the car and aerial noise above it.

2. **Range filter** — keep points within ±20 m in x and y  
   Matches the 40×40 m grid extent. Points outside are irrelevant.

3. **3D → 2D projection** — drop the z coordinate  
   Produces a Bird's Eye View (BEV) point set of (x, y) pairs.

4. **Discretize** — map (x, y) → (row, col)  
   `col = floor((x + 20) / 0.5)`, `row = floor((20 - y) / 0.5)`  
   (x forward = larger col; y left = smaller row)

---

## Inverse Sensor Model (Bresenham Ray Casting)

For each LiDAR point after preprocessing:
- Cast a ray from the ego-vehicle center `(40, 40)` to the terminal grid cell `(row, col)`.
- All cells **along the ray** (excluding terminal) get a **free** log-odds update: `l_free = log(0.3/0.7) ≈ −0.847`
- The **terminal cell** gets an **occupied** log-odds update: `l_occ = log(0.7/0.3) ≈ +0.847`
- Clamp all log-odds values to `[−5, +5]` after each update.

---

## Module Structure

```
src/
├── __init__.py
├── data_loader.py       # Reads nuScenes JSON + loads .pcd.bin files
├── preprocessor.py      # Height filter, range filter, 3D→2D, discretize
├── occupancy_grid.py    # 80×80 log-odds grid data structure
├── sensor_model.py      # Bresenham ray casting + log-odds update values
├── bayesian_ogm.py      # Tier 1: Classical Bayesian update loop
└── visualizer.py        # Matplotlib BEV grid visualization

main.py                  # Entry point: run one scene through Tier 1
```

---

## Quick Start

**Requirements:** Python 3.11+, numpy, scipy, matplotlib

```bash
pip install numpy scipy matplotlib
```

**Run Tier 1 on one scene:**

```bash
~/.pyenv/versions/3.11.9/bin/python3 main.py \
    --data-root v1.0-mini \
    --scene scene-0061 \
    --out output/
```

This will:
1. Load all LiDAR scans for the scene
2. Run preprocessing on each scan
3. Accumulate Bayesian log-odds updates across all scans
4. Save a PNG visualization of the final occupancy grid

---

## Project Timeline

| Phase | Dates | Status | Goal |
|---|---|---|---|
| 1 — Literature + Concepts | May 13–18 | Done | Read all 3 sources, understood OGM theory |
| 2 — Interim Prep | May 19–25 | Active | Slides uploaded, feedback written |
| **3 — Classical Bayesian** | **May 19–Jun 01** | **Active** | **Working Tier 1 pipeline on nuScenes** |
| 4 — PC-SBL EM | Jun 02–15 | Planned | Tier 2 EM algorithm from Önen 2024 |
| 5 — Evaluation | Jun 16–25 | Planned | NMSE + IoBB metrics on all 10 scenes |
| 6 — Report + Final | Jun 26–Jul 03 | Planned | Written report + final presentation |

---

## What Needs to Happen Before June 01

The Bayesian baseline pipeline must be running. Task breakdown:

| Day | Task | Module |
|---|---|---|
| May 23–24 | Data loader: list scenes, load .pcd.bin per sample | `data_loader.py` |
| May 24–25 | Preprocessing: filter + project + discretize | `preprocessor.py` |
| May 25–26 | Occupancy grid + Bresenham ray tracing | `occupancy_grid.py`, `sensor_model.py` |
| May 26–28 | Bayesian update loop over full scene | `bayesian_ogm.py` |
| May 28–29 | Visualizer + sanity check (cone at 5m → grid row 40, col 50) | `visualizer.py` |
| May 29–Jun 01 | Debug + clean output on multiple scenes | all |

---

## Evaluation Metrics

**NMSE (Normalized Mean Squared Error)**  
`NMSE = mean((p_estimated − p_gt)²) / mean(p_gt²)`  
Lower is better. Önen 2024 reports 0.1–0.3 on nuScenes.

**IoBB (Intersection over Bounding Box)**  
For each annotated object in `sample_annotation.json`:  
1. Project its 3D BEV bounding box onto the 80×80 grid.  
2. Mark cells with `p > 0.5` as occupied.  
3. `IoBB = |occupied_cells ∩ bbox_cells| / |bbox_cells|`  
Higher is better. Threshold: cell probability > 0.5 = occupied.

---

## Key Challenges and Mitigations

| Challenge | Mitigation |
|---|---|
| Coordinate frame alignment | nuScenes .pcd.bin is already in ego-vehicle frame — no manual rotation needed. Unit test: point at x=5m → col 50. |
| PC-SBL convergence | Start from paper defaults β=1, a=b=1. Cap at 100 EM iterations. Monitor `‖μ_t − μ_{t-1}‖₂`. Fall back to Tier 1 if diverges. |
| C matrix size (~15k×6400) | Use `scipy.sparse.csr_matrix`. Memory drops from ~350 MB dense to ~5 MB sparse. |
| Focus area without digital map | Skip focus masking. Apply range filter ±20m as sole spatial constraint. Document as known scope limitation. |

---

## References

1. Elfes, A. (1989). "Using occupancy grids for mobile robot perception and navigation." *IEEE Computer*, 22(6), 46–57.
2. Stachniss, C. (2013). *Grid Maps* [Lecture slides]. University of Freiburg SLAM Course.
3. Önen, M. et al. (2024). "Occupancy Grid Mapping for Automotive Driving Exploiting Clustered Sparsity." *IEEE*.
