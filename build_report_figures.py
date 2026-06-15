"""
Build remaining report figures (Phase 8):
  1. output/tradeoff_plot.png       — NMSE vs runtime: accel (sg) + sector (K)
  2. output/alpha_evolution.png     — α-weight evolution across EM iterations
  3. output/qualitative_panel.png   — T1 vs T2(β=0) vs T2(β=1) for scene-0916
  4. output/beta_sweep.png          — β ∈ {0,0.5,1,2,3} NMSE across 3 scenes

Run:
    ~/.pyenv/versions/3.11.9/bin/python3 build_report_figures.py
"""
import sys, os, time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.data_loader import NuScenesLoader
from src.preprocessor import transform_to_ego, height_filter, range_filter, project_bev
from src.pc_sbl import PCSBL
from src.pc_sbl_accel import PCSBLAccel
from src.pc_sbl_sector import PCSBLSector
from src.bayesian_ogm import run_single_scan
from src.occupancy_grid import OccupancyGrid
from src.metrics import compute_angular_nmse

DATA_ROOT   = "v1.0-mini"
GRID_SIZE   = 80
CELL_SIZE   = 0.5
BASE_KWARGS = dict(
    beta=1.0, max_iter=150, tol=2e-3, gamma_fixed=30.0,
    alpha_damping=0.3, hits_per_bin=3,
    grid_size_sbl=GRID_SIZE, cell_size_sbl=CELL_SIZE,
    free_weight=0.5, verbose=False,
)

loader = NuScenesLoader(DATA_ROOT)

# ── helpers ──────────────────────────────────────────────────────────────────

def load_scan(scene_name, scan_index=0):
    scene = loader.get_scene_by_name(scene_name)
    tokens = loader.get_lidar_tokens_for_scene(scene)
    sd_token = tokens[scan_index]
    pts_raw = loader.load_lidar_points(sd_token)
    cs_sens = loader.get_calibrated_sensor(sd_token)
    pts_ego = transform_to_ego(pts_raw, cs_sens)
    pts_f   = height_filter(pts_ego)
    pts_f   = range_filter(pts_f, GRID_SIZE * CELL_SIZE / 2.0)
    pts_xy  = project_bev(pts_f)
    return pts_xy, sd_token


# ════════════════════════════════════════════════════════════════════════════
# FIGURE 1 — Tradeoff: NMSE vs runtime (accel sg + sector K)
# ════════════════════════════════════════════════════════════════════════════
print("\n── Figure 1: tradeoff_plot ──")

TRADEOFF_SCENES = ["scene-0061", "scene-0916", "scene-1077"]

# Hardcoded accel results from run_accel_benchmark.py output
accel_data = {
    # sg: (mean_time_s, mean_nmse) across 3 scenes
    1:  (5.17, 0.0785),   # baseline
    2:  (2.27, 0.4317),
    4:  (0.35, 1.0953),
}

# Run sector benchmark on same 3 scenes
sector_ks = [1, 2, 4, 8]
sector_results = {}   # k -> (mean_time, mean_nmse)

for K in sector_ks:
    times, nmses = [], []
    for sname in TRADEOFF_SCENES:
        pts_xy, _ = load_scan(sname)
        t0 = time.perf_counter()
        sec = PCSBLSector(n_sectors=K, **BASE_KWARGS)
        res = sec.run(pts_xy, ego_xy=np.zeros(2))
        elapsed = time.perf_counter() - t0
        nmse_r = compute_angular_nmse(res['prob_map'], pts_xy, GRID_SIZE, CELL_SIZE)
        times.append(elapsed)
        nmses.append(nmse_r['nmse'])
        print(f"  K={K} {sname}: t={elapsed:.1f}s NMSE={nmse_r['nmse']:.4f}")
    sector_results[K] = (np.mean(times), np.mean(nmses))

fig, ax = plt.subplots(figsize=(7, 5))

# Accel (rectangular tile)
sg_vals  = sorted(accel_data.keys())
sg_times = [accel_data[sg][0] for sg in sg_vals]
sg_nmses = [accel_data[sg][1] for sg in sg_vals]
ax.plot(sg_times, sg_nmses, 'o--', color='tomato', linewidth=1.8,
        markersize=8, label='Rectangular tile (sg=1,2,4)', zorder=3)
