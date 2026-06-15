"""
Plot convergence trace (rel_Δμ vs iteration) for selected scenes.
Per improvement_v3 §4: confirms convergence behaviour for report.
"""
import sys, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from src.data_loader import NuScenesLoader
from src.pc_sbl import PCSBL
from src.preprocessor import transform_to_ego, height_filter, range_filter, project_bev

DATA_ROOT = "v1.0-mini"
SCENES = ["scene-0061", "scene-0916"]
BETAS = [0, 1]
GRID_SIZE = 80
CELL_SIZE = 0.5

loader = NuScenesLoader(DATA_ROOT)

fig, axes = plt.subplots(1, 2, figsize=(12, 5))

for ax, scene_name in zip(axes, SCENES):
    scene = loader.get_scene_by_name(scene_name)
    lidar_tokens = loader.get_lidar_tokens_for_scene(scene)
    sd_token = lidar_tokens[0]
    pts_raw = loader.load_lidar_points(sd_token)
    cs = loader.get_calibrated_sensor(sd_token)
    pts_ego = transform_to_ego(pts_raw, cs)
    pts_f = height_filter(pts_ego)
    pts_f = range_filter(pts_f, GRID_SIZE * CELL_SIZE / 2.0)
    points_xy = project_bev(pts_f)

    ego_xy = np.zeros(2)

    for beta in BETAS:
        sbl = PCSBL(
            beta=beta,
            max_iter=150,
            tol=2e-3,
            gamma_fixed=50.0,
            alpha_damping=0.3,
            hits_per_bin=3,
            grid_size_sbl=GRID_SIZE,
            cell_size_sbl=CELL_SIZE,
            free_weight=1.0,
            verbose=False,
        )
        result = sbl.run(points_xy, ego_xy=ego_xy)
        hist = result["conv_history"]
        label = f"β={beta} (iters={result['iters']}, converged={result['converged']})"
        ax.semilogy(range(1, len(hist) + 1), hist, label=label)

    ax.axhline(2e-3, color="gray", linestyle="--", linewidth=0.8, label="tol=2e-3")
    ax.set_xlabel("EM Iteration")
    ax.set_ylabel("rel Δμ")
    ax.set_title(scene_name)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

plt.tight_layout()
out_path = "output/convergence_trace.png"
plt.savefig(out_path, dpi=150)
print(f"Saved → {out_path}")
