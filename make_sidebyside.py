"""
Create side-by-side comparison plots for β=0 vs β=1.
Used for the "money plot" slide: scene-0916.
"""
import sys, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def sidebyside(scene, beta0_path, beta1_path, t1_path, out_path,
               nmse_b0, nmse_b1, nmse_t1):
    imgs = []
    titles = []
    for p, label, nmse in [
        (t1_path,    f"Tier 1 — Bayesian OGM\nNMSE={nmse_t1:.4f}", nmse_t1),
        (beta0_path, f"Tier 2 — PC-SBL  β=0\nNMSE={nmse_b0:.4f}", nmse_b0),
        (beta1_path, f"Tier 2 — PC-SBL  β=1\nNMSE={nmse_b1:.4f}", nmse_b1),
    ]:
        img = Image.open(p)
        imgs.append(np.array(img))
        titles.append(label)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    for ax, img, title in zip(axes, imgs, titles):
        ax.imshow(img)
        ax.set_title(title, fontsize=13)
        ax.axis("off")

    fig.suptitle(f"{scene} — β coupling effect  (γ=30, free_weight=0.5, hits=3, 0.5 m)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved → {out_path}")


sidebyside(
    scene="scene-0916",
    t1_path="output/scene-0916_scan00.png",
    beta0_path="output/scene-0916_pcsbl_b0.0_scan00.png",
    beta1_path="output/scene-0916_pcsbl_b1.0_scan00.png",
    out_path="output/scene-0916_sidebyside.png",
    nmse_t1=0.0482,
    nmse_b0=0.1443,
    nmse_b1=0.0707,
)
