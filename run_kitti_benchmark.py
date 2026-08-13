"""
KITTI Generalisability Benchmark — Phase 10

Run the identical preprocessing + ISM + PC-SBL pipeline on KITTI 3D Object
Detection frames and measure the same IoBB, NMSE and Precision metrics as
Phase 7 (nuScenes).  The nuScenes pipeline is not touched.

Usage
-----
  ~/.pyenv/versions/3.11.9/bin/python3 run_kitti_benchmark.py
  ~/.pyenv/versions/3.11.9/bin/python3 run_kitti_benchmark.py --n-frames 10 --verbose
  ~/.pyenv/versions/3.11.9/bin/python3 run_kitti_benchmark.py --n-frames 5 --start-frame 0

Config
------
  KITTI_ROOT: path to kitti/training/ inside this project directory.
  PCSBL_KWARGS: identical to Phase 7 optimal config (β=1, γ=30, fw=0.5,
                hits_per_bin=3, tol=2e-3, max_iter=150).

Outputs written to output/:
  kitti_benchmark.png       — NMSE box plots T1 vs T2(β=0) vs T2(β=1)
  kitti_iobb_overlay_XXXXXX.png — GT box overlay for first frame
  kitti_table.txt           — per-frame + aggregate table
Results appended to results/results_log.md.
"""

import argparse
import os
import sys
import time
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, os.path.dirname(__file__))

from src.kitti_loader import KITTILoader
from src.preprocessor import (
    transform_to_ego, height_filter, range_filter, project_bev, discretize,
)
from src.occupancy_grid import OccupancyGrid
from src.sensor_model import update_grid_from_scan
from src.pc_sbl import PCSBL
from src.metrics import (
    compute_angular_nmse, compute_iobb_kitti, compute_precision_kitti,
)

# ─────────────────────────── Paths / Config ──────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KITTI_ROOT = os.path.join(SCRIPT_DIR, "kitti", "training")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
RESULTS_LOG = os.path.join(SCRIPT_DIR, "results", "results_log.md")

GRID_SIZE = 80
CELL_SIZE = 0.5
HALF_RANGE = GRID_SIZE * CELL_SIZE / 2.0   # 20 m

PCSBL_KWARGS = dict(
    gamma_init=30.0,
    gamma_fixed=30.0,
    free_weight=0.5,
    hits_per_bin=3,
    tol=2e-3,
    max_iter=150,
    n_angles=360,
    grid_size_sbl=GRID_SIZE,
    cell_size_sbl=CELL_SIZE,
)

ETA_TH = 0.5   # P(occ) threshold for IoBB and NMSE


# ─────────────────────────── Preprocessing helper ────────────────────────────

def _preprocess_kitti(loader: KITTILoader, frame_id: str):
    """
    Load + preprocess one KITTI frame.  Returns:
        cells      — (M, 2) int  (row, col) after all filters
        points_xy  — (M, 2) float BEV coordinates in ego frame (for NMSE)
        cs         — calibration dict (already used; returned for reference)
    """
    pts_raw = loader.load_lidar_points(frame_id)   # (N, 4) sensor frame
    cs = loader.get_calibrated_sensor(frame_id)
    pts_ego = transform_to_ego(pts_raw, cs)        # apply Tr_velo_to_cam + R_cam_to_ego

    pts_f = height_filter(pts_ego)
    pts_f = range_filter(pts_f, half_range=HALF_RANGE)
    points_xy = project_bev(pts_f)                 # (M, 2)
    cells = discretize(points_xy, half_range=HALF_RANGE,
                       cell_size=CELL_SIZE, grid_size=GRID_SIZE)
    return cells, points_xy


# ─────────────────────────── Tier 1 helper ───────────────────────────────────

def _run_t1(cells) -> OccupancyGrid:
    grid = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
    update_grid_from_scan(grid, cells)
    return grid


# ─────────────────────────── Tier 2 helper ───────────────────────────────────

