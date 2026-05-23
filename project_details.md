# Project Details — LiDAR Gap Mapping (Autonomous Vehicles, Semester 1)

## 1. Team & Context

- **Course:** Autonomous Vehicles (AUV), Semester 1, TH OWL
- **Team:** Team 02
- **Student:** Zain ul Abdin Khoso
- **Python interpreter:** `~/.pyenv/versions/3.11.9/bin/python3` (system python3 has a numpy binary mismatch — always use pyenv)

### Key dates (as of 2026-05-23)

| Milestone | Date |
|-----------|------|
| Interim presentation submission | 2026-05-24 |
| Interim presentation (live) | 2026-06-01 |
| Tier 2 algorithm target | 2026-06-02 – 2026-06-15 |

---

## 2. Project Goal

Build a **2-tier LiDAR occupancy grid mapping system** on the nuScenes v1.0-mini dataset:

- **Tier 1 (done):** Classical Bayesian Occupancy Grid Mapping using a binary Bayes filter and Bresenham ray-casting inverse sensor model.
- **Tier 2 (planned):** PC-SBL EM algorithm (Önen 2024) — sparse Bayesian learning for improved gap-filling and uncertainty estimation.

The final deliverable compares both tiers on NMSE and IoBB metrics.

---

## 3. Dataset: nuScenes v1.0-mini

### Structure

```
v1.0-mini/
├── v1.0-mini/          ← JSON metadata
│   ├── scene.json
│   ├── sample.json
│   ├── sample_data.json
│   ├── ego_pose.json
│   ├── calibrated_sensor.json
│   └── sample_annotation.json
└── samples/
    └── LIDAR_TOP/      ← .pcd.bin files, one per keyframe
```

### Key facts

- 10 scenes, ~40 keyframe samples each (~39 scans/scene)
- LiDAR sensor: Velodyne (rotating multi-beam) — LIDAR_TOP
- File format: flat `float32` binary, 5 values per point: `(x, y, z, intensity, ring_index)`
- Points are **already in the ego-vehicle frame** at capture time: `x=forward, y=left, z=up`
- Typical point count per scan: ~27,000 after height+range filtering
- Ground truth bounding boxes: `sample_annotation.json` (3D boxes, can be projected to BEV for IoBB)

### Accessing data (Python)

```python
loader = NuScenesLoader("v1.0-mini")
scenes = loader.list_scenes()                     # list of {token, name, description, nbr_samples}
scene = loader.get_scene_by_name("scene-0061")
lidar_tokens = loader.get_lidar_tokens_for_scene(scene)   # ordered list of sd_tokens
points = loader.load_lidar_points(sd_token)       # (N, 5) float32
ego_pose = loader.get_ego_pose(sd_token)          # dict with "translation": [x, y, z]
annotations = loader.get_annotations_for_sample(sample_token)  # list of 3D box dicts
```

---

## 4. Algorithm: Tier 1 — Classical Bayesian OGM

### Grid parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Grid size | 80 × 80 cells | configurable via `--grid-size` |
| Cell size | 0.5 m/cell | configurable via `--cell-size` |
| Total coverage | 40 × 40 m (±20 m) | `half_range = grid_size * cell_size / 2` |
| Ego position | row=40, col=40 | grid center |
| Log-odds clamp | [−5.0, +5.0] | P ≈ 0.007 to 0.993 |

### Coordinate convention

- `x = forward` → increasing `col`: `col = floor((x + half_range) / cell_size)`
- `y = left` → decreasing `row`: `row = floor((half_range - y) / cell_size)`

### Log-odds update values (Stachniss SLAM lecture)

```
L_OCC  = log(0.7 / 0.3) ≈ +0.847   (occupied hit)
L_FREE = log(0.3 / 0.7) ≈ −0.847   (ray traversal = free)
P(occupied) = sigmoid(L) = 1 / (1 + exp(-L))
Initial L = 0  →  P = 0.5 (maximum uncertainty)
```

### Preprocessing pipeline (per scan)

1. **Height filter:** keep points with `z ∈ [−2.0, +3.0]` m (removes ground and aerial noise)
2. **Range filter:** keep `|x| ≤ half_range AND |y| ≤ half_range` (square window)
3. **BEV projection:** drop `z` coordinate → `(N, 2)` array of `(x, y)`
4. **Discretize:** map `(x, y)` → `(row, col)` integer indices, clipped to `[0, grid_size−1]`

### Inverse Sensor Model (Bresenham ray-casting)

For each LiDAR hit cell `(r1, c1)` from ego origin `(r0, c0)`:
- Cast Bresenham line from ego → hit
- All cells along the ray **except the terminal**: `+= L_FREE`
- Terminal cell (the hit): `+= L_OCC`

This is standard integer Bresenham — no floating point in the inner loop.

### Multi-scan accumulation modes

#### Problem: ego-frame smearing
Stacking multiple scans in the ego frame while the vehicle moves causes contradictory evidence — cells that were free from one position receive occupied votes when the vehicle moves and a new obstacle appears at that cell's coordinates.

