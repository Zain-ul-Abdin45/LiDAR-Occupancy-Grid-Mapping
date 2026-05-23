"""
Entry point for Tier 1 — Classical Bayesian Occupancy Grid Mapping.

Usage:
    python3 main.py --data-root v1.0-mini --scene scene-0061 --out output/
    python3 main.py --data-root v1.0-mini --all-scenes --out output/ --no-show
"""

import argparse
import os
import sys

# Ensure src/ is importable when running from project root
sys.path.insert(0, os.path.dirname(__file__))

from src.data_loader import NuScenesLoader
from src.occupancy_grid import OccupancyGrid
from src.bayesian_ogm import run_bayesian_ogm
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
    return p.parse_args()


def process_scene(loader, scene_name, grid, out_dir, show):
    print(f"\n=== Processing {scene_name} ===")
    run_bayesian_ogm(loader, scene_name, grid, verbose=True)
    save_path = os.path.join(out_dir, f"{scene_name}_bayesian.png")
    plot_grid(
        grid,
        title=f"Bayesian OGM — {scene_name}",
        save_path=save_path,
        show=show,
    )


def main():
    args = parse_args()

    print(f"Loading nuScenes-mini from: {args.data_root}")
    loader = NuScenesLoader(args.data_root)

    scenes = loader.list_scenes()
    print(f"Found {len(scenes)} scenes:")
    for s in scenes:
        print(f"  {s['name']} ({s['nbr_samples']} samples) — {s['description'][:60]}")

    grid = OccupancyGrid(grid_size=args.grid_size, cell_size=args.cell_size)
    show = not args.no_show

    if args.all_scenes:
        for s in scenes:
            process_scene(loader, s["name"], grid, args.out, show)
    else:
        process_scene(loader, args.scene, grid, args.out, show)

    print("\nDone.")


if __name__ == "__main__":
    main()
