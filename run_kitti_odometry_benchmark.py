"""
KITTI Odometry Benchmark — multi-frame cross-dataset validation.

Runs the same T1/T2 single-scan pipeline as run_kitti_benchmark.py, then
adds the multi-frame window evaluation that was not possible on KITTI 3D
Objects (because that split has no per-frame ego poses).

No IoBB here — KITTI Odometry has no bounding-box annotations.
Evaluation is NMSE only (+ occ% as a sanity check).

Dataset layout expected
-----------------------
kitti_odometry/
└── dataset/
    ├── sequences/
    │   ├── 00/
    │   │   ├── velodyne/     ← 000000.bin …
    │   │   └── calib.txt
    │   └── …
    └── poses/
        └── 00.txt            ← pose per frame (12 floats, cam0→world)

Download from: https://www.cvlibs.net/datasets/kitti/eval_odometry.php
  - data_odometry_velodyne.zip  (~80 GB full / use sequences 00–02 for testing)
  - data_odometry_calib.zip     (< 1 MB)
  - data_odometry_poses.zip     (< 1 MB)  ← ground truth for sequences 00–10

Usage
-----
  ~/.pyenv/versions/3.11.9/bin/python3 run_kitti_odometry_benchmark.py
  ~/.pyenv/versions/3.11.9/bin/python3 run_kitti_odometry_benchmark.py \\
      --sequence 00 --n-frames 50 --window 2 --verbose

Outputs written to output/:
  kitti_odo_benchmark_<seq>.png    — NMSE single vs multi bar chart
  kitti_odo_table_<seq>.txt        — per-frame results table
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

sys.path.insert(0, os.path.dirname(__file__))

from src.kitti_odometry_loader import KITTIOdometryLoader
from src.preprocessor import (
    transform_to_ego, height_filter, range_filter, project_bev, discretize,
)
from src.occupancy_grid import OccupancyGrid
from src.sensor_model import update_grid_from_scan
from src.pc_sbl import PCSBL
from src.multiframe import load_window, multiframe_t1, multiframe_t2
from src.metrics import compute_angular_nmse

# ─────────────────────────── Paths / Config ──────────────────────────────────

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
ODO_ROOT     = os.path.join(SCRIPT_DIR, "kitti_odometry", "dataset")
OUTPUT_DIR   = os.path.join(SCRIPT_DIR, "output")
RESULTS_LOG  = os.path.join(SCRIPT_DIR, "results", "results_log.md")

GRID_SIZE  = 80
CELL_SIZE  = 0.5
HALF_RANGE = GRID_SIZE * CELL_SIZE / 2.0   # 20 m
ETA_TH     = 0.5

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
    verbose=False,          # suppress per-iteration EM output
)

MULTIFRAME_DECAY = 0.85   # same λ as nuScenes multi-frame study


# ─────────────────────────── Single-frame helpers ────────────────────────────

def _preprocess(loader: KITTIOdometryLoader, token: tuple):
    """Load + preprocess one frame.  Returns (cells, points_xy)."""
    pts_raw = loader.load_lidar_points(token)
    cs      = loader.get_calibrated_sensor(token)
    pts_ego = transform_to_ego(pts_raw, cs)
    pts_f   = height_filter(pts_ego)
    pts_f   = range_filter(pts_f, half_range=HALF_RANGE)
    pts_xy  = project_bev(pts_f)
    cells   = discretize(pts_xy, half_range=HALF_RANGE,
                         cell_size=CELL_SIZE, grid_size=GRID_SIZE)
    return cells, pts_xy


def _run_t1_single(cells) -> np.ndarray:
    g = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
    update_grid_from_scan(g, cells)
    return g.get_probability()


def _run_t2_single(points_xy: np.ndarray, beta: float):
    kwargs = dict(PCSBL_KWARGS, beta=beta)
    sbl = PCSBL(**kwargs)
    res = sbl.run(points_xy)
    return res["prob_map"], res["iters"], res["converged"]


# ─────────────────────────── Multi-frame wrappers ────────────────────────────

def _run_t1_multi(loader, seq_name: str, k: int, w: int):
    """
    Tier-1 multi-frame using the existing multiframe_t1().
    multiframe_t1 expects (loader, scene_name, k, w, decay, ...).
    """
    prob_map, occ_pct = multiframe_t1(
        loader, seq_name,
        k=k, w=w, decay=MULTIFRAME_DECAY,
        grid_size=GRID_SIZE, cell_size=CELL_SIZE,
    )
    return prob_map


def _run_t2_multi(loader, seq_name: str, k: int, w: int):
    """
    Tier-2 multi-frame using the existing multiframe_t2().
    """
    kwargs = dict(PCSBL_KWARGS)
    n_angles     = kwargs.pop("n_angles", 360)
    hits_per_bin = kwargs.pop("hits_per_bin", 3)
    free_weight  = kwargs.pop("free_weight", 0.5)
    # multiframe_t2 pops these internally via **pcsbl_kwargs
    res = multiframe_t2(
        loader, seq_name,
        k=k, w=w, decay=MULTIFRAME_DECAY,
        grid_size=GRID_SIZE, cell_size=CELL_SIZE,
        n_angles=n_angles,
        hits_per_bin=hits_per_bin,
        free_weight=free_weight,
        beta=1.0,
        **{k_: v for k_, v in kwargs.items()
           if k_ not in ("n_angles", "hits_per_bin", "free_weight",
                         "grid_size_sbl", "cell_size_sbl")},
    )
    return res["prob_map"], res["iters"], res["converged"]


# ─────────────────────────── Benchmark loop ──────────────────────────────────

def _append_row(row: dict, seq_name: str, window: int):
    """Flush one result row to the log immediately after a frame completes."""
    os.makedirs(os.path.dirname(RESULTS_LOG), exist_ok=True)
    write_header = not os.path.exists(RESULTS_LOG)
    with open(RESULTS_LOG, "a") as f:
        if write_header:
            f.write("# Results Log — LiDAR OGM (TH OWL Team 02)\n\n")
        # Write section header only on first row of this run
        if row.get("_first_row"):
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            f.write(
                f"\n## KITTI Odometry seq {seq_name} — w={window} ({date_str})\n\n"
            )
            f.write(
                "| frame | T1 single | T1 multi | T2β=1 single | T2β=1 iters | "
                "T2β=1 multi | T2β=1 multi iters |\n"
            )
            f.write("|---|---|---|---|---|---|---|\n")
        f.write(
            f"| {row['frame_idx']} "
            f"| {row['t1_single_nmse']:.4f} "
            f"| {row['t1_multi_nmse']:.4f} "
            f"| {row['t2_single_nmse']:.4f} "
            f"| {row['t2_single_iters']} "
            f"| {row['t2_multi_nmse']:.4f} "
            f"| {row['t2_multi_iters']} |\n"
        )


def run_sequence(loader: KITTIOdometryLoader,
                 seq_name: str,
                 n_frames: int,
                 start_frame: int,
                 window: int,
                 verbose: bool) -> list[dict]:
    """
    Evaluate one sequence.  Returns list of per-frame result dicts.
    """
    scene  = loader.get_scene_by_name(seq_name)
    tokens = loader.get_lidar_tokens_for_scene(scene)

    end = min(start_frame + n_frames, len(tokens))
    eval_indices = list(range(start_frame, end))

    print(f"\nSequence {seq_name}: {len(eval_indices)} frames "
          f"(indices {eval_indices[0]}–{eval_indices[-1]}, "
          f"total seq length {len(tokens)})")
    print(f"Window w={window}, λ={MULTIFRAME_DECAY}")

    rows = []

    for pos, k in enumerate(eval_indices):
        t0 = time.perf_counter()
        token_k = tokens[k]

        # ── single-scan preprocessing ──────────────────────────────────────
        try:
            cells, pts_xy = _preprocess(loader, token_k)
        except Exception as e:
            print(f"  [{pos+1}/{len(eval_indices)}] frame {k}: PREPROCESS ERROR — {e}")
            continue

        # ── T1 single ─────────────────────────────────────────────────────
        t1_single_prob = _run_t1_single(cells)
        t1_single_nmse = compute_angular_nmse(
            t1_single_prob, pts_xy, GRID_SIZE, CELL_SIZE, threshold=ETA_TH
        )["nmse"]

        # ── T2 β=1 single ─────────────────────────────────────────────────
        try:
            t2_single_prob, t2_single_iters, t2_single_conv = _run_t2_single(pts_xy, beta=1.0)
            t2_single_nmse = compute_angular_nmse(
                t2_single_prob, pts_xy, GRID_SIZE, CELL_SIZE, threshold=ETA_TH
            )["nmse"]
        except Exception as e:
            print(f"  [{pos+1}/{len(eval_indices)}] frame {k}: T2 single ERROR — {e}")
            t2_single_nmse = float("nan")
            t2_single_iters, t2_single_conv = 0, False

        # ── T1 multi-frame ────────────────────────────────────────────────
        try:
            t1_multi_prob = _run_t1_multi(loader, seq_name, k, window)
            t1_multi_nmse = compute_angular_nmse(
                t1_multi_prob, pts_xy, GRID_SIZE, CELL_SIZE, threshold=ETA_TH
            )["nmse"]
        except Exception as e:
            print(f"  [{pos+1}/{len(eval_indices)}] frame {k}: T1 multi ERROR — {e}")
            t1_multi_nmse = float("nan")

        # ── T2 β=1 multi-frame ────────────────────────────────────────────
        try:
            t2_multi_prob, t2_multi_iters, t2_multi_conv = _run_t2_multi(
                loader, seq_name, k, window
            )
            t2_multi_nmse = compute_angular_nmse(
                t2_multi_prob, pts_xy, GRID_SIZE, CELL_SIZE, threshold=ETA_TH
            )["nmse"]
        except Exception as e:
            print(f"  [{pos+1}/{len(eval_indices)}] frame {k}: T2 multi ERROR — {e}")
            t2_multi_nmse = float("nan")
            t2_multi_iters, t2_multi_conv = 0, False

        elapsed = time.perf_counter() - t0

        row = {
            "seq":            seq_name,
            "frame_idx":      k,
            "n_pts":          len(cells),
            "t1_single_nmse": t1_single_nmse,
            "t1_multi_nmse":  t1_multi_nmse,
            "t2_single_nmse": t2_single_nmse,
            "t2_single_iters": t2_single_iters,
            "t2_single_conv": t2_single_conv,
            "t2_multi_nmse":  t2_multi_nmse,
            "t2_multi_iters": t2_multi_iters,
            "t2_multi_conv":  t2_multi_conv,
            "elapsed_s":      elapsed,
        }
        row["_first_row"] = (pos == 0)
        rows.append(row)
        _append_row(row, seq_name, window)   # flush immediately

        if verbose or pos < 3 or (pos + 1) % 10 == 0:
            conv_s = "✓" if t2_single_conv else "✗"
            conv_m = "✓" if t2_multi_conv  else "✗"
            print(
                f"  [{pos+1:3d}/{len(eval_indices)}] k={k:5d}  pts={len(cells):5d}  "
                f"T1s={t1_single_nmse:.4f} T1m={t1_multi_nmse:.4f}  "
                f"T2s={t2_single_nmse:.4f}({t2_single_iters}it{conv_s})  "
                f"T2m={t2_multi_nmse:.4f}({t2_multi_iters}it{conv_m})  "
                f"[{elapsed:.1f}s]"
            )

    return rows


# ─────────────────────────── Output helpers ──────────────────────────────────

def _mean(rows: list[dict], key: str) -> float:
    vals = [r[key] for r in rows if r[key] == r[key]]
    return float(np.mean(vals)) if vals else float("nan")


def _save_plot(rows: list[dict], seq_name: str):
    """Bar chart: T1 single vs T1 multi vs T2 single vs T2 multi NMSE."""
    labels  = ["T1 single", f"T1 multi\n(w={args.window})",
               "T2 β=1\nsingle", f"T2 β=1\nmulti (w={args.window})"]
    means   = [
        _mean(rows, "t1_single_nmse"),
        _mean(rows, "t1_multi_nmse"),
        _mean(rows, "t2_single_nmse"),
        _mean(rows, "t2_multi_nmse"),
    ]
    colors  = ["#4e79a7", "#a0cbe8", "#59a14f", "#8cd17d"]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, means, color=colors, edgecolor="black", linewidth=0.6)
    for bar, val in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.001,
                f"{val:.4f}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Mean NMSE (lower = better)")
    ax.set_title(f"KITTI Odometry seq {seq_name} — single vs multi-frame "
                 f"({len(rows)} frames)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUTPUT_DIR, f"kitti_odo_benchmark_{seq_name}.png")
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"→ Plot: {out}")


def _save_table(rows: list[dict], seq_name: str):
    header = (
        f"{'k':>6}  {'pts':>6}  "
        f"{'T1s':>8}  {'T1m':>8}  "
        f"{'T2s':>8}  {'T2s-it':>6}  "
        f"{'T2m':>8}  {'T2m-it':>6}  {'t(s)':>6}"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"{r['frame_idx']:6d}  {r['n_pts']:6d}  "
            f"{r['t1_single_nmse']:8.4f}  {r['t1_multi_nmse']:8.4f}  "
            f"{r['t2_single_nmse']:8.4f}  {r['t2_single_iters']:6d}  "
            f"{r['t2_multi_nmse']:8.4f}  {r['t2_multi_iters']:6d}  "
            f"{r['elapsed_s']:6.1f}"
        )
    lines.append(sep)
    lines.append(
        f"{'MEAN':>6}  {'':>6}  "
        f"{_mean(rows,'t1_single_nmse'):8.4f}  {_mean(rows,'t1_multi_nmse'):8.4f}  "
        f"{_mean(rows,'t2_single_nmse'):8.4f}  {_mean(rows,'t2_single_iters'):6.0f}  "
        f"{_mean(rows,'t2_multi_nmse'):8.4f}  {_mean(rows,'t2_multi_iters'):6.0f}"
    )
    out = os.path.join(OUTPUT_DIR, f"kitti_odo_table_{seq_name}.txt")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"→ Table: {out}")


def _log_results(rows: list[dict], seq_name: str, window: int):
    os.makedirs(os.path.dirname(RESULTS_LOG), exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(RESULTS_LOG, "a") as f:
        f.write(f"\n## KITTI Odometry seq {seq_name} — w={window} ({date_str})\n\n")
        f.write(
            "| frame | T1 single | T1 multi | T2β=1 single | T2β=1 iters | "
            "T2β=1 multi | T2β=1 multi iters |\n"
        )
        f.write("|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(
                f"| {r['frame_idx']} "
                f"| {r['t1_single_nmse']:.4f} "
                f"| {r['t1_multi_nmse']:.4f} "
                f"| {r['t2_single_nmse']:.4f} "
                f"| {r['t2_single_iters']} "
                f"| {r['t2_multi_nmse']:.4f} "
                f"| {r['t2_multi_iters']} |\n"
            )
        f.write(
            f"| **mean** "
            f"| **{_mean(rows,'t1_single_nmse'):.4f}** "
            f"| **{_mean(rows,'t1_multi_nmse'):.4f}** "
            f"| **{_mean(rows,'t2_single_nmse'):.4f}** "
            f"| **{_mean(rows,'t2_single_iters'):.0f}** "
            f"| **{_mean(rows,'t2_multi_nmse'):.4f}** "
            f"| **{_mean(rows,'t2_multi_iters'):.0f}** |\n"
        )
    print(f"→ Results appended to {RESULTS_LOG}")


# ─────────────────────────── CLI ─────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="KITTI Odometry multi-frame benchmark")
    p.add_argument("--odo-root", default=ODO_ROOT,
                   help="Path to kitti_odometry/dataset/")
    p.add_argument("--sequence", default="00",
                   help="Sequence to evaluate (e.g. 00, 01, 02 …)")
    p.add_argument("--n-frames", type=int, default=50,
                   help="Number of eval frames (default 50)")
    p.add_argument("--start-frame", type=int, default=0,
                   help="First frame index in sequence to start from")
    p.add_argument("--window", type=int, default=2,
                   help="Multi-frame half-window w (frames [k-w, k+w], default 2)")
    p.add_argument("--verbose", action="store_true",
                   help="Print every frame")
    return p.parse_args()


args = parse_args()   # module-level so _save_plot can reference args.window


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.isdir(args.odo_root):
        print(
            f"ERROR: KITTI Odometry dataset not found at:\n  {args.odo_root}\n"
            f"Download from:\n"
            f"  https://www.cvlibs.net/datasets/kitti/eval_odometry.php\n"
            f"Extract data_odometry_velodyne, data_odometry_calib, "
            f"data_odometry_poses into:\n  {args.odo_root}"
        )
        sys.exit(1)

    loader = KITTIOdometryLoader(args.odo_root)
    available = loader.list_sequences()

    if not available:
        print(f"ERROR: No sequences with pose files found in {args.odo_root}")
        sys.exit(1)

    print(f"Available sequences (with poses): {available}")

    if args.sequence not in available:
        print(
            f"ERROR: Sequence '{args.sequence}' not available.\n"
            f"Available: {available}"
        )
        sys.exit(1)

    print(f"\nKITTI Odometry benchmark")
    print(f"  Sequence : {args.sequence}")
    print(f"  Frames   : {args.n_frames} starting at {args.start_frame}")
    print(f"  Window   : w={args.window}  (±{args.window} frames, λ={MULTIFRAME_DECAY})")
    print(f"  Grid     : {GRID_SIZE}×{GRID_SIZE}, {CELL_SIZE} m/cell, ±{HALF_RANGE} m")
    print(f"  PC-SBL   : β=1, γ={PCSBL_KWARGS['gamma_fixed']}, "
          f"fw={PCSBL_KWARGS['free_weight']}, hits={PCSBL_KWARGS['hits_per_bin']}")

    rows = run_sequence(
        loader, args.sequence,
        n_frames=args.n_frames,
        start_frame=args.start_frame,
        window=args.window,
        verbose=args.verbose,
    )

    if not rows:
        print("No frames processed — check dataset path and sequence.")
        return

    # ── Summary ──────────────────────────────────────────────────────────────
    conv_s = sum(1 for r in rows if r["t2_single_conv"])
    conv_m = sum(1 for r in rows if r["t2_multi_conv"])
    n = len(rows)

    print(f"\n{'='*65}")
    print(f"KITTI Odometry seq {args.sequence} — {n} frames, w={args.window}")
    print(f"  {'Method':<30} {'Mean NMSE':>10}")
    print(f"  {'T1 single':<30} {_mean(rows,'t1_single_nmse'):10.4f}")
    print(f"  {'T1 multi (w={})'.format(args.window):<30} {_mean(rows,'t1_multi_nmse'):10.4f}")
    print(f"  {'T2 β=1 single':<30} {_mean(rows,'t2_single_nmse'):10.4f}  "
          f"conv {conv_s}/{n}")
    print(f"  {'T2 β=1 multi (w={})'.format(args.window):<30} "
          f"{_mean(rows,'t2_multi_nmse'):10.4f}  conv {conv_m}/{n}")

    t2s = _mean(rows, "t2_single_nmse")
    t2m = _mean(rows, "t2_multi_nmse")
    if t2s > 0 and t2m == t2m:
        delta_pct = 100.0 * (t2s - t2m) / t2s
        print(f"\n  T2 multi-frame NMSE improvement: {delta_pct:+.1f}% vs single-scan")

    print(f"\n  Comparison with nuScenes multi-frame (w=2):")
    print(f"  {'Metric':<35} {'nuScenes':>10}  {'KITTI Odo':>10}")
    print(f"  {'T1 single NMSE':<35} {'0.1064':>10}  "
          f"{_mean(rows,'t1_single_nmse'):10.4f}")
    print(f"  {'T2(β=1) single NMSE':<35} {'0.1131':>10}  "
          f"{_mean(rows,'t2_single_nmse'):10.4f}")
    t1m_ns = 0.144   # nuScenes T1 multi w=2
    t2m_ns = 0.056   # nuScenes T2 multi w=2
    print(f"  {'T1 multi NMSE (IoBB n/a here)':<35} "
          f"{'(IoBB 0.144)':>10}  {_mean(rows,'t1_multi_nmse'):10.4f}")
    print(f"  {'T2(β=1) multi NMSE (IoBB n/a)':<35} "
          f"{'(IoBB 0.056)':>10}  {_mean(rows,'t2_multi_nmse'):10.4f}")

    _save_table(rows, args.sequence)
    _save_plot(rows, args.sequence)
    print(f"→ Results already appended per-frame to {RESULTS_LOG}")


if __name__ == "__main__":
    main()
