"""
Entry point for LiDAR Occupancy Grid Mapping — Tier 1 and Tier 2.

Modes
-----
  --single-scan   One keyframe only — cleanest per-frame demo; used for evaluation.
  --ego-frame     Accumulate all scans in ego frame (demonstrates smearing artifact).
  (default)       World-frame accumulation — trajectory-centroid-centred grid.

Tier 2 (PC-SBL)
---------------
  --tier2         Run PC-SBL after Tier 1 (single-scan mode required).
  --beta FLOAT    Spatial coupling strength β (0 = decoupled SBL, 1 = full PC-SBL).
  --sbl-grid INT  SBL grid size (default 40 → 1.0 m/cell, 40×40 m).

Evaluation
----------
  --eval          Compute IoBB and angular NMSE for the processed scan(s).
  Results are appended to results/results_log.md automatically.

Examples
--------
  # Tier 1 single scan
  python3 main.py --scene scene-0061 --single-scan --smooth 0.8 --no-show

  # Tier 1 world-frame
  python3 main.py --scene scene-0061 --no-show

  # Tier 2 (PC-SBL, β=1)
  python3 main.py --scene scene-0061 --single-scan --tier2 --beta 1.0 --eval --no-show

  # Ablation: β=0 (decoupled SBL)
  python3 main.py --scene scene-0061 --single-scan --tier2 --beta 0.0 --eval --no-show

  # All scenes, Tier 1, with evaluation
  python3 main.py --all-scenes --single-scan --eval --no-show
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from src.data_loader import NuScenesLoader
from src.occupancy_grid import OccupancyGrid
from src.bayesian_ogm import run_bayesian_ogm, run_single_scan
from src.preprocessor import preprocess, transform_to_ego
from src.visualizer import plot_grid


RESULTS_LOG = os.path.join(os.path.dirname(__file__), "results", "results_log.md")


# ─────────────────────────── CLI ────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="LiDAR OGM — Tier 1 and Tier 2 (PC-SBL)")
    p.add_argument("--data-root", default="v1.0-mini")
    p.add_argument("--scene", default="scene-0061")
    p.add_argument("--all-scenes", action="store_true")
    p.add_argument("--out", default="output")
    p.add_argument("--no-show", action="store_true")
    p.add_argument("--grid-size", type=int, default=80)
    p.add_argument("--cell-size", type=float, default=0.5)
    p.add_argument("--smooth", type=float, default=0.8,
                   help="Gaussian sigma for Tier-1 display (default 0.8)")
    p.add_argument("--scan-index", type=int, default=0)

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--single-scan", action="store_true")
    mode.add_argument("--ego-frame", action="store_true")

    # Tier 2
    p.add_argument("--tier2", action="store_true",
                   help="Run PC-SBL Tier 2 after Tier 1 (requires --single-scan)")
    p.add_argument("--beta", type=float, default=1.0,
                   help="PC-SBL coupling strength β: 0=decoupled, 1=full (default 1.0)")
    p.add_argument("--sbl-grid", type=int, default=80,
                   help="PC-SBL grid size in cells (default 80 → 0.5 m/cell, matches Tier 1)")
    p.add_argument("--sbl-max-iter", type=int, default=100)
    p.add_argument("--sbl-tol", type=float, default=2e-3)
    p.add_argument("--sbl-gamma", type=float, default=50.0,
                   help="Fix γ for PC-SBL (>0 = fixed, 0 = adaptive). "
                        "Default 50 (fixed) — re-enable adaptive only after coupling works.")
    p.add_argument("--sbl-damping", type=float, default=0.3,
                   help="α-update damping λ ∈ [0,1] for β>0 stability (default 0.3)")
    p.add_argument("--n-angles", type=int, default=360,
                   help="Angular bins for C matrix (default 360)")
    p.add_argument("--sbl-hits", type=int, default=3,
                   help="Hits per angular bin for C (default 3; more→denser map, better NMSE)")
    p.add_argument("--sbl-free-weight", type=float, default=1.0,
                   help="Scale factor for free rows in C (default 1.0; try 0.5 to allow more fill)")

    # Evaluation
    p.add_argument("--eval", action="store_true",
                   help="Compute IoBB and angular NMSE; append to results_log.md")
    p.add_argument("--eval-threshold", type=float, default=0.5,
                   help="P(occ) threshold for IoBB (default 0.5)")
    p.add_argument("--sbl-threshold", type=float, default=0.3,
                   help="P(occ) threshold for T2 binarization/NMSE/occ%% (default 0.3; paper value)")

    return p.parse_args()


# ─────────────────────────── Results logging ────────────────────────────────

def _ensure_results_log():
    os.makedirs(os.path.dirname(RESULTS_LOG), exist_ok=True)
    if not os.path.exists(RESULTS_LOG):
        with open(RESULTS_LOG, "w") as f:
            f.write("# Results Log — LiDAR OGM (TH OWL Team 02)\n\n")
            f.write("Append-only. Each row is one experiment run.\n\n")
            f.write("| date | scene | tier | mode | β | cell_size | smooth | "
                    "scan_idx | occupied% | IoBB | NMSE | iters | elapsed_s | notes |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")


def log_result(row: dict):
    _ensure_results_log()
    line = (
        f"| {row.get('date','')} "
        f"| {row.get('scene','')} "
        f"| {row.get('tier','')} "
        f"| {row.get('mode','')} "
        f"| {row.get('beta','—')} "
        f"| {row.get('cell_size','')} "
        f"| {row.get('smooth','')} "
        f"| {row.get('scan_idx','')} "
        f"| {row.get('occupied_pct','')} "
        f"| {row.get('iobb','—')} "
        f"| {row.get('nmse','—')} "
        f"| {row.get('iters','—')} "
        f"| {row.get('elapsed_s','—')} "
        f"| {row.get('notes','')} |\n"
    )
    with open(RESULTS_LOG, "a") as f:
        f.write(line)
    print(f"  → logged to {RESULTS_LOG}")


# ─────────────────────────── Evaluation helper ──────────────────────────────

def _run_eval(loader, sd_token, sample_token, grid, args,
              points_xy_ego=None, origin_xy=None, threshold=None):
    """Compute IoBB and NMSE for one scan's grid state."""
    from src.metrics import compute_iobb, compute_angular_nmse

    if threshold is None:
        threshold = args.eval_threshold

    annotations = loader.get_annotations_for_sample(sample_token)
    prob = grid.get_probability()

    iobb_result = compute_iobb(
        prob, annotations,
        grid.grid_size, grid.cell_size,
        origin_xy=origin_xy,
        threshold=threshold,
    )

    nmse_result = {"nmse": float("nan")}
    if points_xy_ego is not None and len(points_xy_ego) > 0:
        nmse_result = compute_angular_nmse(
            prob, points_xy_ego,
            grid.grid_size, grid.cell_size,
            n_angles=360,
            threshold=threshold,
        )

    return iobb_result, nmse_result


