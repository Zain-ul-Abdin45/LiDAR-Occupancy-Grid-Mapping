"""
Cross-dataset comparison figure — nuScenes vs KITTI 3D Object vs KITTI Odometry.

This is the "how does it generalize" chart for the presentation/report: it did not
exist before (run_kitti_benchmark.py only printed a nuScenes-vs-KITTI table to
stdout — never saved). This script reads the KITTI mean rows straight from the
tables written by run_kitti_benchmark.py / run_kitti_odometry_benchmark.py, so it
always reflects the latest run of run_full_version.sh. nuScenes numbers are the
documented Phase 7/9 means (10-scene aggregate, from README.md) since main.py
--all-scenes does not currently emit an aggregate summary row of its own.

Outputs:
  output/cross_dataset_comparison.png
  output/cross_dataset_comparison.txt

Run:
    ~/.pyenv/versions/3.11.9/bin/python3 build_cross_dataset_comparison.py
"""
import os
import re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.chdir(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = "output"

# ── nuScenes reference numbers (README.md — Phase 7 single-scan, Phase 9 multi-frame) ──
NUSCENES = {
    "T1 NMSE (single)": 0.1064,
    "T2 β=1 NMSE (single)": 0.1131,
    "T1 IoBB (single)": 0.102,
    "T2 β=1 IoBB (single)": 0.023,
    "T1 IoBB (multi w=2)": 0.144,
    "T2 β=1 IoBB (multi w=2)": 0.056,
}


def _parse_kitti_table(path):
    """Pull the MEAN row from output/kitti_table.txt."""
    with open(path) as f:
        lines = f.readlines()
    mean_line = next(l for l in lines if l.strip().startswith("MEAN"))
    vals = mean_line.split()[1:]  # skip "MEAN"
    # columns: t1_iobb t1_nmse t2b0_nmse t2b1_iobb t2b1_nmse t2b1_prec b0_iters b1_iters
    return {
        "T1 IoBB (single)": float(vals[0]),
        "T1 NMSE (single)": float(vals[1]),
        "T2 β=0 NMSE (single)": float(vals[2]),
        "T2 β=1 IoBB (single)": float(vals[3]),
        "T2 β=1 NMSE (single)": float(vals[4]),
    }


def _parse_odo_table(path):
    """Pull the MEAN row from output/kitti_odo_table_<seq>.txt."""
    with open(path) as f:
        lines = f.readlines()
    mean_line = next(l for l in lines if l.strip().startswith("MEAN"))
    vals = mean_line.split()[1:]
    # columns: T1s T1m T2s T2s_iters T2m T2m_iters
    return {
        "T1 NMSE (single)": float(vals[0]),
        "T1 NMSE (multi w=2)": float(vals[1]),
        "T2 β=1 NMSE (single)": float(vals[2]),
        "T2 β=1 NMSE (multi w=2)": float(vals[4]),
    }


def _find_latest(pattern):
    import glob
    matches = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not matches:
        return None
    return matches[-1]


kitti_path = _find_latest(os.path.join(OUT_DIR, "kitti_table.txt"))
odo_path = _find_latest(os.path.join(OUT_DIR, "kitti_odo_table_*.txt"))

if kitti_path is None or odo_path is None:
    raise SystemExit(
        "Missing kitti_table.txt / kitti_odo_table_*.txt in output/ — "
        "run run_kitti_benchmark.py and run_kitti_odometry_benchmark.py first."
    )

kitti = _parse_kitti_table(kitti_path)
odo = _parse_odo_table(odo_path)

print(f"Using {kitti_path} and {odo_path}")

# ── Figure 1: NMSE across datasets (single-scan, T1 vs T2 β=1) ──
metrics_nmse = ["T1 NMSE (single)", "T2 β=1 NMSE (single)"]
datasets_nmse = {
    "nuScenes (10 scenes)": [NUSCENES[m] for m in metrics_nmse],
    "KITTI 3D Object (50 fr.)": [kitti[m] for m in metrics_nmse],
    "KITTI Odometry (50 fr.)": [odo[m] for m in metrics_nmse],
}

# ── Figure 2: IoBB single vs multi-frame (nuScenes + KITTI 3D only — no boxes on Odometry) ──
metrics_iobb = ["T1 IoBB (single)", "T2 β=1 IoBB (single)"]
datasets_iobb = {
    "nuScenes (10 scenes)": [NUSCENES[m] for m in metrics_iobb],
    "KITTI 3D Object (50 fr.)": [kitti[m] for m in metrics_iobb],
}

fig, axes = plt.subplots(1, 2, figsize=(13, 5))

# --- NMSE panel ---
ax = axes[0]
x = range(len(metrics_nmse))
width = 0.25
for i, (name, vals) in enumerate(datasets_nmse.items()):
    ax.bar([xi + i * width for xi in x], vals, width=width, label=name)
ax.set_xticks([xi + width for xi in x])
ax.set_xticklabels(metrics_nmse, rotation=10)
ax.set_ylabel("NMSE (lower is better)")
ax.set_title("Cross-dataset generalization — NMSE")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)

