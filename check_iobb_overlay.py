"""
IoBB sanity overlay: visualize T1 and T2 occupied cells vs GT bounding boxes.

Checks for the half-cell offset bug flagged in improvement_v5 §6:
  IoBB = 0.000/0.001 on 0796/0061 is suspicious.

Generates output/iobb_overlay_{scene}.png with:
  - Grey: occupancy grid (prob_map)
  - Red boxes: GT bounding box outlines (rasterized boundary)
  - Cyan: T1 occupied cells (P > 0.5)
  - Yellow: T2 occupied cells (P > 0.5)

Run:
    ~/.pyenv/versions/3.11.9/bin/python3 check_iobb_overlay.py
"""
import sys, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.path import Path
import matplotlib.patheffects as pe

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.data_loader import NuScenesLoader
from src.preprocessor import transform_to_ego, height_filter, range_filter, project_bev
from src.pc_sbl import PCSBL
from src.bayesian_ogm import run_single_scan
from src.occupancy_grid import OccupancyGrid
from src.metrics import _box_corners_bev

DATA_ROOT = "v1.0-mini"
SCENES    = ["scene-0061", "scene-0796"]
GRID_SIZE = 80
CELL_SIZE = 0.5
THRESHOLD = 0.5

PCSBL_KWARGS = dict(
    beta=1.0, max_iter=150, tol=2e-3, gamma_fixed=30.0,
    alpha_damping=0.3, hits_per_bin=3,
    grid_size_sbl=GRID_SIZE, cell_size_sbl=CELL_SIZE,
    free_weight=0.5, verbose=False,
)

loader = NuScenesLoader(DATA_ROOT)

for scene_name in SCENES:
    print(f"\nScene: {scene_name}")
    scene = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)
    sd_token = lidar_tokens[0]
    sample_token = loader.get_sample_token(sd_token)

    # Load points
    pts_raw = loader.load_lidar_points(sd_token)
    cs_sens = loader.get_calibrated_sensor(sd_token)
    pts_ego = transform_to_ego(pts_raw, cs_sens)
    pts_f   = height_filter(pts_ego)
    pts_f   = range_filter(pts_f, GRID_SIZE * CELL_SIZE / 2.0)
    pts_xy  = project_bev(pts_f)

    ego_pose  = loader.get_ego_pose(sd_token)
    origin_xy = np.array(ego_pose["translation"][:2])
    annotations = loader.get_annotations_for_sample(sample_token)
    print(f"  {len(annotations)} annotations, origin_xy={origin_xy.round(1)}")

    # Tier 1
    grid_t1 = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
    run_single_scan(loader, scene_name, grid_t1, scan_index=0, verbose=False)
    prob_t1 = grid_t1.get_probability()

    # Tier 2
    sbl = PCSBL(**PCSBL_KWARGS)
    ego_xy = np.zeros(2)
    res = sbl.run(pts_xy, ego_xy=ego_xy)
    prob_t2 = res['prob_map']

    # Build box masks (all annotations → one combined mask, each individual outline)
    half = GRID_SIZE * CELL_SIZE / 2.0
    xs = np.linspace(-half + CELL_SIZE / 2, half - CELL_SIZE / 2, GRID_SIZE)
    ys = np.linspace(-half + CELL_SIZE / 2, half - CELL_SIZE / 2, GRID_SIZE)
    gx, gy = np.meshgrid(xs, ys)
    cell_centres = np.stack([
        gx.ravel() + origin_xy[0],
        (np.flipud(gy)).ravel() + origin_xy[1],
    ], axis=1)

    fig, axes = plt.subplots(1, 2, figsize=(16, 8))

    for ax, (prob, label, color) in zip(axes, [
        (prob_t1, "Tier 1 (Bayesian OGM)", "cyan"),
        (prob_t2, f"Tier 2 (PC-SBL β=1)", "yellow"),
    ]):
        # Background: grayscale prob map
        ax.imshow(prob, cmap='gray', vmin=0, vmax=1, origin='upper',
                  extent=[-half, half, -half, half])

        # Occupied cells as colored dots
        occ_mask = prob > THRESHOLD
        occ_rows, occ_cols = np.where(occ_mask)
        # Convert pixel coords to world coords
        occ_x = -half + (occ_cols + 0.5) * CELL_SIZE
        occ_y =  half - (occ_rows + 0.5) * CELL_SIZE
        ax.scatter(occ_x, occ_y, c=color, s=4, alpha=0.8, label=f"{label} occupied",
                   zorder=3)
        n_occ = occ_mask.sum()

        # Draw GT box outlines in ego-centric coordinates (shift by -origin_xy)
        n_in_grid = 0
        for ann in annotations:
            corners_global = _box_corners_bev(ann)   # (4,2) global
            corners_ego = corners_global - origin_xy  # shift to ego-centric
            # Check if box center is within grid
            cx_ego, cy_ego = corners_ego.mean(axis=0)
            if abs(cx_ego) < half and abs(cy_ego) < half:
                poly = plt.Polygon(corners_ego, fill=False, edgecolor='red',
                                   linewidth=1.5, zorder=4)
                ax.add_patch(poly)
                n_in_grid += 1

        # Count T2 cells inside GT boxes per annotation (for overlap verification)
        path_hits = 0
        for ann in annotations:
            corners_global = _box_corners_bev(ann)
            path = Path(corners_global)
            inside = path.contains_points(cell_centres).reshape(GRID_SIZE, GRID_SIZE)
            overlap = (occ_mask & inside).sum()
            path_hits += overlap

        ax.set_xlim(-half, half)
        ax.set_ylim(-half, half)
        ax.set_aspect('equal')
        ax.set_title(f"{scene_name} — {label}\n"
                     f"occ cells={n_occ}  boxes_in_grid={n_in_grid}  "
                     f"occ∩boxes={path_hits}",
                     fontsize=10)
        ax.set_xlabel("x (m, ego-centric)")
        ax.set_ylabel("y (m, ego-centric)")
        ax.legend(fontsize=8, loc='upper right')

        # Draw ego marker
        ax.plot(0, 0, 'g*', markersize=12, zorder=5, label='ego')
        ax.legend(fontsize=8, loc='upper right')

    fig.suptitle(f"IoBB sanity overlay — {scene_name}\n"
                 f"Red outlines = GT boxes (ego-centric).  "
                 f"Colored dots = predicted occupied cells (P>{THRESHOLD}).",
                 fontsize=11)
    plt.tight_layout()
    out_path = f"output/iobb_overlay_{scene_name}.png"
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"  Saved → {out_path}")
    plt.close()

print("\nDone. Check output/iobb_overlay_*.png for overlap (or lack of).")