for sg, t, n in zip(sg_vals, sg_times, sg_nmses):
    ax.annotate(f'sg={sg}', (t, n), textcoords='offset points',
                xytext=(6, 4), fontsize=8, color='tomato')

# Sector (angular)
K_vals  = sorted(sector_results.keys())
K_times = [sector_results[K][0] for K in K_vals]
K_nmses = [sector_results[K][1] for K in K_vals]
ax.plot(K_times, K_nmses, 's-', color='steelblue', linewidth=1.8,
        markersize=8, label='Angular sector (K=1,2,4,8)', zorder=3)
for K, t, n in zip(K_vals, K_times, K_nmses):
    ax.annotate(f'K={K}', (t, n), textcoords='offset points',
                xytext=(6, -10), fontsize=8, color='steelblue')

# Baseline reference line
ax.axhline(accel_data[1][1], color='gray', linestyle=':', linewidth=1,
           label=f'Baseline NMSE ({accel_data[1][1]:.3f})')

ax.set_xlabel('Wall time (s) — mean over 3 scenes', fontsize=11)
ax.set_ylabel('Angular NMSE (lower = better)', fontsize=11)
ax.set_title('Acceleration tradeoff: NMSE vs runtime\n'
             'Rectangular tiles sever rays → NMSE→1.0;  Angular sectors preserve accuracy',
             fontsize=10)
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('output/tradeoff_plot.png', dpi=150, bbox_inches='tight')
plt.close()
print("  → output/tradeoff_plot.png")


# ════════════════════════════════════════════════════════════════════════════
# FIGURE 2 — α-evolution across EM iterations
# ════════════════════════════════════════════════════════════════════════════
print("\n── Figure 2: alpha_evolution ──")

# Patch PCSBL to record α history
import src.pc_sbl as _pc_sbl_mod

class PCSBLAlphaTrack(PCSBL):
    """PCSBL subclass that records full α-vector at every iteration."""
    def run(self, points_xy, ego_xy=None):
        res = super().run(points_xy, ego_xy=ego_xy)
        return res

# Use PCSBL with verbose=False but capture conv_history; also need α snapshots.
# We do it by subclassing and overriding _em_loop (less invasive: re-run with
# small max_iter stepping to capture snapshots).

pts_0916, _ = load_scan("scene-0916")

SAMPLE_ITERS = [1, 5, 10, 20, 40, 60, 80, 100]

alpha_snaps_b0 = []
alpha_snaps_b1 = []

for n_iter in SAMPLE_ITERS:
    for beta, store in [(0.0, alpha_snaps_b0), (1.0, alpha_snaps_b1)]:
        kw = dict(BASE_KWARGS)
        kw['beta'] = beta
        kw['max_iter'] = n_iter
        kw['tol'] = 0.0     # force exact n_iter iterations (no early stop)
        sbl = PCSBL(**kw)
        res = sbl.run(pts_0916, ego_xy=np.zeros(2))
        # prob_map encodes α implicitly: prob = α/(α+β_noise)
        # We save the occupied-cell probabilities as a proxy for α distribution
        store.append(res['prob_map'].copy())

# Summarise α distribution: mean prob of top-10% cells vs iteration
def top10_mean(snaps):
    vals = []
    for pm in snaps:
        flat = pm.ravel()
        thresh = np.percentile(flat, 90)
        vals.append(flat[flat > thresh].mean())
    return np.array(vals)

# Also track mean of all occupied cells (P>0.5)
def occ_mean(snaps):
    vals = []
    for pm in snaps:
        mask = pm > 0.5
        vals.append(pm[mask].mean() if mask.any() else 0.0)
    return np.array(vals)

def occ_count(snaps):
    return np.array([(pm > 0.5).sum() for pm in snaps])

fig, axes = plt.subplots(1, 3, figsize=(14, 4))

# Panel A: occupied cell count vs iteration
ax = axes[0]
ax.plot(SAMPLE_ITERS, occ_count(alpha_snaps_b0), 'o-', color='darkorange',
        label='β=0 (decoupled)', linewidth=1.8)