def _run_t2(points_xy: np.ndarray, beta: float) -> tuple[np.ndarray, int, bool]:
    """
    Run PC-SBL on one frame.

    Returns:
        prob_map   — (GRID_SIZE, GRID_SIZE) float P(occ)
        n_iters    — EM iterations taken
        converged  — True if EM converged within max_iter
    """
    kwargs = dict(PCSBL_KWARGS)
    kwargs["beta"] = beta

    sbl = PCSBL(**kwargs)
    result = sbl.run(points_xy)

    prob_map = result["prob_map"]     # already (GRID_SIZE, GRID_SIZE)
    n_iters  = result["iters"]
    converged = result["converged"]
    return prob_map, n_iters, converged


# ─────────────────────────── IoBB overlay helper ─────────────────────────────

def _save_iobb_overlay(frame_id: str, prob_map: np.ndarray,
                       labels: list[dict], tag: str = "t1"):
    """Save a BEV heatmap with GT box outlines for one KITTI frame."""
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.imshow(1.0 - prob_map, cmap="gray", vmin=0, vmax=1,
              origin="upper", extent=[-HALF_RANGE, HALF_RANGE, -HALF_RANGE, HALF_RANGE])
    ax.set_title(f"KITTI {frame_id} — {tag.upper()}")
    ax.set_xlabel("x (m, right)")
    ax.set_ylabel("y (m, forward)")

    from matplotlib.patches import Polygon
    from src.metrics import _kitti_box_bev
    for lbl in labels:
        x_cam, _, z_cam = lbl["location"]
        cx_ego, cy_ego = float(x_cam), float(z_cam)
        if abs(cx_ego) > HALF_RANGE or abs(cy_ego) > HALF_RANGE:
            continue
        _, w, l = lbl["dimensions"]
        corners = _kitti_box_bev(cx_ego, cy_ego, w, l, lbl["rotation_y"])
        patch = Polygon(corners, closed=True, fill=False, edgecolor="red", linewidth=1.0)
        ax.add_patch(patch)

    out_path = os.path.join(OUTPUT_DIR, f"kitti_iobb_overlay_{frame_id}_{tag}.png")
    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)
    print(f"  → {out_path}")


# ─────────────────────────── Benchmark plot ──────────────────────────────────

def _save_benchmark_plot(rows: list[dict]):
    """Box plots of NMSE distribution for T1, T2β=0, T2β=1."""
    t1_nmse  = [r["t1_nmse"]  for r in rows if r["t1_nmse"]  == r["t1_nmse"]]
    t2b0     = [r["t2b0_nmse"] for r in rows if r["t2b0_nmse"] == r["t2b0_nmse"]]
    t2b1     = [r["t2b1_nmse"] for r in rows if r["t2b1_nmse"] == r["t2b1_nmse"]]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # NMSE box plot
    ax = axes[0]
    data = [d for d in [t1_nmse, t2b0, t2b1] if d]
    labels_plot = []
    colors = []
    color_map = {"T1": "#4e79a7", "T2 β=0": "#f28e2b", "T2 β=1": "#59a14f"}
    for name, d in [("T1", t1_nmse), ("T2 β=0", t2b0), ("T2 β=1", t2b1)]:
        if d:
            labels_plot.append(name)
            colors.append(color_map[name])
    bp = ax.boxplot([t1_nmse, t2b0, t2b1], tick_labels=["T1", "T2 β=0", "T2 β=1"],
                    patch_artist=True, medianprops={"color": "black"})
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
    ax.set_title("NMSE Distribution (KITTI)")
    ax.set_ylabel("NMSE (lower = better)")
    ax.grid(True, alpha=0.3)

    # IoBB scatter
    ax2 = axes[1]
    t1_iobb  = [r["t1_iobb"]  for r in rows]
    t2b1_iobb = [r["t2b1_iobb"] for r in rows]
    ax2.scatter(t1_iobb, t2b1_iobb, alpha=0.7, color="#59a14f")
    lim = max(max(t1_iobb + [0.01]), max(t2b1_iobb + [0.01])) * 1.1
    ax2.plot([0, lim], [0, lim], "k--", linewidth=0.8, label="T1 = T2")
    ax2.set_xlabel("T1 IoBB")
    ax2.set_ylabel("T2 β=1 IoBB")
    ax2.set_title("IoBB: T1 vs T2 β=1 (KITTI)")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    fig.suptitle(f"KITTI Benchmark — {len(rows)} frames", fontsize=13)
    fig.tight_layout()
    out_path = os.path.join(OUTPUT_DIR, "kitti_benchmark.png")
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"\n→ Plot: {out_path}")


