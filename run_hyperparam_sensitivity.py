"""
PC-SBL hyperparameter sensitivity sweep — independent validation script.

Not wired into run_full_version.sh. The Phase 5 config (gamma=30,
free_weight=0.5) was chosen by hand-tuning on the same 10-scene set used
for reporting, and the report's Limitations section already discloses
that. This script exists to actually check how sensitive the headline
NMSE is to that choice, one parameter at a time, the same way the beta
sweep was done, on the same 3-scene subset run_accel_benchmark.py and
run_sector_benchmark.py already use.

Run standalone first and inspect the output before deciding whether to
fold this into run_full_version.sh:

    ~/.pyenv/versions/3.11.9/bin/python3 run_hyperparam_sensitivity.py

Outputs (default output_nuscenes/, pass --out output to write into the
tracked report output/ directory instead):
  hyperparam_sensitivity.png        - NMSE vs gamma and vs free_weight
  hyperparam_sensitivity_table.txt  - per-scene NMSE for every value tested
"""
import argparse
import sys, os
import time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.data_loader import NuScenesLoader
from src.preprocessor import transform_to_ego, height_filter, range_filter, project_bev
from src.pc_sbl import PCSBL
from src.metrics import compute_angular_nmse

# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_DATA_ROOT = "v1.0-mini"
GRID_SIZE = 80
CELL_SIZE = 0.5
THRESHOLD = 0.5
SCENES = ["scene-0061", "scene-0916", "scene-1077"]

# Phase 5 chosen values (held fixed while the *other* parameter is swept)
GAMMA_DEFAULT = 30.0
FREE_WEIGHT_DEFAULT = 0.5

GAMMA_SWEEP = [15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0]
FREE_WEIGHT_SWEEP = [0.0, 0.25, 0.5, 0.75, 1.0]

# Everything except gamma_fixed/free_weight stays at the Phase 5 config
BASE_KWARGS = dict(
    beta=1.0, max_iter=150, tol=2e-3,
    alpha_damping=0.3, hits_per_bin=3,
    grid_size_sbl=GRID_SIZE, cell_size_sbl=CELL_SIZE,
    verbose=False,
)


def parse_args():
    p = argparse.ArgumentParser(description="PC-SBL hyperparameter sensitivity sweep (nuScenes).")
    p.add_argument("--data-root", default=DEFAULT_DATA_ROOT)
    p.add_argument("--out", default=None,
                   help="Output directory. Defaults to output_nuscenes; pass "
                        "--out output to write into the tracked report output/.")
    return p.parse_args()


args = parse_args()
args.out = args.out if args.out is not None else "output_nuscenes"
os.makedirs(args.out, exist_ok=True)
loader = NuScenesLoader(args.data_root)


def load_scene(scene_name):
    scene = loader.get_scene_by_name(scene_name)
    sd_tok = loader.get_lidar_tokens_for_scene(scene)[0]
    pts_raw = loader.load_lidar_points(sd_tok)
    cs_sens = loader.get_calibrated_sensor(sd_tok)
    pts_ego = transform_to_ego(pts_raw, cs_sens)
    pts_f = height_filter(pts_ego)
    pts_f = range_filter(pts_f, GRID_SIZE * CELL_SIZE / 2.0)
    return project_bev(pts_f)


def run_one(pts_xy, ego_xy, gamma, free_weight):
    sbl = PCSBL(gamma_fixed=gamma, free_weight=free_weight, **BASE_KWARGS)
    res = sbl.run(pts_xy, ego_xy=ego_xy)
    nmse = compute_angular_nmse(
        res['prob_map'], pts_xy, GRID_SIZE, CELL_SIZE, ego_xy=ego_xy,
        threshold=THRESHOLD
    )['nmse']
    return nmse, res['iters'], res['converged']


# ── Load all three scenes once ──────────────────────────────────────────────
scene_points = {s: load_scene(s) for s in SCENES}
ego_xy = np.zeros(2)