ax.plot(SAMPLE_ITERS, occ_count(alpha_snaps_b1), 's-', color='steelblue',
        label='β=1 (coupled)', linewidth=1.8)
ax.set_xlabel('EM iteration', fontsize=10)
ax.set_ylabel('# cells with P > 0.5', fontsize=10)
ax.set_title('Occupied cell count\nscene-0916', fontsize=10)
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)

# Panel B: mean probability of top-10% cells
ax = axes[1]
ax.plot(SAMPLE_ITERS, top10_mean(alpha_snaps_b0), 'o-', color='darkorange',
        label='β=0', linewidth=1.8)
ax.plot(SAMPLE_ITERS, top10_mean(alpha_snaps_b1), 's-', color='steelblue',
        label='β=1', linewidth=1.8)
ax.set_xlabel('EM iteration', fontsize=10)
ax.set_ylabel('Mean P(occ) — top 10% cells', fontsize=10)
ax.set_title('α concentration (top-10% cells)\nscene-0916', fontsize=10)
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)

# Panel C: prob maps at convergence (β=0 vs β=1, last snapshot)
half = GRID_SIZE * CELL_SIZE / 2.0
ax = axes[2]
# Difference map: β=1 − β=0
diff = alpha_snaps_b1[-1].astype(float) - alpha_snaps_b0[-1].astype(float)
im = ax.imshow(diff, cmap='RdBu_r', vmin=-0.5, vmax=0.5, origin='upper',
               extent=[-half, half, -half, half])
plt.colorbar(im, ax=ax, label='P(β=1) − P(β=0)')
ax.set_title('Probability difference map\nβ=1 minus β=0 at iter=100', fontsize=10)
ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)')

fig.suptitle('α (occupancy weight) evolution — scene-0916', fontsize=12, y=1.01)
plt.tight_layout()
plt.savefig('output/alpha_evolution.png', dpi=150, bbox_inches='tight')
plt.close()
print("  → output/alpha_evolution.png")


# ════════════════════════════════════════════════════════════════════════════
# FIGURE 3 — Qualitative panel: T1 vs T2(β=0) vs T2(β=1) for scene-0916
# ════════════════════════════════════════════════════════════════════════════
print("\n── Figure 3: qualitative_panel ──")

scene_name = "scene-0916"
pts_xy, sd_token = load_scan(scene_name)

# Tier 1
grid_t1 = OccupancyGrid(grid_size=GRID_SIZE, cell_size=CELL_SIZE)
run_single_scan(loader, scene_name, grid_t1, scan_index=0, verbose=False)
prob_t1 = grid_t1.get_probability()

# T2 β=0
kw0 = dict(BASE_KWARGS, beta=0.0)
res0 = PCSBL(**kw0).run(pts_xy, ego_xy=np.zeros(2))
prob_b0 = res0['prob_map']

# T2 β=1 (already have from alpha_snaps, but re-run cleanly)
kw1 = dict(BASE_KWARGS, beta=1.0)
res1 = PCSBL(**kw1).run(pts_xy, ego_xy=np.zeros(2))
prob_b1 = res1['prob_map']

nmse_t1 = compute_angular_nmse(prob_t1, pts_xy, GRID_SIZE, CELL_SIZE)['nmse']
nmse_b0 = compute_angular_nmse(prob_b0, pts_xy, GRID_SIZE, CELL_SIZE)['nmse']
nmse_b1 = compute_angular_nmse(prob_b1, pts_xy, GRID_SIZE, CELL_SIZE)['nmse']

fig, axes = plt.subplots(1, 3, figsize=(15, 5))
half = GRID_SIZE * CELL_SIZE / 2.0

panels = [
    (prob_t1,  f"Tier 1 — Bayesian OGM\nNMSE={nmse_t1:.4f}",  'gray'),
    (prob_b0,  f"Tier 2 — PC-SBL β=0 (decoupled)\nNMSE={nmse_b0:.4f}  iters={res0['iters']}  cvg={res0['converged']}", 'gray'),
    (prob_b1,  f"Tier 2 — PC-SBL β=1 (coupled)\nNMSE={nmse_b1:.4f}  iters={res1['iters']}  cvg={res1['converged']}", 'gray'),
]