#### Three modes implemented

| Mode | Flag | Use case |
|------|------|----------|
| Single scan (ego-centric) | `--single-scan` | Cleanest demo; one keyframe; no smearing |
| World-frame accumulation | (default) | Correct multi-scan; centers grid on trajectory centroid |
| Ego-frame accumulation | `--ego-frame` | Shows smearing artifact; pedagogical comparison |

#### World-frame centering fix
Previously the grid was centered on the first scan's ego position. As the vehicle drove away (e.g., turning at an intersection), scans 13–39 contributed zero points because they fell outside the 40 m window.

**Fix:** compute the centroid of all ego positions first, use that as the world-frame origin. All 39 scans now contribute:

```python
all_xy = np.array([loader.get_ego_pose(t)["translation"][:2] for t in lidar_tokens])
origin_xy = all_xy.mean(axis=0)  # centroid of full trajectory
```

For each scan: `xy_world = xy_ego + (ego_xy - origin_xy)` then re-filter to grid bounds.

---

## 5. Codebase Structure

```
lidar_gap_mapping/
├── main.py                 # CLI entry point
├── requirements.txt        # numpy, scipy, matplotlib
├── src/
│   ├── __init__.py
│   ├── data_loader.py      # NuScenesLoader class
│   ├── preprocessor.py     # height_filter, range_filter, project_bev, discretize, preprocess
│   ├── occupancy_grid.py   # OccupancyGrid class (log-odds, update, get_probability)
│   ├── sensor_model.py     # bresenham(), update_grid_from_scan()
│   ├── bayesian_ogm.py     # run_single_scan(), run_bayesian_ogm()
│   └── visualizer.py       # plot_grid(), plot_probability_histogram()
├── output/                 # generated PNG files
│   ├── scene-0061_scan00.png       # single-scan demo (use for presentation)
│   └── scene-0061_world_frame.png  # accumulated world-frame map
└── v1.0-mini/              # nuScenes dataset (not in version control)
```

### Module responsibilities

**`src/data_loader.py` — `NuScenesLoader`**
- Loads all JSON metadata at init, builds O(1) token lookup dicts
- Key methods: `list_scenes()`, `get_scene_by_name()`, `get_lidar_tokens_for_scene()`, `load_lidar_points()`, `get_ego_pose()`, `get_annotations_for_sample()`

**`src/preprocessor.py`**
- Constants: `Z_MIN=-2.0`, `Z_MAX=3.0`, `HALF_RANGE=20.0`, `GRID_SIZE=80`, `CELL_SIZE=0.5`
- Functions: `height_filter`, `range_filter`, `project_bev`, `discretize`, `preprocess` (full pipeline)
- `discretize(xy, half_range, cell_size, grid_size)` — all parameters explicit for grid-agnostic use

**`src/occupancy_grid.py` — `OccupancyGrid`**
- Stores `log_odds: np.ndarray` shape `(grid_size, grid_size)` as `float32`
- `update(rows, cols, delta)` — batch numpy update with clamping
- `get_probability()` — returns `sigmoid(log_odds)`
- `get_binary_map(threshold=0.5)` — boolean occupancy mask
- Properties: `ego_row`, `ego_col` — both return `grid_size // 2`
- Stores `grid_size` and `cell_size` as attributes (used by bayesian_ogm.py)

**`src/sensor_model.py`**
- `bresenham(r0, c0, r1, c1)` — generator, standard integer algorithm
- `update_grid_from_scan(grid, cells, ego_row=None, ego_col=None)` — ego override for world-frame mode; batches free updates for efficiency

**`src/bayesian_ogm.py`**
- `run_single_scan(loader, scene_name, grid, scan_index=0, verbose=True)`
- `run_bayesian_ogm(loader, scene_name, grid, world_frame=True, verbose=True)` — derives `half_range` from `grid.grid_size * grid.cell_size / 2` so it respects any `--grid-size` override

**`src/visualizer.py`**
- `plot_grid(grid, title, save_path, show, smooth_sigma=0.0, show_ego=True)`
  - `smooth_sigma > 0`: applies `scipy.ndimage.gaussian_filter` before display (σ=0.8 recommended to soften LiDAR beam-ring gaps without losing obstacle edges)
  - `show_ego=False`: hides the ego marker (used in world-frame mode where grid center ≠ vehicle)
  - `extent` is computed from `grid.grid_size * grid.cell_size / 2` so it scales with grid params
- `plot_probability_histogram(grid)` — diagnostic histogram of cell P(occ) distribution

**`main.py`**
- All flags: `--data-root`, `--scene`, `--all-scenes`, `--out`, `--no-show`, `--grid-size`, `--cell-size`, `--single-scan`, `--ego-frame`, `--scan-index`, `--smooth`

---

## 6. Running the Code

