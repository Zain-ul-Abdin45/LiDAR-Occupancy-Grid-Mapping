"""
Multi-frame benchmark (improvement_v7):
  - Window sweep w ∈ {0, 1, 2, 4} on all 10 scenes
  - Tier-1 and Tier-2 (β=1)
  - Eval keyframe k=0 vs k's annotations → IoBB, NMSE, occ%
  - Outputs:
      output/window_sweep.png            — IoBB vs w (T1 and T2)
      output/single_vs_multi_table.txt   — full per-scene table
      output/iobb_overlay_multi_*.png    — visual proof (scene-0061 and 0916)

Run:
    ~/.pyenv/versions/3.11.9/bin/python3 run_multiframe_benchmark.py
"""
import argparse
import sys, os, time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.data_loader import NuScenesLoader, KittiLoader
from src.preprocessor import transform_to_ego, height_filter, range_filter, project_bev
from src.multiframe import multiframe_t1, multiframe_t2
from src.bayesian_ogm import run_single_scan
from src.occupancy_grid import OccupancyGrid
from src.pc_sbl import PCSBL
from src.metrics import compute_iobb, compute_angular_nmse, compute_precision, _box_corners_bev

DEFAULT_DATA_ROOT = "v1.0-mini"
KITTIDATA_ROOT = "kitti_data"
GRID_SIZE = 80
CELL_SIZE = 0.5

DEFAULT_ALL_SCENES = [
    "scene-0061", "scene-0103", "scene-0553", "scene-0655",
    "scene-0757", "scene-0796", "scene-0916", "scene-1077",
    "scene-1094", "scene-1100",
]
KITTI_DEFAULT_SCENES = ["training"]
WINDOWS = [0, 1, 2, 4]
DECAY   = 0.85   # λ — downweight older frames slightly

PCSBL_KWARGS = dict(
    beta=1.0, max_iter=150, tol=2e-3, gamma_fixed=30.0,
    alpha_damping=0.3, hits_per_bin=3, free_weight=0.5, verbose=False,
)

def parse_args():
    parser = argparse.ArgumentParser(description="Multi-frame benchmark for nuscenes or kitti.")
    parser.add_argument("--dataset", choices=["nuscenes", "kitti"], default="nuscenes",
                        help="Dataset to benchmark (default: nuscenes)")
    parser.add_argument("--data-root", default=None,
                        help="Root directory for dataset files")
    parser.add_argument("--out", default=None,
                        help="Output directory for benchmark files. Defaults to output_nuscenes or output_kitti.")
    return parser.parse_args()


def get_loader(dataset: str, data_root: str):
    if dataset == "kitti":
        return KittiLoader(data_root)
    return NuScenesLoader(data_root)


args = parse_args()
root = args.data_root if args.data_root is not None else (
    KITTIDATA_ROOT if args.dataset == "kitti" else DEFAULT_DATA_ROOT
)
args.out = args.out if args.out is not None else f"output_{args.dataset}"
loader = get_loader(args.dataset, root)

if args.dataset == "kitti":
    available_scenes = [scene["name"] for scene in loader.list_scenes()]
    ALL_SCENES = [name for name in available_scenes if name == "training"] or available_scenes
    WINDOWS = [0]
    SINGLE_SCAN_ONLY = True
    print("KITTI dataset detected: multi-frame fusion is disabled. Running single-scan benchmark only.")
else:
    ALL_SCENES = DEFAULT_ALL_SCENES
    SINGLE_SCAN_ONLY = False

os.makedirs(args.out, exist_ok=True)


def get_pts_xy_k0(scene_name):
    """Load eval keyframe (k=0) points and ego metadata."""
    scene = loader.get_scene_by_name(scene_name)
    tokens = loader.get_lidar_tokens_for_scene(scene)
    sd_token = tokens[0]
    pts_raw = loader.load_lidar_points(sd_token)
    cs = loader.get_calibrated_sensor(sd_token)
    pts_ego = transform_to_ego(pts_raw, cs)
    pts_ego = height_filter(pts_ego)
    pts_ego = range_filter(pts_ego, GRID_SIZE * CELL_SIZE / 2.0)
    pts_xy  = project_bev(pts_ego)
    ego_pose = loader.get_ego_pose(sd_token)
    origin_xy = np.array(ego_pose["translation"][:2])
    sample_token = loader.get_sample_token(sd_token)
    annotations = loader.get_annotations_for_sample(sample_token)
    return pts_xy, origin_xy, annotations