# ─────────────────────────── Scene processing ───────────────────────────────

def process_scene(loader, scene_name, grid, args):
    show = not args.no_show
    print(f"\n=== {scene_name} ===")

    scene = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)

    t_start = time.perf_counter()

    # ── Determine mode ──────────────────────────────────────────────────────
    if args.single_scan:
        mode_str = f"single-scan[{args.scan_index}]"
        run_single_scan(loader, scene_name, grid,
                        scan_index=args.scan_index, verbose=True)
        suffix = f"scan{args.scan_index:02d}"
        title = f"Bayesian OGM — {scene_name} (scan {args.scan_index})"
        show_ego = True

        # Collect points_xy for NMSE evaluation
        sd_token = lidar_tokens[args.scan_index]
        sample_token = loader.get_sample_token(sd_token)
        pts_raw = loader.load_lidar_points(sd_token)
        cs = loader.get_calibrated_sensor(sd_token)
        pts_ego = transform_to_ego(pts_raw, cs)
        # Apply same filters as preprocess
        from src.preprocessor import height_filter, range_filter, project_bev
        pts_f = height_filter(pts_ego)
        pts_f = range_filter(pts_f, grid.grid_size * grid.cell_size / 2)
        points_xy_ego = project_bev(pts_f)
        # Annotations are in global frame — set origin to ego's world position
        ego_pose = loader.get_ego_pose(sd_token)
        origin_xy = np.array(ego_pose["translation"][:2])

    elif args.ego_frame:
        mode_str = "ego-frame"
        run_bayesian_ogm(loader, scene_name, grid, world_frame=False, verbose=True)
        suffix = "ego_frame"
        title = f"Bayesian OGM — {scene_name} (ego frame)"
        show_ego = True
        sd_token = lidar_tokens[0]
        sample_token = loader.get_sample_token(sd_token)
        points_xy_ego = None
        origin_xy = None

    else:
        mode_str = "world-frame"
        run_bayesian_ogm(loader, scene_name, grid, world_frame=True, verbose=True)
        suffix = "world_frame"
        title = f"Bayesian OGM — {scene_name} (world frame)"
        show_ego = False
        sd_token = lidar_tokens[0]
        sample_token = loader.get_sample_token(sd_token)
        # For world-frame: compute centroid for evaluation
        all_xy = np.array([
            loader.get_ego_pose(t)["translation"][:2] for t in lidar_tokens
        ])
        origin_xy = all_xy.mean(axis=0)
        points_xy_ego = None

    elapsed_t1 = time.perf_counter() - t_start

    # ── Save Tier-1 output ──────────────────────────────────────────────────
    save_path_t1 = os.path.join(args.out, f"{scene_name}_{suffix}.png")
    plot_grid(grid, title=title, save_path=save_path_t1, show=show,
              smooth_sigma=args.smooth, show_ego=show_ego)

    prob_t1 = grid.get_probability()
    occ_pct_t1 = f"{100*(prob_t1 > 0.5).mean():.1f}%"

    # ── Tier-1 evaluation ───────────────────────────────────────────────────
    iobb_t1, nmse_t1 = {"iobb_mean": float("nan")}, {"nmse": float("nan")}
    if args.eval:
        iobb_t1, nmse_t1 = _run_eval(
            loader, sd_token, sample_token, grid, args,
            points_xy_ego=points_xy_ego, origin_xy=origin_xy,
        )
        print(f"  [Tier 1] IoBB={iobb_t1['iobb_mean']:.3f}  "
              f"NMSE={nmse_t1['nmse']:.4f}  "
              f"n_objects={iobb_t1['n_objects']}")

    row_t1 = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "scene": scene_name,
        "tier": "T1",
        "mode": mode_str,
        "beta": "—",
        "cell_size": args.cell_size,
        "smooth": args.smooth,
        "scan_idx": args.scan_index if args.single_scan else "all",
        "occupied_pct": occ_pct_t1,
        "iobb": f"{iobb_t1['iobb_mean']:.3f}" if args.eval else "—",
        "nmse": f"{nmse_t1['nmse']:.4f}" if args.eval else "—",
        "iters": "—",
        "elapsed_s": f"{elapsed_t1:.1f}",
        "notes": "",
    }
    log_result(row_t1)

    # ── Tier 2 (PC-SBL) ────────────────────────────────────────────────────
    if args.tier2:
        if not args.single_scan:
            print("  [WARNING] --tier2 requires --single-scan mode; skipping.")
            return

        from src.pc_sbl import PCSBL, upsample_prob_map

        sbl_cell = grid.grid_size * grid.cell_size / args.sbl_grid  # e.g. 80*0.5/40 = 1.0 m
        sbl = PCSBL(
            beta=args.beta,
            max_iter=args.sbl_max_iter,
            tol=args.sbl_tol,
            gamma_fixed=args.sbl_gamma,
            alpha_damping=args.sbl_damping,
            n_angles=args.n_angles,
            hits_per_bin=args.sbl_hits,
            grid_size_sbl=args.sbl_grid,
            cell_size_sbl=sbl_cell,
            free_weight=args.sbl_free_weight,
            verbose=True,
        )

        print(f"\n  [Tier 2] PC-SBL β={args.beta} | "
              f"SBL grid {args.sbl_grid}×{args.sbl_grid} @ {sbl_cell:.2f} m/cell")

        t2_start = time.perf_counter()
        result = sbl.run(points_xy_ego if points_xy_ego is not None else np.zeros((0, 2)))
        elapsed_t2 = time.perf_counter() - t2_start

        # Upsample to Tier-1 resolution for display
        prob_sbl = upsample_prob_map(result["prob_map"], grid.grid_size)

        title_t2 = (f"PC-SBL OGM (β={args.beta}) — {scene_name} (scan {args.scan_index})")
        save_path_t2 = os.path.join(
            args.out,
            f"{scene_name}_pcsbl_b{args.beta:.1f}_scan{args.scan_index:02d}.png"
        )

        # Borrow grid's OccupancyGrid structure for the visualizer
        # Create a temporary grid-like object for the upsampled SBL prob map
        from src.occupancy_grid import OccupancyGrid
        grid_t2 = OccupancyGrid(grid_size=grid.grid_size, cell_size=grid.cell_size)
        # Convert prob to log-odds for the OccupancyGrid
        eps = 1e-6
        log_odds_sbl = np.log(
            np.clip(prob_sbl, eps, 1 - eps) / np.clip(1 - prob_sbl, eps, 1 - eps)
        )
        grid_t2.log_odds = log_odds_sbl.astype(np.float32)

        plot_grid(grid_t2, title=title_t2, save_path=save_path_t2,
                  show=show, smooth_sigma=0.0, show_ego=True)

        sbl_th = args.sbl_threshold
        occ_pct_t2 = f"{100*(prob_sbl > sbl_th).mean():.1f}%"

        iobb_t2, nmse_t2 = {"iobb_mean": float("nan")}, {"nmse": float("nan")}
        if args.eval:
            # Temporarily swap grid log-odds for eval
            orig_lo = grid.log_odds.copy()
            grid.log_odds = log_odds_sbl.astype(np.float32)
            iobb_t2, nmse_t2 = _run_eval(
                loader, sd_token, sample_token, grid, args,
                points_xy_ego=points_xy_ego, origin_xy=origin_xy,
                threshold=sbl_th,
            )
            grid.log_odds = orig_lo
            print(f"  [Tier 2] IoBB={iobb_t2['iobb_mean']:.3f}  "
                  f"NMSE={nmse_t2['nmse']:.4f}")

        row_t2 = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "scene": scene_name,
            "tier": "T2",
            "mode": mode_str,
            "beta": args.beta,
            "cell_size": sbl_cell,
            "smooth": "0.0",
            "scan_idx": args.scan_index,
            "occupied_pct": occ_pct_t2,
            "iobb": f"{iobb_t2['iobb_mean']:.3f}" if args.eval else "—",
            "nmse": f"{nmse_t2['nmse']:.4f}" if args.eval else "—",
            "iters": result["iters"],
            "elapsed_s": f"{elapsed_t2:.1f}",
            "notes": f"converged={result['converged']} eta_th={sbl_th}",
        }
        log_result(row_t2)


# ─────────────────────────── Main ───────────────────────────────────────────

def main():
    args = parse_args()

    # Change working dir to script location so relative paths work
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print(f"Loading nuScenes-mini from: {args.data_root}")
    loader = NuScenesLoader(args.data_root)
    scenes = loader.list_scenes()
    print(f"Found {len(scenes)} scenes:")
    for s in scenes:
        print(f"  {s['name']} ({s['nbr_samples']} samples)")

    grid = OccupancyGrid(grid_size=args.grid_size, cell_size=args.cell_size)
    os.makedirs(args.out, exist_ok=True)

    _ensure_results_log()

    if args.all_scenes:
        for s in scenes:
            process_scene(loader, s["name"], grid, args)
    else:
        process_scene(loader, args.scene, grid, args)

    print("\nDone.")


if __name__ == "__main__":
    main()