# ── Sweep 1: gamma, free_weight held at Phase 5 default ────────────────────
print("=" * 60)
print(f"Gamma sweep (free_weight={FREE_WEIGHT_DEFAULT} fixed, beta=1)")
print("=" * 60)
gamma_results = {s: {} for s in SCENES}
for scene_name in SCENES:
    pts_xy = scene_points[scene_name]
    print(f"\n  {scene_name}")
    for gamma in GAMMA_SWEEP:
        t0 = time.time()
        nmse, iters, converged = run_one(pts_xy, ego_xy, gamma, FREE_WEIGHT_DEFAULT)
        elapsed = time.time() - t0
        print(f"    gamma={gamma:>5.1f}  NMSE={nmse:.4f}  iters={iters:>3d}  "
              f"converged={converged}  ({elapsed:.1f}s)")
        gamma_results[scene_name][gamma] = nmse

# ── Sweep 2: free_weight, gamma held at Phase 5 default ─────────────────────
print("\n" + "=" * 60)
print(f"Free-weight sweep (gamma={GAMMA_DEFAULT} fixed, beta=1)")
print("=" * 60)
fw_results = {s: {} for s in SCENES}
for scene_name in SCENES:
    pts_xy = scene_points[scene_name]
    print(f"\n  {scene_name}")
    for fw in FREE_WEIGHT_SWEEP:
        t0 = time.time()
        nmse, iters, converged = run_one(pts_xy, ego_xy, GAMMA_DEFAULT, fw)
        elapsed = time.time() - t0
        print(f"    free_weight={fw:.2f}  NMSE={nmse:.4f}  iters={iters:>3d}  "
              f"converged={converged}  ({elapsed:.1f}s)")
        fw_results[scene_name][fw] = nmse

# ── Save table ───────────────────────────────────────────────────────────────
table_path = os.path.join(args.out, "hyperparam_sensitivity_table.txt")
with open(table_path, "w", encoding="utf-8") as f:
    f.write(f"PC-SBL hyperparameter sensitivity ({', '.join(SCENES)}, beta=1)\n\n")
    f.write(f"Gamma sweep (free_weight={FREE_WEIGHT_DEFAULT} fixed)\n")
    f.write(f"{'gamma':>8}" + "".join(f"{s:>14}" for s in SCENES) + "\n")
    for gamma in GAMMA_SWEEP:
        f.write(f"{gamma:>8.1f}" + "".join(f"{gamma_results[s][gamma]:>14.4f}" for s in SCENES) + "\n")
    f.write(f"\nFree-weight sweep (gamma={GAMMA_DEFAULT} fixed)\n")
    f.write(f"{'free_wt':>8}" + "".join(f"{s:>14}" for s in SCENES) + "\n")
    for fw in FREE_WEIGHT_SWEEP:
        f.write(f"{fw:>8.2f}" + "".join(f"{fw_results[s][fw]:>14.4f}" for s in SCENES) + "\n")
print(f"\nSaved -> {table_path}")

# ── Save plot ────────────────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
for s in SCENES:
    ax1.plot(GAMMA_SWEEP, [gamma_results[s][g] for g in GAMMA_SWEEP], marker='o', label=s)
ax1.axvline(GAMMA_DEFAULT, color='gray', linestyle='--', linewidth=0.8, label=f'reported (γ={GAMMA_DEFAULT:.0f})')
ax1.set_xlabel("γ (fixed noise precision)")
ax1.set_ylabel("Angular NMSE (lower = better)")
ax1.set_title(f"NMSE vs γ  (free_weight={FREE_WEIGHT_DEFAULT} fixed)")
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)

for s in SCENES:
    ax2.plot(FREE_WEIGHT_SWEEP, [fw_results[s][w] for w in FREE_WEIGHT_SWEEP], marker='o', label=s)
ax2.axvline(FREE_WEIGHT_DEFAULT, color='gray', linestyle='--', linewidth=0.8, label=f'reported (fw={FREE_WEIGHT_DEFAULT})')
ax2.set_xlabel("free-row weight")
ax2.set_ylabel("Angular NMSE (lower = better)")
ax2.set_title(f"NMSE vs free-row weight  (γ={GAMMA_DEFAULT:.0f} fixed)")
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)

fig.suptitle("PC-SBL hyperparameter sensitivity (β=1)")
plt.tight_layout(rect=[0, 0, 1, 0.93])
plot_path = os.path.join(args.out, "hyperparam_sensitivity.png")
plt.savefig(plot_path, dpi=150, bbox_inches='tight')
print(f"Saved -> {plot_path}")
