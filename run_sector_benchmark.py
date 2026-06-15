"""
Angular-sector PC-SBL benchmark + precision metric sweep.

Tests:
  1. Angular-sector partitioning (K=1/2/4/8) — NMSE + runtime
  2. Precision metric for T1 vs T2(β=0) vs T2(β=1) across 3 scenes

Outputs:
  output/sector_benchmark.png   — NMSE + runtime vs K (sector sweep)
  output/precision_table.txt    — T1/β=0/β=1 precision comparison
  output/iters_table.txt        — iters-to-converge for β=0 vs β=1

Run:
    ~/.pyenv/versions/3.11.9/bin/python3 run_sector_benchmark.py
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
from src.pc_sbl import PCSBL
from src.pc_sbl_sector import PCSBLSector
from src.bayesian_ogm import run_single_scan
from src.occupancy_grid import OccupancyGrid
from src.metrics import compute_angular_nmse, compute_iobb, compute_precision

# ── Config ───────────────────────────────────────────────────────────────────
DATA_ROOT = "v1.0-mini"
GRID_SIZE = 80
CELL_SIZE = 0.5
THRESHOLD = 0.5
N_SECTORS = [1, 2, 4, 8]
SCENES    = ["scene-0061", "scene-0916", "scene-1077"]

# Phase 5 optimal config
PCSBL_KWARGS = dict(
    beta=1.0, max_iter=150, tol=2e-3, gamma_fixed=30.0,
    alpha_damping=0.3, hits_per_bin=3,
    grid_size_sbl=GRID_SIZE, cell_size_sbl=CELL_SIZE,
    free_weight=0.5, verbose=False,
)

loader = NuScenesLoader(DATA_ROOT)


def load_scene(scene_name):
    scene  = loader.get_scene_by_name(scene_name)
    tokens = loader.get_lidar_tokens_for_scene(scene)
    sd_tok = tokens[0]
    pts_raw = loader.load_lidar_points(sd_tok)
    cs_sens = loader.get_calibrated_sensor(sd_tok)
    pts_ego = transform_to_ego(pts_raw, cs_sens)
    pts_f   = height_filter(pts_ego)
    pts_f   = range_filter(pts_f, GRID_SIZE * CELL_SIZE / 2.0)
    pts_xy  = project_bev(pts_f)
    sample_token = loader.get_sample_token(sd_tok)
    ego_pose     = loader.get_ego_pose(sd_tok)
    origin_xy    = np.array(ego_pose["translation"][:2])
    annotations  = loader.get_annotations_for_sample(sample_token)
    return pts_xy, sample_token, origin_xy, annotations, sd_tok


# ── Part 1: Sector sweep (NMSE + runtime) ─────────────────────────────────────
print("=" * 60)
print("Part 1: Angular sector sweep")
print("=" * 60)

sector_results = {}   # [scene][K] = {nmse, elapsed}

for scene_name in SCENES:
    print(f"\n  {scene_name}")
    pts_xy, sample_token, origin_xy, annotations, sd_tok = load_scene(scene_name)
    ego_xy = np.zeros(2)
    sector_results[scene_name] = {}

    for K in N_SECTORS:
        print(f"    K={K} sectors ...", flush=True)
        sbl = PCSBLSector(n_sectors=K, **PCSBL_KWARGS)
        res = sbl.run(pts_xy, ego_xy=ego_xy)

        nmse = compute_angular_nmse(
            res['prob_map'], pts_xy, GRID_SIZE, CELL_SIZE, ego_xy=ego_xy,
            threshold=THRESHOLD
        )['nmse']

        print(f"      elapsed={res['elapsed_s']:.1f}s  NMSE={nmse:.4f}  "
              f"iters={res['sector_iters']}  converged={res['sector_converged']}")

        sector_results[scene_name][K] = {
            'elapsed_s': res['elapsed_s'],
            'nmse': nmse,
        }

# Print sector summary table
print("\n\nSector sweep summary:")
print(f"{'Scene':<14}", end="")
for K in N_SECTORS:
    print(f"  K={K} time  K={K} NMSE", end="")
print()
for scene_name in SCENES:
    print(f"{scene_name:<14}", end="")
    for K in N_SECTORS:
        r = sector_results[scene_name][K]
        print(f"  {r['elapsed_s']:>7.1f}s  {r['nmse']:>8.4f}", end="")
    print()

# ── Part 2: T1 / T2(β=0) / T2(β=1) — precision + IoBB + NMSE + iters ─────────
print("\n" + "=" * 60)
print("Part 2: T1 vs β=0 vs β=1 — NMSE / IoBB / Precision / Iters")
print("=" * 60)

# 10-scene sweep for iters-to-converge table
ALL_SCENES = [
    "scene-0061", "scene-0103", "scene-0553", "scene-0655",
    "scene-0757", "scene-0796", "scene-0916", "scene-1077",
    "scene-1094", "scene-1100",
]

print("\n{:<14} {:>6} {:>8} {:>8} {:>8} {:>8} {:>8} {:>8} {:>8} {:>8}".format(
    "Scene",
    "T1 NMSE", "T1 IoBB", "T1 Prec",
    "b0 NMSE", "b0 IoBB", "b0 Prec", "b0 iters",
    "b1 NMSE", "b1 iters",
))

rows = []
for scene_name in ALL_SCENES:
    print(f"\n  {scene_name} ...", flush=True)
    pts_xy, sample_token, origin_xy, annotations, sd_tok = load_scene(scene_name)
    ego_xy = np.zeros(2)

    # T1
    grid_t1 = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
    run_single_scan(loader, scene_name, grid_t1, scan_index=0, verbose=False)
    prob_t1 = grid_t1.get_probability()

    nmse_t1  = compute_angular_nmse(prob_t1, pts_xy, GRID_SIZE, CELL_SIZE,
                                    ego_xy=ego_xy, threshold=THRESHOLD)['nmse']
    iobb_t1  = compute_iobb(prob_t1, annotations, GRID_SIZE, CELL_SIZE,
                            origin_xy=origin_xy, threshold=THRESHOLD)['iobb_mean']
    prec_t1  = compute_precision(prob_t1, annotations, GRID_SIZE, CELL_SIZE,
                                 origin_xy=origin_xy, threshold=THRESHOLD)['precision']

    row = {"scene": scene_name,
           "nmse_t1": nmse_t1, "iobb_t1": iobb_t1, "prec_t1": prec_t1}

    # T2 β=0 and β=1
    for beta in [0.0, 1.0]:
        kw = dict(PCSBL_KWARGS, beta=beta)
        sbl = PCSBL(**kw)
        res = sbl.run(pts_xy, ego_xy=ego_xy)
        pm  = res['prob_map']

        nmse = compute_angular_nmse(pm, pts_xy, GRID_SIZE, CELL_SIZE,
                                    ego_xy=ego_xy, threshold=THRESHOLD)['nmse']
        iobb = compute_iobb(pm, annotations, GRID_SIZE, CELL_SIZE,
                            origin_xy=origin_xy, threshold=THRESHOLD)['iobb_mean']
        prec = compute_precision(pm, annotations, GRID_SIZE, CELL_SIZE,
                                 origin_xy=origin_xy, threshold=THRESHOLD)['precision']

        key = "b0" if beta == 0.0 else "b1"
        row[f"nmse_{key}"]  = nmse
        row[f"iobb_{key}"]  = iobb
        row[f"prec_{key}"]  = prec
        row[f"iters_{key}"] = res['iters']
        row[f"cvg_{key}"]   = res['converged']

    rows.append(row)
    print(f"    T1:    NMSE={row['nmse_t1']:.4f}  IoBB={row['iobb_t1']:.3f}  "
          f"Prec={row['prec_t1']:.3f}")
    print(f"    β=0:   NMSE={row['nmse_b0']:.4f}  IoBB={row['iobb_b0']:.3f}  "
          f"Prec={row['prec_b0']:.3f}  iters={row['iters_b0']}  cvg={row['cvg_b0']}")
    print(f"    β=1:   NMSE={row['nmse_b1']:.4f}  IoBB={row['iobb_b1']:.3f}  "
          f"Prec={row['prec_b1']:.3f}  iters={row['iters_b1']}  cvg={row['cvg_b1']}")

# ── Print aggregate table ─────────────────────────────────────────────────────
def fmt(v):
    if v != v:   return "  nan  "
    return f"{v:.4f}"

def fmt3(v):
    if v != v:   return " nan "
    return f"{v:.3f}"

print("\n\n" + "=" * 100)
print("FULL 10-SCENE TABLE (Phase 5 config: β=1, γ=30, fw=0.5)")
print("=" * 100)
hdr = (f"{'Scene':<14} | {'T1 NMSE':>8} {'T1 IoBB':>8} {'T1 Prec':>8} |"
       f" {'b0 NMSE':>8} {'b0 IoBB':>8} {'b0 Prec':>8} {'b0 its':>6} |"
       f" {'b1 NMSE':>8} {'b1 IoBB':>8} {'b1 Prec':>8} {'b1 its':>6} {'b1<T1':>6}")
print(hdr)
print("-" * len(hdr))

for r in rows:
    beat_t1 = "✅" if r['nmse_b1'] < r['nmse_t1'] else "✗ "
    prec_win = "✅" if r['prec_b1'] > r['prec_t1'] else "✗ "
    print(
        f"{r['scene']:<14} | "
        f"{r['nmse_t1']:>8.4f} {r['iobb_t1']:>8.3f} {r['prec_t1']:>8.3f} | "
        f"{r['nmse_b0']:>8.4f} {r['iobb_b0']:>8.3f} {r['prec_b0']:>8.3f} {r['iters_b0']:>6d} | "
        f"{r['nmse_b1']:>8.4f} {r['iobb_b1']:>8.3f} {r['prec_b1']:>8.3f} {r['iters_b1']:>6d} "
        f"{beat_t1:>6}"
    )

# Aggregates
def safe_mean(vals):
    v = [x for x in vals if x == x]  # filter nan
    return float(np.mean(v)) if v else float('nan')

m_nmse_t1  = safe_mean([r['nmse_t1']  for r in rows])
m_iobb_t1  = safe_mean([r['iobb_t1']  for r in rows])
m_prec_t1  = safe_mean([r['prec_t1']  for r in rows])
m_nmse_b0  = safe_mean([r['nmse_b0']  for r in rows])
m_iobb_b0  = safe_mean([r['iobb_b0']  for r in rows])
m_prec_b0  = safe_mean([r['prec_b0']  for r in rows])
m_nmse_b1  = safe_mean([r['nmse_b1']  for r in rows])
m_iobb_b1  = safe_mean([r['iobb_b1']  for r in rows])
m_prec_b1  = safe_mean([r['prec_b1']  for r in rows])
m_iters_b0 = safe_mean([r['iters_b0'] for r in rows])
m_iters_b1 = safe_mean([r['iters_b1'] for r in rows])

n_beat_nmse = sum(1 for r in rows if r['nmse_b1'] < r['nmse_t1'])
n_beat_prec = sum(1 for r in rows if r['prec_b1'] > r['prec_t1'])

print("-" * len(hdr))
print(
    f"{'mean':<14} | "
    f"{m_nmse_t1:>8.4f} {m_iobb_t1:>8.3f} {m_prec_t1:>8.3f} | "
    f"{m_nmse_b0:>8.4f} {m_iobb_b0:>8.3f} {m_prec_b0:>8.3f} {m_iters_b0:>6.0f} | "
    f"{m_nmse_b1:>8.4f} {m_iobb_b1:>8.3f} {m_prec_b1:>8.3f} {m_iters_b1:>6.0f} "
    f"{'':>6}"
)
print(f"\nβ=1 beats T1 on NMSE: {n_beat_nmse}/10")
print(f"β=1 beats T1 on Prec: {n_beat_prec}/10")
print(f"Mean iters-to-converge: β=0 = {m_iters_b0:.0f},  β=1 = {m_iters_b1:.0f}")

# ── Save tables ───────────────────────────────────────────────────────────────
os.makedirs("output", exist_ok=True)

with open("output/precision_table.txt", "w") as f:
    f.write("Phase 5 config: β=1, γ=30, fw=0.5, hits=3, tol=2e-3, 80×80 @ 0.5m\n\n")
    f.write(f"{'Scene':<14} | T1 NMSE  T1 IoBB  T1 Prec | b0 NMSE  b0 IoBB  b0 Prec | b1 NMSE  b1 IoBB  b1 Prec | b1<T1(NMSE)  b1>T1(Prec)\n")
    f.write("-" * 110 + "\n")
    for r in rows:
        beat_n = "✅" if r['nmse_b1'] < r['nmse_t1'] else "✗ "
        beat_p = "✅" if r['prec_b1'] > r['prec_t1'] else "✗ "
        f.write(f"{r['scene']:<14} | "
                f"{r['nmse_t1']:>7.4f}  {r['iobb_t1']:>7.3f}  {r['prec_t1']:>7.3f} | "
                f"{r['nmse_b0']:>7.4f}  {r['iobb_b0']:>7.3f}  {r['prec_b0']:>7.3f} | "
                f"{r['nmse_b1']:>7.4f}  {r['iobb_b1']:>7.3f}  {r['prec_b1']:>7.3f} | "
                f"  {beat_n}           {beat_p}\n")
    f.write("-" * 110 + "\n")
    f.write(f"{'mean':<14} | "
            f"{m_nmse_t1:>7.4f}  {m_iobb_t1:>7.3f}  {m_prec_t1:>7.3f} | "
            f"{m_nmse_b0:>7.4f}  {m_iobb_b0:>7.3f}  {m_prec_b0:>7.3f} | "
            f"{m_nmse_b1:>7.4f}  {m_iobb_b1:>7.3f}  {m_prec_b1:>7.3f}\n")
    f.write(f"\nβ=1 beats T1 on NMSE: {n_beat_nmse}/10\n")
    f.write(f"β=1 beats T1 on Prec: {n_beat_prec}/10\n")

with open("output/iters_table.txt", "w") as f:
    f.write("Iters-to-converge: β=0 vs β=1  (tol=2e-3, max_iter=150)\n\n")
    f.write(f"{'Scene':<14}  b0_iters  b0_cvg  b1_iters  b1_cvg  speedup\n")
    f.write("-" * 60 + "\n")
    for r in rows:
        sp = f"{r['iters_b0']/r['iters_b1']:.2f}×" if r['iters_b1'] > 0 else "—"
        f.write(f"{r['scene']:<14}  {r['iters_b0']:>8d}  {str(r['cvg_b0']):>6}  "
                f"{r['iters_b1']:>8d}  {str(r['cvg_b1']):>6}  {sp:>7}\n")
    f.write("-" * 60 + "\n")
    f.write(f"{'mean':<14}  {m_iters_b0:>8.0f}           {m_iters_b1:>8.0f}\n")
    f.write(f"\nβ=1 converges {m_iters_b0/m_iters_b1:.1f}× faster than β=0 on average\n")

print(f"\nSaved → output/precision_table.txt")
print(f"Saved → output/iters_table.txt")

# ── Plot: sector sweep ─────────────────────────────────────────────────────────
fig, (ax_time, ax_nmse) = plt.subplots(1, 2, figsize=(13, 5))
colors  = ['#1f77b4', '#ff7f0e', '#2ca02c']
markers = ['o', 's', '^']

for scene_name, color, marker in zip(SCENES, colors, markers):
    times = [sector_results[scene_name][K]['elapsed_s'] for K in N_SECTORS]
    nmses = [sector_results[scene_name][K]['nmse']      for K in N_SECTORS]

    ax_time.plot(N_SECTORS, times, color=color, marker=marker,
                 linewidth=2, markersize=8, label=scene_name)
    ax_nmse.plot(N_SECTORS, nmses, color=color, marker=marker,
                 linewidth=2, markersize=8, label=scene_name)
    for K, nm in zip(N_SECTORS, nmses):
        ax_nmse.annotate(f"{nm:.4f}", (K, nm),
                         textcoords="offset points", xytext=(4, 3),
                         fontsize=8, color=color)

# Reference: K=1 NMSE (the target to preserve)
for scene_name, color in zip(SCENES, colors):
    ref = sector_results[scene_name][1]['nmse']
    ax_nmse.axhline(ref, color=color, linestyle=':', linewidth=0.8, alpha=0.5)

ax_time.set_xticks(N_SECTORS)
ax_time.set_xlabel("Number of angular sectors (K)")
ax_time.set_ylabel("Wall time (seconds)")
ax_time.set_title("Runtime vs Sectors")
ax_time.legend(fontsize=9)
ax_time.grid(alpha=0.3)

ax_nmse.set_xticks(N_SECTORS)
ax_nmse.set_xlabel("Number of angular sectors (K)")
ax_nmse.set_ylabel("Angular NMSE")
ax_nmse.set_title("NMSE vs Sectors\n(dotted = K=1 reference per scene)")
ax_nmse.legend(fontsize=9)
ax_nmse.grid(alpha=0.3)

fig.suptitle("Angular-Sector PC-SBL — Accuracy Preservation\n"
             "(β=1, γ=30, fw=0.5, 80×80 @ 0.5m)",
             fontsize=12, fontweight='bold')
plt.tight_layout()
plt.savefig("output/sector_benchmark.png", dpi=150, bbox_inches='tight')
print("Saved → output/sector_benchmark.png")