# ─────────────────────────────────────────────────────────────────────────────
# Part 1 — window sweep on all 10 scenes
# ─────────────────────────────────────────────────────────────────────────────
print("Part 1: window sweep")

# results[w][scene] = {t1_iobb, t1_nmse, t1_occ, t2_iobb, t2_nmse, t2_occ}
results = {w: {} for w in WINDOWS}

for scene_name in ALL_SCENES:
    print(f"\n  Scene: {scene_name}")
    pts_xy_k0, origin_xy, annotations = get_pts_xy_k0(scene_name)

    for w in WINDOWS:
        print(f"    w={w} ...", end=" ", flush=True)
        t0 = time.perf_counter()

        # ── Tier 1 ──
        prob_t1, occ_t1 = multiframe_t1(
            loader, scene_name, k=0, w=w, decay=DECAY,
            grid_size=GRID_SIZE, cell_size=CELL_SIZE,
        )
        r_iobb_t1 = compute_iobb(prob_t1, annotations, GRID_SIZE, CELL_SIZE, origin_xy)
        r_nmse_t1 = compute_angular_nmse(prob_t1, pts_xy_k0, GRID_SIZE, CELL_SIZE)

        # ── Tier 2 ──
        res_t2 = multiframe_t2(
            loader, scene_name, k=0, w=w, decay=DECAY,
            grid_size=GRID_SIZE, cell_size=CELL_SIZE,
            **PCSBL_KWARGS,
        )
        prob_t2 = res_t2['prob_map']
        occ_t2 = float((prob_t2 > 0.5).mean())
        r_iobb_t2 = compute_iobb(prob_t2, annotations, GRID_SIZE, CELL_SIZE, origin_xy)
        r_prec_t2 = compute_precision(prob_t2, annotations, GRID_SIZE, CELL_SIZE, origin_xy)
        r_nmse_t2 = compute_angular_nmse(prob_t2, pts_xy_k0, GRID_SIZE, CELL_SIZE)

        elapsed = time.perf_counter() - t0
        results[w][scene_name] = {
            't1_iobb': r_iobb_t1['iobb_mean'],
            't1_nmse': r_nmse_t1['nmse'],
            't1_occ':  occ_t1,
            't2_iobb': r_iobb_t2['iobb_mean'],
            't2_prec': r_prec_t2['precision'],
            't2_nmse': r_nmse_t2['nmse'],
            't2_occ':  occ_t2,
            't2_iters': res_t2['iters'],
            't2_conv':  res_t2['converged'],
        }
        print(f"T1 IoBB={r_iobb_t1['iobb_mean']:.3f} T2 IoBB={r_iobb_t2['iobb_mean']:.3f} prec={r_prec_t2['precision']:.3f} [{elapsed:.0f}s]")


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — window_sweep.png
# ─────────────────────────────────────────────────────────────────────────────
print("\nPart 2: window_sweep.png")

if SINGLE_SCAN_ONLY:
    plot_label = "Single-scan"
    plot_caption = "(KITTI single-scan only)"
else:
    plot_label = "Multi-frame"
    plot_caption = "(k=0, λ={:.2f}, 10 scenes)".format(DECAY)

t1_mean_iobb = [np.nanmean([results[w][s]['t1_iobb'] for s in ALL_SCENES]) for w in WINDOWS]
t2_mean_iobb = [np.nanmean([results[w][s]['t2_iobb'] for s in ALL_SCENES]) for w in WINDOWS]
t1_mean_nmse = [np.nanmean([results[w][s]['t1_nmse'] for s in ALL_SCENES]) for w in WINDOWS]
t2_mean_nmse = [np.nanmean([results[w][s]['t2_nmse'] for s in ALL_SCENES]) for w in WINDOWS]