# --- IoBB panel ---
ax = axes[1]
x = range(len(metrics_iobb))
for i, (name, vals) in enumerate(datasets_iobb.items()):
    ax.bar([xi + i * width for xi in x], vals, width=width, label=name)
ax.set_xticks([xi + 0.5 * width for xi in x])
ax.set_xticklabels(metrics_iobb, rotation=10)
ax.set_ylabel("IoBB (higher is better)")
ax.set_title("Cross-dataset generalization — IoBB\n(KITTI Odometry has no box annotations)")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)

fig.tight_layout()
out_png = os.path.join(OUT_DIR, "cross_dataset_comparison.png")
fig.savefig(out_png, dpi=150)
plt.close(fig)
print(f"→ {out_png}")

# ── Table ──
out_txt = os.path.join(OUT_DIR, "cross_dataset_comparison.txt")
with open(out_txt, "w") as f:
    f.write("Cross-dataset comparison — nuScenes vs KITTI 3D Object vs KITTI Odometry\n")
    f.write("=" * 78 + "\n\n")
    f.write(f"{'Metric':<28}{'nuScenes':>14}{'KITTI 3D':>14}{'KITTI Odo':>14}\n")
    for m in ["T1 NMSE (single)", "T2 β=1 NMSE (single)"]:
        f.write(f"{m:<28}{NUSCENES[m]:>14.4f}{kitti[m]:>14.4f}{odo[m]:>14.4f}\n")
    f.write(f"{'T1 NMSE (multi w=2)':<28}{'—':>14}{'—':>14}{odo['T1 NMSE (multi w=2)']:>14.4f}\n")
    f.write(f"{'T2 β=1 NMSE (multi w=2)':<28}{'—':>14}{'—':>14}{odo['T2 β=1 NMSE (multi w=2)']:>14.4f}\n")
    f.write(f"{'T1 IoBB (single)':<28}{NUSCENES['T1 IoBB (single)']:>14.4f}{kitti['T1 IoBB (single)']:>14.4f}{'n/a':>14}\n")
    f.write(f"{'T2 β=1 IoBB (single)':<28}{NUSCENES['T2 β=1 IoBB (single)']:>14.4f}{kitti['T2 β=1 IoBB (single)']:>14.4f}{'n/a':>14}\n")
    f.write(f"{'T1 IoBB (multi w=2)':<28}{NUSCENES['T1 IoBB (multi w=2)']:>14.4f}{'—':>14}{'—':>14}\n")
    f.write(f"{'T2 β=1 IoBB (multi w=2)':<28}{NUSCENES['T2 β=1 IoBB (multi w=2)']:>14.4f}{'—':>14}{'—':>14}\n")
print(f"→ {out_txt}")