for ax, (pm, title, cmap) in zip(axes, panels):
    ax.imshow(pm, cmap='gray', vmin=0, vmax=1, origin='upper',
              extent=[-half, half, -half, half])
    # Overlay occupied cells
    occ_r, occ_c = np.where(pm > 0.5)
    occ_x = -half + (occ_c + 0.5) * CELL_SIZE
    occ_y =  half - (occ_r + 0.5) * CELL_SIZE
    ax.scatter(occ_x, occ_y, c='cyan', s=2, alpha=0.7, zorder=2)
    ax.plot(0, 0, 'r*', markersize=10, zorder=3, label='ego')
    ax.set_xlim(-half, half); ax.set_ylim(-half, half)
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=9)
    ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)')

fig.suptitle(f'Qualitative occupancy maps — {scene_name}\n'
             f'Cyan = P(occ) > 0.5', fontsize=11)
plt.tight_layout()
plt.savefig('output/qualitative_panel.png', dpi=150, bbox_inches='tight')
plt.close()
print("  → output/qualitative_panel.png")


# ════════════════════════════════════════════════════════════════════════════
# FIGURE 4 — β-sweep: NMSE vs β for 3 scenes
# ════════════════════════════════════════════════════════════════════════════
print("\n── Figure 4: beta_sweep ──")

BETA_SCENES  = ["scene-0061", "scene-0916", "scene-1077"]
BETA_VALUES  = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]
COLORS       = ['steelblue', 'darkorange', 'forestgreen']

fig, axes = plt.subplots(1, 2, figsize=(12, 5))

ax_nmse  = axes[0]
ax_iters = axes[1]

for sname, color in zip(BETA_SCENES, COLORS):
    pts_xy, _ = load_scan(sname)
    nmse_vals  = []
    iter_vals  = []
    for beta in BETA_VALUES:
        kw = dict(BASE_KWARGS, beta=beta)
        res = PCSBL(**kw).run(pts_xy, ego_xy=np.zeros(2))
        nmse_r = compute_angular_nmse(res['prob_map'], pts_xy, GRID_SIZE, CELL_SIZE)
        nmse_vals.append(nmse_r['nmse'])
        iter_vals.append(res['iters'])
        print(f"  β={beta} {sname}: NMSE={nmse_r['nmse']:.4f}  iters={res['iters']}")
    ax_nmse.plot(BETA_VALUES, nmse_vals,  'o-', color=color, linewidth=1.8,
                 markersize=7, label=sname)
    ax_iters.plot(BETA_VALUES, iter_vals, 'o-', color=color, linewidth=1.8,
                  markersize=7, label=sname)

ax_nmse.set_xlabel('β (coupling strength)', fontsize=11)
ax_nmse.set_ylabel('Angular NMSE (lower = better)', fontsize=11)
ax_nmse.set_title('β-sweep: NMSE vs coupling strength', fontsize=11)
ax_nmse.legend(fontsize=9)
ax_nmse.grid(True, alpha=0.3)
ax_nmse.axvline(1.0, color='gray', linestyle='--', linewidth=1, alpha=0.7, label='β=1 (optimal)')

ax_iters.set_xlabel('β (coupling strength)', fontsize=11)
ax_iters.set_ylabel('EM iterations to converge', fontsize=11)
ax_iters.set_title('β-sweep: convergence speed', fontsize=11)
ax_iters.legend(fontsize=9)
ax_iters.grid(True, alpha=0.3)
ax_iters.axvline(1.0, color='gray', linestyle='--', linewidth=1, alpha=0.7)
ax_iters.axhline(150, color='red', linestyle=':', linewidth=1, alpha=0.6, label='max_iter limit')
ax_iters.legend(fontsize=9)

fig.suptitle('Effect of β coupling on accuracy and convergence\n'
             'β=0 = decoupled SBL;  β=1 = full PC-SBL coupling', fontsize=11)
plt.tight_layout()
plt.savefig('output/beta_sweep.png', dpi=150, bbox_inches='tight')
plt.close()
print("  → output/beta_sweep.png")

print("\n✓ All four report figures written to output/")