fig, axes = plt.subplots(1, 2, figsize=(12, 5))

ax = axes[0]
ax.plot(WINDOWS, t1_mean_iobb, 'o-', color='steelblue', linewidth=1.8,
        markersize=8, label='Tier 1 (Bayesian OGM)')
ax.plot(WINDOWS, t2_mean_iobb, 's-', color='darkorange', linewidth=1.8,
        markersize=8, label='Tier 2 (PC-SBL β=1)')
ax.set_xlabel('Window half-size w (frames)', fontsize=11)
ax.set_ylabel('Mean IoBB (higher = better)', fontsize=11)
ax.set_title(f'{plot_label} IoBB vs window size\n{plot_caption}', fontsize=10)
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)
ax.set_xticks(WINDOWS)
ax.set_xticklabels([f'w={w}\n({2*w+1} frames)' for w in WINDOWS])

ax = axes[1]
ax.plot(WINDOWS, t1_mean_nmse, 'o-', color='steelblue', linewidth=1.8,
        markersize=8, label='Tier 1')
ax.plot(WINDOWS, t2_mean_nmse, 's-', color='darkorange', linewidth=1.8,
        markersize=8, label='Tier 2 (β=1)')
ax.set_xlabel('Window half-size w (frames)', fontsize=11)
ax.set_ylabel('Mean NMSE (lower = better)', fontsize=11)
ax.set_title(f'{plot_label} NMSE vs window size\n{plot_caption}', fontsize=10)
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)
ax.set_xticks(WINDOWS)
ax.set_xticklabels([f'w={w}\n({2*w+1} frames)' for w in WINDOWS])

fig.suptitle(
    f"{plot_label} accumulation sweep {plot_caption}\n"
    f"Ground removed per-frame in own ego frame before transform",
    fontsize=11
)
plt.tight_layout()
out_path = os.path.join(args.out, 'window_sweep.png')
plt.savefig(out_path, dpi=150, bbox_inches='tight')
plt.close()
print(f"  → {out_path}")


print("Part 3: single_vs_multi_table.txt")

if SINGLE_SCAN_ONLY:
    lines = [
        "Single-scan KITTI benchmark — eval k=0 vs k's boxes",
        "T1 numbers use the original run_single_scan path; T2 is PC-SBL β=1 on the same scan.",
        "",
        f"{'Scene':<14} | {'T1 IoBB':>7} | {'T2 IoBB':>7} | {'T2 Prec':>8} | {'T2 occ%':>7}",
        "-" * 70,
    ]

    t1_iobb, t2_iobb, t2_prec = [], [], []
    for s in ALL_SCENES:
        r = results[0][s]
        lines.append(
            f"{s:<14} | {r['t1_iobb']:7.3f} | {r['t2_iobb']:7.3f} | "
            f"{r['t2_prec']:8.3f} | {100*r['t2_occ']:7.1f}%"
        )
        t1_iobb.append(r['t1_iobb'])
        t2_iobb.append(r['t2_iobb'])
        t2_prec.append(r['t2_prec'])

    lines.append("-" * 70)
    lines.append(
        f"{'mean':<14} | {np.nanmean(t1_iobb):7.3f} | {np.nanmean(t2_iobb):7.3f} | "
        f"{np.nanmean(t2_prec):8.3f} | {'':>7}"
    )
    lines.append("")
    lines.append(
        f"Headline: Single-scan T1 IoBB = {np.nanmean(t1_iobb):.3f}, "
        f"T2 IoBB = {np.nanmean(t2_iobb):.3f}, T2 precision = {np.nanmean(t2_prec):.3f}."
    )
