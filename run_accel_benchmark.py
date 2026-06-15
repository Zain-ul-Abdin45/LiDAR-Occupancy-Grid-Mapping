"""
Accelerated PC-SBL benchmark: runtime vs NMSE for submap partitioning.

Tests submap_grid ∈ {1, 2, 4} on 3 representative scenes.
Generates output/accel_benchmark.png with:
  - Left:  Runtime vs submap_grid (bar chart per scene)
  - Right: NMSE vs submap_grid (line chart per scene)

Run:
    ~/.pyenv/versions/3.11.9/bin/python3 run_accel_benchmark.py
"""
import sys, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.data_loader import NuScenesLoader
from src.preprocessor import transform_to_ego, height_filter, range_filter, project_bev
from src.pc_sbl_accel import PCSBLAccel
from src.metrics import compute_angular_nmse

# ── Config ──────────────────────────────────────────────────────────────────
DATA_ROOT    = "v1.0-mini"
SCENES       = ["scene-0061", "scene-0916", "scene-1077"]
SUBMAP_GRIDS = [1, 2, 4]           # partition sizes to benchmark
GRID_SIZE    = 80
CELL_SIZE    = 0.5
THRESHOLD    = 0.5

# Phase 5 optimal PCSBL parameters
PCSBL_KWARGS = dict(
    beta          = 1.0,
    max_iter      = 150,
    tol           = 2e-3,
    gamma_fixed   = 30.0,
    alpha_damping = 0.3,
    hits_per_bin  = 3,
    grid_size_sbl = GRID_SIZE,
    cell_size_sbl = CELL_SIZE,
    free_weight   = 0.5,
    verbose       = False,
)

# ── Data loading ─────────────────────────────────────────────────────────────
loader = NuScenesLoader(DATA_ROOT)

def load_scene_points(scene_name: str):
    scene  = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)
    sd_token = lidar_tokens[0]
    pts_raw  = loader.load_lidar_points(sd_token)
    cs_sens  = loader.get_calibrated_sensor(sd_token)
    pts_ego  = transform_to_ego(pts_raw, cs_sens)
    pts_f    = height_filter(pts_ego)
    pts_f    = range_filter(pts_f, GRID_SIZE * CELL_SIZE / 2.0)
    pts_xy   = project_bev(pts_f)
    return pts_xy

# ── Benchmark ────────────────────────────────────────────────────────────────
# results[scene_name][sg] = {"elapsed_s": float, "nmse": float}
results: dict[str, dict[int, dict]] = {}

for scene_name in SCENES:
    print(f"\n{'='*60}")
    print(f"Scene: {scene_name}")
    pts_xy = load_scene_points(scene_name)
    ego_xy = np.zeros(2)
    results[scene_name] = {}

    for sg in SUBMAP_GRIDS:
        print(f"  submap_grid={sg} ({sg*sg} submaps) ...", flush=True)
        accel = PCSBLAccel(submap_grid=sg, **PCSBL_KWARGS)
        res   = accel.run(pts_xy, ego_xy=ego_xy)

        nmse_res = compute_angular_nmse(
            res['prob_map'], pts_xy,
            grid_size=GRID_SIZE,
            cell_size=CELL_SIZE,
            ego_xy=ego_xy,
            threshold=THRESHOLD,
        )

        elapsed = res['elapsed_s']
        nmse    = nmse_res['nmse']
        n_sub   = res['n_submaps']
        iters   = res['submap_iters']
        cvg     = res['submap_converged']

        print(f"    elapsed={elapsed:.1f}s  NMSE={nmse:.4f}  "
              f"iters={iters}  converged={cvg}")

        results[scene_name][sg] = {
            "elapsed_s": elapsed,
            "nmse": nmse,
            "n_submaps": n_sub,
        }

# ── Print summary table ───────────────────────────────────────────────────────
print("\n\n" + "="*70)
print(f"{'Scene':<14}", end="")
for sg in SUBMAP_GRIDS:
    print(f"  sg={sg} time(s)  sg={sg} NMSE", end="")