```bash
# Always use pyenv python — system python3 has numpy binary mismatch
PYTHON=~/.pyenv/versions/3.11.9/bin/python3

# Install dependencies (once)
$PYTHON -m pip install numpy scipy matplotlib

# Primary presentation demo: single scan, smoothed
$PYTHON main.py --data-root v1.0-mini --scene scene-0061 --single-scan --smooth 0.8 --out output --no-show

# Show smearing (pedagogical comparison)
$PYTHON main.py --data-root v1.0-mini --scene scene-0061 --ego-frame --smooth 0.5 --out output --no-show

# Full accumulation (world-frame, all 39 scans)
$PYTHON main.py --data-root v1.0-mini --scene scene-0061 --smooth 0.8 --out output --no-show

# All 10 scenes at once
$PYTHON main.py --data-root v1.0-mini --all-scenes --single-scan --smooth 0.8 --out output --no-show

# Pick a specific scan frame
$PYTHON main.py --data-root v1.0-mini --scene scene-0061 --single-scan --scan-index 5 --smooth 0.8 --out output --no-show
```

---

## 7. Known Artifacts and Their Causes

| Artifact | Cause | Mitigation |
|----------|-------|------------|
| Concentric ring bands in single-scan | Velodyne discrete elevation angles create circles of points in BEV; cells between rings stay at P=0.5 | `--smooth 0.8` Gaussian blur; or use world-frame accumulation |
| World-frame dropout (scans contributing 0 pts) | Grid centered on first ego position; vehicle drives out of 40 m window | Fixed: center on trajectory centroid |
| Ego-frame smearing | All scans written at ego-centric coordinates while vehicle moves | Use `--single-scan` or `--world-frame` (default) |
| Noisy world-frame result | Dense urban scene + 90° turn = complex overlapping free/occupied evidence | Expected for this scene; demonstrate single-scan for presentation |
| High occupied % in world-frame (~72%) | Many cells receive at least one occupied vote across 39 diverse scan positions | Inherent limitation of simple Bayesian OGM; motivates Tier 2 |

---

## 8. Evaluation Metrics (Planned for Tier 2 comparison)

### NMSE (Normalized Mean Squared Error)
- Lower is better; Önen 2024 reports 0.1–0.3 for PC-SBL
- Requires a ground-truth occupancy map (can be synthesised from a dense reference scan or the full non-mini nuScenes set)

### IoBB (Intersection over Bounding Box)
- Measures how well detected occupied regions match annotated object bounding boxes
- Ground truth source: `sample_annotation.json` — each entry has `translation`, `size`, `rotation` fields defining a 3D box
- Project to BEV: use `translation[:2]` (x, y) and `size[:2]` (length, width); rotation gives yaw
- `loader.get_annotations_for_sample(sample_token)` returns all annotations for a keyframe

---

## 9. Tier 2 Plan (PC-SBL EM, not yet started)

**Reference:** Önen, M. (2024). *Point-Cloud Sparse Bayesian Learning for Occupancy Grid Mapping*. IEEE.

**Target dates:** 2026-06-02 to 2026-06-15

**Concept:**
- Model the occupancy grid as a sparse signal recovered from noisy LiDAR measurements via Sparse Bayesian Learning (SBL) with Expectation-Maximization (EM)
- Expected to fill in ring-gap artifacts and handle sparse measurements better than the classical Bayesian filter
- Inputs: same preprocessed `(row, col)` cells from `src/preprocessor.py`
- Output: refined occupancy probability map — should compare against Tier 1 on NMSE and IoBB

**Integration point:** the Tier 2 module should accept the same `OccupancyGrid` object or a compatible numpy array as input/output, so the existing visualizer and evaluation code can be reused.

---

## 10. Current Output Status (2026-05-23)

| Output file | Mode | Status |
|-------------|------|--------|
| `output/scene-0061_scan00.png` | Single scan, σ=0.8 smooth | **Use for presentation** — clean, road structure visible, ring gaps smoothed |
| `output/scene-0061_world_frame.png` | World-frame, all 39 scans, σ=0.8 | Correct algorithm, complex urban result — not ideal for visual demo |
| `output/scene-0061_bayesian.png` | Old ego-frame (pre-fix) | Archived — shows smearing artifact |

---

## 11. What Works / What Is Pending

### Done
- Full nuScenes-mini data loading pipeline (no external nuScenes SDK dependency — pure JSON + numpy)
- Preprocessing pipeline: height filter → range filter → BEV → discretize
- OccupancyGrid with log-odds, update, sigmoid, binary map
- Bresenham ISM with batched free-space updates
- Three accumulation modes: single-scan, world-frame (trajectory-centered), ego-frame
- Visualizer with Gaussian smoothing and configurable ego marker
- All 39 scans contributing in world-frame mode (centroid-centering fix)
- Axis labels correct (x=forward horizontal, y=left vertical)

### Pending
- Tier 2: PC-SBL EM implementation
- NMSE computation (needs ground-truth occupancy reference)
- IoBB computation (needs BEV projection of `sample_annotation.json` boxes)
- Quantitative comparison table: Tier 1 vs Tier 2 on NMSE and IoBB
- Final report