else:
    W_REPORT = 2   # w=2 as the main comparison

    lines = [
        f"Multi-frame (w={W_REPORT}, λ={DECAY}) vs single-scan (w=0) — eval k=0 vs k's boxes",
        f"T1 numbers use the original run_single_scan path (matches Phase 5 reference).",
        "",
        f"{'Scene':<14} | {'T1':>7} | {'T1':>7} | {'T1 Δ':>6} | "
        f"{'T2':>7} | {'T2':>7} | {'T2 Δ':>6} | {'T2 Prec':>8} | {'T2 occ%':>7}",
        f"{'':14} | {'IoBB sgl':>7} | {'IoBB mlt':>7} | {'IoBB':>6} | "
        f"{'IoBB sgl':>7} | {'IoBB mlt':>7} | {'IoBB':>6} | {'multi':>8} | {'multi':>7}",
        "-" * 108,
    ]

    t1_iobb_s, t1_iobb_m = [], []
    t2_iobb_s, t2_iobb_m, t2_prec_m = [], [], []

    def delta(new, old):
        if np.isnan(new) or np.isnan(old):
            return "  n/a"
        return f"{new-old:+.3f}"

    for s in ALL_SCENES:
        r0 = results[0][s]
        rm = results[W_REPORT][s]

        lines.append(
            f"{s:<14} | {r0['t1_iobb']:7.3f} | {rm['t1_iobb']:7.3f} | {delta(rm['t1_iobb'], r0['t1_iobb']):>6} | "
            f"{r0['t2_iobb']:7.3f} | {rm['t2_iobb']:7.3f} | {delta(rm['t2_iobb'], r0['t2_iobb']):>6} | "
            f"{rm['t2_prec']:8.3f} | {100*rm['t2_occ']:7.1f}%"
        )
        t1_iobb_s.append(r0['t1_iobb']); t1_iobb_m.append(rm['t1_iobb'])
        t2_iobb_s.append(r0['t2_iobb']); t2_iobb_m.append(rm['t2_iobb'])
        t2_prec_m.append(rm['t2_prec'])

    lines.append("-" * 108)
    mean_t1s = np.nanmean(t1_iobb_s); mean_t1m = np.nanmean(t1_iobb_m)
    mean_t2s = np.nanmean(t2_iobb_s); mean_t2m = np.nanmean(t2_iobb_m)
    lines.append(
        f"{'mean':<14} | {mean_t1s:7.3f} | {mean_t1m:7.3f} | {mean_t1m-mean_t1s:+.3f} | "
        f"{mean_t2s:7.3f} | {mean_t2m:7.3f} | {mean_t2m-mean_t2s:+.3f} | "
        f"{np.nanmean(t2_prec_m):8.3f} | {'':>7}"
    )
    lines.append("")
    lines.append(
        f"Headline: T2 multi-frame IoBB {mean_t2s:.3f} → {mean_t2m:.3f} (Δ={mean_t2m-mean_t2s:+.3f}). "
        f"T1 multi = {mean_t1m:.3f}. Gap T1−T2 multi = {mean_t1m-mean_t2m:+.3f}."
    )
    lines.append(
        f"Precision multi = {np.nanmean(t2_prec_m):.3f} (IoBB rise reflects genuine coverage, not inflation)."
    )


# Write the table file for both cases.
table_txt = "\n".join(lines)
print(table_txt)
with open(os.path.join(args.out, "single_vs_multi_table.txt"), "w", encoding="utf-8") as f:
    f.write(table_txt + "\n")
print(f"  → {os.path.join(args.out, 'single_vs_multi_table.txt')}")


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 — IoBB overlay (single vs multi)
# ─────────────────────────────────────────────────────────────────────────────
print("Part 4: iobb_overlay_multi_*.png")

if args.dataset == "kitti":
    # Only KITTI training has annotation labels in the object dataset.
    OVERLAY_SCENES = [name for name in ALL_SCENES if name == "training"]
else:
    OVERLAY_SCENES = ["scene-0061", "scene-0916"]

if not OVERLAY_SCENES:
    print("No overlay scenes available for this dataset. Skipping Part 4.")