print()
print("-"*70)

for scene_name in SCENES:
    print(f"{scene_name:<14}", end="")
    sg1 = results[scene_name][1]
    for sg in SUBMAP_GRIDS:
        r = results[scene_name][sg]
        speedup = sg1['elapsed_s'] / max(r['elapsed_s'], 0.01)
        print(f"  {r['elapsed_s']:>9.1f}   {r['nmse']:>8.4f}", end="")
    print()

print("\nSpeedup vs sg=1:")
for scene_name in SCENES:
    sg1_t = results[scene_name][1]['elapsed_s']
    print(f"  {scene_name}: ", end="")
    for sg in SUBMAP_GRIDS:
        t = results[scene_name][sg]['elapsed_s']
        print(f"sg={sg}: {sg1_t/max(t,0.01):.2f}×  ", end="")
    print()

# ── Plot ─────────────────────────────────────────────────────────────────────
fig, (ax_time, ax_nmse) = plt.subplots(1, 2, figsize=(13, 5))

colors = ['#1f77b4', '#ff7f0e', '#2ca02c']
markers = ['o', 's', '^']

# Left: elapsed time per scene
x = np.arange(len(SUBMAP_GRIDS))
width = 0.25
for i, (scene_name, color) in enumerate(zip(SCENES, colors)):
    times = [results[scene_name][sg]['elapsed_s'] for sg in SUBMAP_GRIDS]
    bars  = ax_time.bar(x + i * width, times, width, label=scene_name,
                        color=color, alpha=0.8, edgecolor='k', linewidth=0.5)
    for bar, t in zip(bars, times):
        ax_time.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                     f"{t:.0f}s", ha='center', va='bottom', fontsize=7)

ax_time.set_xticks(x + width)
ax_time.set_xticklabels([f"sg={sg}\n({sg*sg} submaps)" for sg in SUBMAP_GRIDS])
ax_time.set_xlabel("Submap partition")
ax_time.set_ylabel("Wall time (seconds)")
ax_time.set_title("Runtime vs Partition Size")
ax_time.legend(fontsize=9)
ax_time.grid(axis='y', alpha=0.3)

# Right: NMSE per scene
for scene_name, color, marker in zip(SCENES, colors, markers):
    nmses = [results[scene_name][sg]['nmse'] for sg in SUBMAP_GRIDS]
    label_parts = [f"sg={sg}: {n:.4f}" for sg, n in zip(SUBMAP_GRIDS, nmses)]
    ax_nmse.plot(SUBMAP_GRIDS, nmses, color=color, marker=marker,
                 linewidth=2, markersize=8, label=scene_name)
    for sg, nmse in zip(SUBMAP_GRIDS, nmses):
        ax_nmse.annotate(f"{nmse:.4f}", (sg, nmse),
                         textcoords="offset points", xytext=(5, 3),
                         fontsize=8, color=color)

ax_nmse.set_xticks(SUBMAP_GRIDS)
ax_nmse.set_xticklabels([f"sg={sg}\n({sg*sg} sub)" for sg in SUBMAP_GRIDS])
ax_nmse.set_xlabel("Submap partition")
ax_nmse.set_ylabel("Angular NMSE")
ax_nmse.set_title("NMSE vs Partition Size")
ax_nmse.legend(fontsize=9)
ax_nmse.grid(alpha=0.3)

fig.suptitle(
    "Accelerated PC-SBL: Submap Partitioning  "
    "(β=1, γ=30, fw=0.5, 80×80 @ 0.5m)",
    fontsize=12, fontweight='bold'
)
plt.tight_layout()

os.makedirs("output", exist_ok=True)
out_path = "output/accel_benchmark.png"
plt.savefig(out_path, dpi=150, bbox_inches='tight')
print(f"\nSaved → {out_path}")
