# LiDAR Occupancy Grid Mapping — TH OWL, Team 02

**Team:** Michael Ketler · Joani Gaxhi · Zain ul Abdin Khoso  
**Course:** Autonomous Vehicles, Semester 1  
**Interim presentation:** 2026-06-01 (completed)  
**Final presentation:** 2026-07-13

---

## What This Project Does

Converts raw LiDAR point clouds into probabilistic 2D occupancy grid maps — a core perception primitive for autonomous driving. We implement and compare two approaches:

- **Tier 1 (Classical Bayesian OGM):** Sequential log-odds updates via an inverse sensor model. Dense output; fast; well-understood.
- **Tier 2 (PC-SBL, Önen 2024):** Pattern-Coupled Sparse Bayesian Learning. EM algorithm with spatial coupling prior. Sparse, surface-accurate output with measured convergence guarantees.

Both tiers are evaluated on nuScenes-mini (10 scenes) using angular NMSE and IoBB metrics from the Önen 2024 paper. A multi-frame accumulation extension is implemented for both tiers.

---

## Summary of Results

All results at 80×80 grid, 0.5 m/cell, 40×40 m coverage, eval keyframe k=0.

### Single-scan (Phase 7 — 10-scene mean)

| Metric | T1 (Bayesian) | T2 β=0 (SBL) | T2 β=1 (PC-SBL) |
|---|---|---|---|
| NMSE | 0.1064 | 0.1503 | **0.1131** |
| IoBB | 0.102 | 0.024 | 0.023 |
| Precision | 0.073 | 0.059 | 0.059 |
| Mean iters | — | 124 | **58** |
| Converged | — | 4/10 | **10/10** |

β=1 coupling: 24.7% NMSE reduction vs β=0, 2.1× faster convergence, 100% convergence rate.

### Multi-frame (Phase 9 — w=2 window, λ=0.85, 10-scene mean)

| Metric | T1 single | T1 multi (w=2) | T2 single | T2 multi (w=2) |
|---|---|---|---|---|
| IoBB | 0.102 | 0.144 | 0.023 | **0.056** |
| Precision | — | — | — | 0.055 |

T2 multi-frame closes 42% of the gap to T1 single (+0.033 absolute IoBB). Precision=0.055 confirms genuine coverage gain.

---

## Literature

| Source | Key Idea |
|---|---|
| Elfes 1989 | Foundational OGM: probabilistic sensor model + Binary Bayes filter |
| Stachniss, Freiburg SLAM Lecture | Log-odds formulation, LiDAR ISM, full pseudocode |
| Önen et al. 2024 (IEEE Sensors J.) | PC-SBL EM — sparsity-promoting prior + spatial pattern coupling |
| TU Delft (Joseph group, 2025) | Accelerated PC-SBL (angular sector decomposition reference) |
| TU Delft — Dynamic OGM | Temporal PC-SBL via motion-augmented hyperprior (multi-frame reference) |
| Robbiano et al. arXiv:1911.07915 | Recursive Bayesian OGM with correlated cells |
| Mann et al. 2022 | Learned 4D occupancy prediction on nuScenes (related-work contrast) |

---

## Dataset — nuScenes-mini