else:
    for scene_name in OVERLAY_SCENES:
        pts_xy_k0, origin_xy, annotations = get_pts_xy_k0(scene_name)
        half = GRID_SIZE * CELL_SIZE / 2.0

        if args.dataset == "kitti":
            grid_t1 = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
            run_single_scan(loader, scene_name, grid_t1, scan_index=0, verbose=False)
            prob_t1 = grid_t1.get_probability()
            iobb_t1 = compute_iobb(prob_t1, annotations, GRID_SIZE, CELL_SIZE, origin_xy)['iobb_mean']

            sbl_single = PCSBL(**PCSBL_KWARGS, grid_size_sbl=GRID_SIZE, cell_size_sbl=CELL_SIZE)
            res_single = sbl_single.run(pts_xy_k0, ego_xy=np.zeros(2))
            prob_t2 = res_single['prob_map']
            iobb_t2 = compute_iobb(prob_t2, annotations, GRID_SIZE, CELL_SIZE, origin_xy)['iobb_mean']

            panels = [
                (prob_t1, f"T1 single scan\nIoBB={iobb_t1:.3f}", iobb_t1, (prob_t1 > 0.5).sum()),
                (prob_t2, f"T2 single scan β=1\nIoBB={iobb_t2:.3f}", iobb_t2, (prob_t2 > 0.5).sum()),
            ]
            fig_title = f"KITTI single-scan T1 vs T2 — {scene_name}"
        else:
            grid_t1 = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
            run_single_scan(loader, scene_name, grid_t1, scan_index=0, verbose=False)
            prob_single = grid_t1.get_probability()
            iobb_single = compute_iobb(prob_single, annotations, GRID_SIZE, CELL_SIZE, origin_xy)['iobb_mean']

            res_multi = multiframe_t2(
                loader, scene_name, k=0, w=2, decay=DECAY,
                grid_size=GRID_SIZE, cell_size=CELL_SIZE,
                **PCSBL_KWARGS,
            )
            prob_multi = res_multi['prob_map']
            iobb_multi = compute_iobb(prob_multi, annotations, GRID_SIZE, CELL_SIZE, origin_xy)['iobb_mean']

            panels = [
                (prob_single, f"Single scan (w=0)\nIoBB={iobb_single:.3f}", iobb_single, (prob_single > 0.5).sum()),
                (prob_multi,  f"Multi-frame w=2, λ={DECAY}\nIoBB={iobb_multi:.3f}", iobb_multi, (prob_multi > 0.5).sum()),
            ]
            fig_title = f"Multi-frame IoBB proof — {scene_name}"

        fig, axes = plt.subplots(1, 2, figsize=(14, 7))
        for ax, (prob, tag, iobb, n_occ) in zip(axes, panels):
            ax.imshow(prob, cmap='gray', vmin=0, vmax=1, origin='upper',
                      extent=[-half, half, -half, half])
            occ_r, occ_c = np.where(prob > 0.5)
            occ_x = -half + (occ_c + 0.5) * CELL_SIZE
            occ_y =  half - (occ_r + 0.5) * CELL_SIZE
            ax.scatter(occ_x, occ_y, c='yellow', s=3, alpha=0.7, zorder=3)

            for ann in annotations:
                corners_g = _box_corners_bev(ann)
                corners_e = corners_g - origin_xy
                cx, cy = corners_e.mean(axis=0)
                if abs(cx) < half and abs(cy) < half:
                    poly = plt.Polygon(corners_e, fill=False, edgecolor='red',
                                       linewidth=1.5, zorder=4)
                    ax.add_patch(poly)

            ax.plot(0, 0, 'g*', markersize=12, zorder=5)
            ax.set_xlim(-half, half); ax.set_ylim(-half, half)
            ax.set_aspect('equal')
            ax.set_title(f"PC-SBL β=1 — {tag}", fontsize=10)
            ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)')

        fig.suptitle(f"{fig_title}\nRed = GT boxes (ego-centric). Yellow = P>0.5.", fontsize=11)
        plt.tight_layout()
        out = os.path.join(args.out, f"iobb_overlay_multi_{scene_name}.png")
        plt.savefig(out, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  → {out}")

print("\nDone. All output files written.")
