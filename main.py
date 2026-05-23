"""
Entry point for Tier 1 — Classical Bayesian Occupancy Grid Mapping.

Modes:
  Default (--world-frame):  accumulate all scans in a world-centred grid.
  --single-scan:            one keyframe only — cleanest demo for presentations.
  --ego-frame:              accumulate all scans in moving ego frame (shows smearing).

Usage:
    python3 main.py --data-root v1.0-mini --scene scene-0061 --out output/
    python3 main.py --data-root v1.0-mini --scene scene-0061 --single-scan --out output/
    python3 main.py --data-root v1.0-mini --all-scenes --out output/ --no-show
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.data_loader import NuScenesLoader
from src.occupancy_grid import OccupancyGrid
from src.bayesian_ogm import run_bayesian_ogm, run_single_scan
from src.visualizer import plot_grid


def parse_args():
    p = argparse.ArgumentParser(description="LiDAR Bayesian Occupancy Grid Mapping")
    p.add_argument("--data-root", default="v1.0-mini",
                   help="Path to the v1.0-mini dataset root directory")
    p.add_argument("--scene", default="scene-0061",
                   help="Scene name to process (e.g. scene-0061)")
    p.add_argument("--all-scenes", action="store_true",
                   help="Process all 10 scenes instead of one")
    p.add_argument("--out", default="output",
                   help="Directory to save grid PNG images")
    p.add_argument("--no-show", action="store_true",
                   help="Do not call plt.show() (useful for headless servers)")
    p.add_argument("--grid-size", type=int, default=80,
                   help="Grid cells per side (default 80 → 40 m at 0.5 m/cell)")
    p.add_argument("--cell-size", type=float, default=0.5,
                   help="Metres per cell (default 0.5)")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--single-scan", action="store_true",
                      help="Use only one keyframe (cleanest demo, no motion smearing)")
    mode.add_argument("--ego-frame", action="store_true",
                      help="Accumulate all scans in moving ego frame (causes smearing)")

    p.add_argument("--scan-index", type=int, default=0,
                   help="Which keyframe to use with --single-scan (default 0)")
    p.add_argument("--smooth", type=float, default=0.0,
                   help="Gaussian smoothing sigma in cells (0=off, try 0.8)")
    return p.parse_args()


def process_scene(loader, scene_name, grid, args):
    show = not args.no_show
    print(f"\n=== Processing {scene_name} ===")

    if args.single_scan:
        run_single_scan(loader, scene_name, grid,
                        scan_index=args.scan_index, verbose=True)
        suffix = f"scan{args.scan_index:02d}"
        title = f"Bayesian OGM — {scene_name} (scan {args.scan_index})"
    elif args.ego_frame:
        run_bayesian_ogm(loader, scene_name, grid,
                         world_frame=False, verbose=True)
        suffix = "ego_frame"
        title = f"Bayesian OGM — {scene_name} (ego frame, all scans)"
    else:
        # Default: world-frame accumulation
        run_bayesian_ogm(loader, scene_name, grid,
                         world_frame=True, verbose=True)
        suffix = "world_frame"
        title = f"Bayesian OGM — {scene_name} (world frame, all scans)"

    show_ego = args.single_scan or args.ego_frame  # centre marker only valid for ego-frame modes
    save_path = os.path.join(args.out, f"{scene_name}_{suffix}.png")
    plot_grid(grid, title=title, save_path=save_path, show=show,
              smooth_sigma=args.smooth, show_ego=show_ego)


def main():
    args = parse_args()

    print(f"Loading nuScenes-mini from: {args.data_root}")
    loader = NuScenesLoader(args.data_root)

    scenes = loader.list_scenes()
    print(f"Found {len(scenes)} scenes:")
    for s in scenes:
        print(f"  {s['name']} ({s['nbr_samples']} samples) — {s['description'][:60]}")

    grid = OccupancyGrid(grid_size=args.grid_size, cell_size=args.cell_size)

    if args.all_scenes:
        for s in scenes:
            process_scene(loader, s["name"], grid, args)
    else:
        process_scene(loader, args.scene, grid, args)

    print("\nDone.")


if __name__ == "__main__":
    main()