# ─────────────────────────── Results logging ─────────────────────────────────

def _log_results(rows: list[dict]):
    os.makedirs(os.path.dirname(RESULTS_LOG), exist_ok=True)
    if not os.path.exists(RESULTS_LOG):
        with open(RESULTS_LOG, "w") as f:
            f.write("# Results Log — LiDAR OGM (TH OWL Team 02)\n\n")
            f.write("| date | frame | tier | β | IoBB | NMSE | Precision | iters | elapsed_s |\n")
            f.write("|---|---|---|---|---|---|---|---|---|\n")

    with open(RESULTS_LOG, "a") as f:
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        f.write(f"\n## Phase 10 — KITTI Benchmark ({date_str})\n\n")
        f.write("| frame | T1 IoBB | T1 NMSE | T2β=0 NMSE | T2β=1 IoBB | T2β=1 NMSE | T2β=1 Prec | β=0 iters | β=1 iters |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(
                f"| {r['frame_id']} "
                f"| {r['t1_iobb']:.3f} "
                f"| {r['t1_nmse']:.4f} "
                f"| {r['t2b0_nmse']:.4f} "
                f"| {r['t2b1_iobb']:.3f} "
                f"| {r['t2b1_nmse']:.4f} "
                f"| {r['t2b1_prec']:.3f} "
                f"| {r['t2b0_iters']} "
                f"| {r['t2b1_iters']} |\n"
            )

        # Aggregate means
        def _mean(key):
            vals = [r[key] for r in rows if r[key] == r[key]]
            return float(np.mean(vals)) if vals else float("nan")

        f.write(
            f"| **mean** "
            f"| **{_mean('t1_iobb'):.3f}** "
            f"| **{_mean('t1_nmse'):.4f}** "
            f"| **{_mean('t2b0_nmse'):.4f}** "
            f"| **{_mean('t2b1_iobb'):.3f}** "
            f"| **{_mean('t2b1_nmse'):.4f}** "
            f"| **{_mean('t2b1_prec'):.3f}** "
            f"| **{_mean('t2b0_iters'):.0f}** "
            f"| **{_mean('t2b1_iters'):.0f}** |\n"
        )

    print(f"→ Results appended to {RESULTS_LOG}")


# ─────────────────────────── Table text ──────────────────────────────────────

def _save_table_txt(rows: list[dict]):
    out_path = os.path.join(OUTPUT_DIR, "kitti_table.txt")
    header = (
        f"{'Frame':>8}  {'T1 IoBB':>8}  {'T1 NMSE':>8}  "
        f"{'β=0 NMSE':>9}  {'β=1 IoBB':>9}  {'β=1 NMSE':>9}  "
        f"{'β=1 Prec':>9}  {'β=0 it':>6}  {'β=1 it':>6}"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"{r['frame_id']:>8}  {r['t1_iobb']:8.3f}  {r['t1_nmse']:8.4f}  "
            f"{r['t2b0_nmse']:9.4f}  {r['t2b1_iobb']:9.3f}  {r['t2b1_nmse']:9.4f}  "
            f"{r['t2b1_prec']:9.3f}  {r['t2b0_iters']:6d}  {r['t2b1_iters']:6d}"
        )

    def _mean(key):
        vals = [r[key] for r in rows if r[key] == r[key]]
        return float(np.mean(vals)) if vals else float("nan")

    lines.append(sep)
    lines.append(
        f"{'MEAN':>8}  {_mean('t1_iobb'):8.3f}  {_mean('t1_nmse'):8.4f}  "
        f"{_mean('t2b0_nmse'):9.4f}  {_mean('t2b1_iobb'):9.3f}  {_mean('t2b1_nmse'):9.4f}  "
        f"{_mean('t2b1_prec'):9.3f}  {_mean('t2b0_iters'):6.0f}  {_mean('t2b1_iters'):6.0f}"
    )

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"→ Table: {out_path}")


