"""
Visualizes the occupancy grid as a Bird's Eye View heatmap.
"""

import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from .occupancy_grid import OccupancyGrid


def plot_grid(
    grid: OccupancyGrid,
    title: str = "Occupancy Grid",
    save_path: str | None = None,
    show: bool = True,
):
    """
    Plot the occupancy probability grid as a grayscale BEV image.

    Dark = occupied (P → 1), Light = free (P → 0), Mid-grey = unknown (P = 0.5).
    A red cross marks the ego vehicle position.

    Args:
        grid:      OccupancyGrid to visualize
        title:     Figure title
        save_path: If provided, save PNG to this path
        show:      If True, call plt.show()
    """
    prob = grid.get_probability()

    fig, ax = plt.subplots(figsize=(7, 7))

    # imshow: row 0 is top of image; flip so forward (small row) is up
    im = ax.imshow(
        prob,
        cmap="gray_r",     # dark = occupied
        vmin=0.0,
        vmax=1.0,
        origin="upper",
        extent=[-20, 20, -20, 20],  # x and y in metres
    )

    # Ego vehicle marker
    ax.plot(0, 0, "r+", markersize=12, markeredgewidth=2, label="Ego vehicle")

    # Axes labels in world coordinates
    ax.set_xlabel("y (left) →  metres")
    ax.set_ylabel("x (forward) →  metres")
    ax.set_title(title)

    plt.colorbar(im, ax=ax, label="P(occupied)", fraction=0.046, pad=0.04)
    ax.legend(loc="upper right", fontsize=9)

    plt.tight_layout()

    if save_path is not None:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Saved → {save_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def plot_probability_histogram(grid: OccupancyGrid, title: str = "Cell Probability Distribution"):
    """Quick diagnostic: show how many cells are near 0, 0.5, or 1."""
    prob = grid.get_probability().ravel()
    plt.figure(figsize=(6, 3))
    plt.hist(prob, bins=50, color="steelblue", edgecolor="white")
    plt.xlabel("P(occupied)")
    plt.ylabel("Cell count")
    plt.title(title)
    plt.tight_layout()
    plt.show()