Download from the [nuScenes website](https://www.nuscenes.org/download) and place at `v1.0-mini/`:

```
v1.0-mini/
├── v1.0-mini/                  # JSON metadata
│   ├── scene.json              # 10 driving scenes
│   ├── sample.json             # keyframe samples per scene
│   ├── sample_data.json        # sensor file pointers per sample
│   ├── ego_pose.json           # vehicle position + orientation per timestamp
│   ├── calibrated_sensor.json  # sensor-to-ego transforms (quaternion + translation)
│   ├── sample_annotation.json  # 3D bounding boxes (ground-truth for IoBB)
│   └── ...
├── samples/
│   └── LIDAR_TOP/              # .pcd.bin files — raw LiDAR scans
└── maps/                       # BEV map tiles (not used)
```

**LiDAR format:** `.pcd.bin` = flat binary `float32`, 5 values per point: `(x, y, z, intensity, ring_index)`. Points are in the **LiDAR sensor frame**; the pipeline transforms them to ego frame via `calibrated_sensor.json`.

**Key numbers:** 10 scenes · 404 keyframe samples · 3,935 LiDAR frames. ~4,800 points survive preprocessing per scan.

---

## Grid Parameters

| Parameter | Value |
|---|---|
| Area | 40 × 40 m (ego vehicle at center) |
| Resolution | 0.5 × 0.5 m per cell |
| Grid size | 80 × 80 = 6,400 cells |
| Representation | log-odds per cell, clamped to [−5, +5] |
| Ego center | grid cell (40, 40) |

---

## Preprocessing Pipeline

Applied to every raw LiDAR scan, in ego(j) frame before any transforms:

| Step | Operation | Module |
|---|---|---|
| 0 | Sensor → ego transform (calibrated_sensor quaternion) | `preprocessor.transform_to_ego` |
| 1 | Height filter: keep z ∈ [0.3, 3.0] m | `preprocessor.height_filter` |
| 2 | Range filter: keep points ≤ 20 m, ≥ 1.5 m radial | `preprocessor.range_filter` |
| 3 | 3D → 2D BEV projection (drop z) | `preprocessor.project_bev` |
| 4 | Discretize (x,y) → (row, col) | `preprocessor.discretize` |

The height filter must be applied in the scan's own ego frame before rotating to another frame — this is the critical constraint for correct multi-frame accumulation.

---

## Inverse Sensor Model

For each LiDAR point (row, col) after preprocessing:
- Cast Bresenham ray from ego (40,40) to terminal cell (row, col)
- All **intermediate cells**: `l_free = log(0.3/0.7) ≈ −0.847`
- **Terminal cell**: `l_occ = log(0.7/0.3) ≈ +0.847`
- Per-scan free cells deduplicated (numpy repeated-index write applies once per cell)
- Final log-odds clamped to [−5, +5]

---

## PC-SBL Model (Tier 2)

**Observation model:** `y = C·f + e`, `e ~ N(0, (1/γ)·I)`

**C matrix (2-equation):** For each of B occupied angular bins:
- Row 1 (occupied): y=1, C[b, hit_cell]=1 only (terminal-cell localisation)
- Row 2 (free): y=0, C[b+B, all_ray_cells]=free_weight (off-diagonal CᵀC coupling)

**E-step:** `A = γ·CᵀC + diag(ξ)`, solved with `spsolve`. Diagonal of Φ estimated by Hutchinson (K=16 Rademacher probes) — avoids O(N³) inversion.

**Pattern coupling (Fang 2015):** `ξ[n] = α[n] + β·Σ_{j∈L_n} α[j]`

**M-step:** `ω[n] = v̂[n] + β·Σ_{j∈L_n} v̂[j]`, `α[n] = (2a+1)/(2b+ω[n])`

**Optimal config (Phase 5):** β=1, γ=30, free_weight=0.5, hits_per_bin=3, tol=2e-3, max_iter=150, η_th=0.5

---

## Multi-frame Accumulation (Phase 9)

**Transform chain:** Bring scan j's points into eval frame k's ego:
```
p_ego_k = R(q_k)ᵀ · (R(q_j) · p_ego_j + t_j − t_k)
```

**Tier 1 (decay log-odds):** `L(cell) = clip(Σ_{j∈W} λ^{|k-j|} · l_update_j(cell), -5, +5)`

**Tier 2 (stacked C):** Stack C matrices from all window frames in ego(k), scaled by λ^{|k-j|}. Solve once with PC-SBL. More rows per cell = coverage the sparse method was starved for.

**Config:** k=0, w=2 (5 frames), λ=0.85

**Critical constraint:** Height filter applied in ego(j) BEFORE rotating to ego(k). Z-filter is only valid where z is vehicle-relative.

---

## Module Structure

```
lidar_gap_mapping/
├── main.py                     # Entry point: CLI flags, T1/T2 single-scan, eval, logging
├── build_report_figures.py     # Phase 8: generate 4 report figures
├── run_multiframe_benchmark.py # Phase 9: window sweep w∈{0,1,2,4}, all 10 scenes
├── run_accel_benchmark.py      # Phase 6: rectangular submap benchmark
├── run_sector_benchmark.py     # Phase 7: angular sector benchmark
├── check_iobb_overlay.py       # Phase 7: IoBB sanity visualizer
│
├── src/
│   ├── data_loader.py          # NuScenesLoader: JSON + .pcd.bin reading
│   ├── preprocessor.py         # transform_to_ego, height_filter, range_filter,
│   │                           #   project_bev, discretize, preprocess
│   ├── occupancy_grid.py       # 80×80 log-odds grid: update + sigmoid
│   ├── sensor_model.py         # Bresenham ISM: bresenham, update_grid_from_scan
│   ├── bayesian_ogm.py         # Tier 1: run_single_scan, run_bayesian_ogm
│   ├── pc_sbl.py               # Tier 2: PCSBL EM, build_C_matrix, _build_neighbour_lists
│   ├── pc_sbl_accel.py         # Phase 6: PCSBLAccel (rectangular tiles — negative result)
│   ├── pc_sbl_sector.py        # Phase 7: PCSBLSector (angular sectors — accuracy preserved)
│   ├── multiframe.py           # Phase 9: transform chain, multiframe_t1, multiframe_t2
│   ├── metrics.py              # compute_iobb, compute_angular_nmse, compute_precision
│   └── visualizer.py           # Greyscale BEV heatmap, GT box overlay
│
├── output/                     # Generated figures (gitignored)
├── results/
│   └── results_log.md          # Auto-appended experiment results (all 9 phases)
├── v1.0-mini/                  # nuScenes-mini dataset (gitignored)
├── README.md
└── implementation.md           # Phase-by-phase implementation record
```

---

## Quick Start

**Requirements:** Python 3.11+

```bash
pip install numpy scipy matplotlib nuscenes-devkit
```

**Tier 1 single scan with evaluation:**
```bash
~/.pyenv/versions/3.11.9/bin/python3 main.py \
    --scene scene-0061 --single-scan --eval --no-show
```

**Tier 2 PC-SBL β ablation:**
```bash
# β=0: decoupled SBL
~/.pyenv/versions/3.11.9/bin/python3 main.py \
    --scene scene-0061 --single-scan --tier2 --beta 0.0 --eval --no-show

# β=1: full PC-SBL (optimal)
~/.pyenv/versions/3.11.9/bin/python3 main.py \
    --scene scene-0061 --single-scan --tier2 --beta 1.0 --eval --no-show
```

**All 10 scenes sweep:**
```bash
~/.pyenv/versions/3.11.9/bin/python3 main.py --all-scenes --single-scan --eval --no-show
```

**Multi-frame benchmark (all 10 scenes, w∈{0,1,2,4}):**
```bash
~/.pyenv/versions/3.11.9/bin/python3 run_multiframe_benchmark.py
```

**Generate report figures:**
```bash
~/.pyenv/versions/3.11.9/bin/python3 build_report_figures.py
```

Results are appended to `results/results_log.md` after every run.

---

## Evaluation Metrics

**Angular NMSE (Önen 2024):** `NMSE = ||d − d̂||² / ||d||²`
- `d[i]` = closest LiDAR hit range in 1° angular bin i (ground-truth free-space distance)
- `d̂[i]` = distance to first occupied cell (P > 0.5) along ray i in the estimated map
- 360 bins; walker starts at r > 1.5 m to avoid self-return artifacts
- Lower is better. T1 mean 0.106, T2(β=1) mean 0.113.

**IoBB (Intersection over Bounding Box):** `IoBB = |{P>0.5} ∩ box_cells| / |box_cells|`
- Rotated 3D boxes from `sample_annotation.json` rasterized to BEV via `Path.contains_points`
- Higher is better. T1 mean 0.102, T2(β=1) single 0.023, T2 multi 0.056.

**Precision:** `Precision = |{P>0.5} ∩ box_cells| / |{P>0.5}|`
- Fraction of predicted-occupied cells that fall inside GT boxes.
- Guards against IoBB inflation via false positives.

---

## Project Phases

| Phase | Dates | Status | Deliverable |
|---|---|---|---|
| 1 — Literature + Concepts | May 13–18 | ✅ Done | Understood OGM + PC-SBL theory |
| 2 — Interim Prep | May 19–Jun 01 | ✅ Done | Interim presentation passed |
| 3 — Tier 1 Classical OGM | May 19–Jun 01 | ✅ Done | Working Bayesian pipeline on nuScenes |
| 4 — Tier 2 PC-SBL (Phase 2–3) | Jun 02–Jun 15 | ✅ Done | C matrix bug fixed; 10-scene eval |
| 5 — PC-SBL coupling + tuning (Phase 4–5) | Jun 15 | ✅ Done | β coupling active; optimal config |
| 6 — Acceleration (Phase 6–7) | Jun 15 | ✅ Done | Rectangular tiles (negative); sector (positive) |
| 7 — Report figures (Phase 8) | Jun 15 | ✅ Done | 4 figures for report |
| 8 — Multi-frame (Phase 9) | Jun 15 | ✅ Done | T2 IoBB 0.023→0.056 (+141%) |
| 9 — Report + Final | Jun 22–Jul 03 | Active | Written report + slides (deadline Jul 6) |

---

## Key Findings

1. **β coupling is universally beneficial:** NMSE reduction β=0→β=1 on all 10 scenes. 24.7% aggregate improvement. 2.1× faster convergence. 100% vs 40% convergence rate.

2. **Rectangular submap partitioning breaks PC-SBL:** NMSE→1.0 because the ego position falls outside distant tile boundaries, severing the Bresenham free-ray constraint. This is a genuine algorithmic finding.

3. **Angular sector partitioning preserves accuracy:** K=2,4 sectors give identical NMSE to K=1 because rays stay whole within each sector. Runtime improves only with a polar-grid implementation (N per sector < N total).

4. **Multi-frame raises T2 coverage:** w=2 window raises T2 mean IoBB 0.023→0.056, closing 42% of the gap to T1 single (0.102). Precision=0.055 confirms genuine coverage gain. Ground removal must occur in each frame's own ego frame before transform.

5. **T2 sparse surface vs T1 dense fill:** T2's low IoBB is structural — PC-SBL activates only surface-hit cells while GT boxes enclose full object volumes. Multi-frame partially compensates by accumulating surface hits from different angles.

---

## References

1. Elfes, A. (1989). Using occupancy grids for mobile robot perception and navigation. *IEEE Computer*, 22(6), 46–57.
2. Stachniss, C. (2013). *Grid Maps* [Lecture slides]. University of Freiburg SLAM Course.
3. Önen, Ç., Pandharipande, A., Joseph, G., & Myers, N. J. (2024). Occupancy Grid Mapping for Automotive Driving Exploiting Clustered Sparsity. *IEEE Sensors Journal*, 24(7), 9240–9250.
4. Joseph, G., Myers, N. J., et al. (2025). Accelerated Pattern-Coupled Sparse Bayesian Learning for Automotive Occupancy Mapping. *IEEE Sensors Journal*, 25, 41801–41810.
5. TU Delft group. *Dynamic Occupancy Grid Mapping for Automotive Vehicles Exploiting Temporal and Spatial Information* (temporal PC-SBL via motion-augmented hyperprior).
6. Robbiano, C., Chong, M., Azimi-Sadjadi, M., Scharf, L., & Pezeshki, A. (2019). Bayesian Learning of Occupancy Grids. arXiv:1911.07915.
7. Mann, M., Tomy, A., Paigwar, A., Renzaglia, A., & Laugier, C. (2022). Predicting Future Occupancy Grids in Dynamic Environments with Spatio-Temporal Learning. *IROS*.