# ─────────────────────────── Main ────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="KITTI generalisability benchmark")
    p.add_argument("--kitti-root", default=KITTI_ROOT,
                   help="Path to kitti/training/")
    p.add_argument("--n-frames", type=int, default=50,
                   help="Number of frames to evaluate (default 50)")
    p.add_argument("--start-frame", type=int, default=0,
                   help="Index into the sorted frame list to start from")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-frame progress")
    return p.parse_args()


def main():
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.isdir(args.kitti_root):
        print(
            f"ERROR: KITTI training directory not found at:\n  {args.kitti_root}\n"
            f"Download the dataset and extract to that path.\n"
            f"See kitti_dataset.md for instructions."
        )
        sys.exit(1)

    loader = KITTILoader(args.kitti_root)
    all_frames = loader.list_frames()

    if not all_frames:
        print(f"ERROR: No frames found in {args.kitti_root}/velodyne/")
        sys.exit(1)

    start = args.start_frame
    end = min(start + args.n_frames, len(all_frames))
    frame_ids = all_frames[start:end]

    print(f"KITTI benchmark: {len(frame_ids)} frames "
          f"({frame_ids[0]} … {frame_ids[-1]})")
    print(f"Grid: {GRID_SIZE}×{GRID_SIZE}, {CELL_SIZE} m/cell, ±{HALF_RANGE} m")
    print(f"PC-SBL config: β=1, γ={PCSBL_KWARGS['gamma_fixed']}, "
          f"fw={PCSBL_KWARGS['free_weight']}, hits={PCSBL_KWARGS['hits_per_bin']}\n")

    rows = []
    overlay_done = False

    for i, frame_id in enumerate(frame_ids):
        t0 = time.perf_counter()

        # ── Preprocess ──────────────────────────────────────────────────────
        try:
            cells, points_xy = _preprocess_kitti(loader, frame_id)
        except Exception as e:
            print(f"  [{i+1}/{len(frame_ids)}] {frame_id}: PREPROCESS ERROR — {e}")
            continue

        labels = loader.get_annotations_for_frame(frame_id)
        n_obj = len(labels)

        # ── Tier 1 ──────────────────────────────────────────────────────────
        t1_grid = _run_t1(cells)
        t1_prob = t1_grid.get_probability()

        t1_iobb_res  = compute_iobb_kitti(t1_prob, labels, GRID_SIZE, CELL_SIZE, ETA_TH)
        t1_nmse_res  = compute_angular_nmse(t1_prob, points_xy, GRID_SIZE, CELL_SIZE,
                                             threshold=ETA_TH)

        # ── Tier 2 β=0 ──────────────────────────────────────────────────────
        try:
            t2b0_prob, t2b0_iters, t2b0_conv = _run_t2(points_xy, beta=0.0)
            t2b0_nmse = compute_angular_nmse(t2b0_prob, points_xy, GRID_SIZE, CELL_SIZE,
                                              threshold=ETA_TH)["nmse"]
        except Exception as e:
            print(f"  [{i+1}/{len(frame_ids)}] {frame_id}: T2β=0 ERROR — {e}")
            t2b0_prob, t2b0_iters, t2b0_conv, t2b0_nmse = None, 0, False, float("nan")

        # ── Tier 2 β=1 ──────────────────────────────────────────────────────
        try:
            t2b1_prob, t2b1_iters, t2b1_conv = _run_t2(points_xy, beta=1.0)
            t2b1_iobb = compute_iobb_kitti(t2b1_prob, labels, GRID_SIZE, CELL_SIZE, ETA_TH)["iobb_mean"]
            t2b1_nmse = compute_angular_nmse(t2b1_prob, points_xy, GRID_SIZE, CELL_SIZE,
                                              threshold=ETA_TH)["nmse"]
            t2b1_prec = compute_precision_kitti(t2b1_prob, labels, GRID_SIZE, CELL_SIZE, ETA_TH)["precision"]
        except Exception as e:
            print(f"  [{i+1}/{len(frame_ids)}] {frame_id}: T2β=1 ERROR — {e}")
            t2b1_prob, t2b1_iters, t2b1_conv = None, 0, False
            t2b1_iobb = t2b1_nmse = t2b1_prec = float("nan")

        elapsed = time.perf_counter() - t0

        row = {
            "frame_id":  frame_id,
            "n_objects": n_obj,
            "t1_iobb":   t1_iobb_res["iobb_mean"] if t1_iobb_res["n_objects"] > 0 else float("nan"),
            "t1_nmse":   t1_nmse_res["nmse"],
            "t2b0_nmse": t2b0_nmse,
            "t2b0_iters": t2b0_iters,
            "t2b0_conv": t2b0_conv,
            "t2b1_iobb": t2b1_iobb,
            "t2b1_nmse": t2b1_nmse,
            "t2b1_prec": t2b1_prec,
            "t2b1_iters": t2b1_iters,
            "t2b1_conv": t2b1_conv,
        }
        rows.append(row)

        if args.verbose or i < 3:
            conv0 = "✓" if t2b0_conv else "✗"
            conv1 = "✓" if t2b1_conv else "✗"
            print(
                f"  [{i+1:3d}/{len(frame_ids)}] {frame_id}  "
                f"obj={n_obj:2d}  pts={len(cells):5d}  "
                f"T1 IoBB={row['t1_iobb']:.3f} NMSE={row['t1_nmse']:.4f}  "
                f"T2β=0 NMSE={t2b0_nmse:.4f}({t2b0_iters}it{conv0})  "
                f"T2β=1 IoBB={t2b1_iobb:.3f} NMSE={t2b1_nmse:.4f}({t2b1_iters}it{conv1})  "
                f"[{elapsed:.1f}s]"
            )
        elif (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(frame_ids)}] processed…")

        # Save IoBB overlay for first frame only
        if not overlay_done and t1_prob is not None:
            _save_iobb_overlay(frame_id, t1_prob, labels, tag="t1")
            if t2b1_prob is not None:
                _save_iobb_overlay(frame_id, t2b1_prob, labels, tag="t2b1")
            overlay_done = True

    if not rows:
        print("No frames processed — check dataset path.")
        return

    # ── Summary ─────────────────────────────────────────────────────────────
    def _mean(key):
        vals = [r[key] for r in rows if r[key] == r[key]]
        return float(np.mean(vals)) if vals else float("nan")

    conv_b0 = sum(1 for r in rows if r["t2b0_conv"])
    conv_b1 = sum(1 for r in rows if r["t2b1_conv"])
    n = len(rows)

    print(f"\n{'='*70}")
    print(f"KITTI — {n} frames  (mean ± over-frame)")
    print(f"  T1   IoBB={_mean('t1_iobb'):.3f}  NMSE={_mean('t1_nmse'):.4f}")
    print(f"  T2β=0  NMSE={_mean('t2b0_nmse'):.4f}  converged {conv_b0}/{n}")
    print(f"  T2β=1  IoBB={_mean('t2b1_iobb'):.3f}  NMSE={_mean('t2b1_nmse'):.4f}  "
          f"Prec={_mean('t2b1_prec'):.3f}  converged {conv_b1}/{n}")

    b0_nmse = _mean("t2b0_nmse")
    b1_nmse = _mean("t2b1_nmse")
    if b0_nmse > 0 and b1_nmse == b1_nmse:
        coupling_delta = 100.0 * (b0_nmse - b1_nmse) / b0_nmse
        print(f"  β coupling Δ NMSE = {coupling_delta:+.1f}%  "
              f"({'improvement' if coupling_delta > 0 else 'regression'})")

    print(f"\nComparison with nuScenes Phase 7:")
    print(f"  {'Metric':<22} {'nuScenes':>12}  {'KITTI':>12}")
    print(f"  {'T1 NMSE mean':<22} {'0.1064':>12}  {_mean('t1_nmse'):12.4f}")
    print(f"  {'T2(β=0) NMSE mean':<22} {'0.1503':>12}  {_mean('t2b0_nmse'):12.4f}")
    print(f"  {'T2(β=1) NMSE mean':<22} {'0.1131':>12}  {_mean('t2b1_nmse'):12.4f}")
    print(f"  {'β coupling Δ':<22} {'-24.7%':>12}  "
          f"{f'{coupling_delta:+.1f}%' if 'coupling_delta' in dir() else 'n/a':>12}")

    _save_table_txt(rows)
    _save_benchmark_plot(rows)
    _log_results(rows)


if __name__ == "__main__":
    main()
