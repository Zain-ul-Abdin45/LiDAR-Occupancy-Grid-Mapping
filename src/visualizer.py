"""
Visualizes the occupancy grid as a Bird's Eye View heatmap.
"""

import os
import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter
from .occupancy_grid import OccupancyGrid


def plot_grid(
    grid: OccupancyGrid,
    title: str = "Occupancy Grid",
    save_path: str | None = None,
    show: bool = True,
    smooth_sigma: float = 0.0,
    show_ego: bool = True,
):
    """
    Plot the occupancy probability grid as a grayscale BEV image.

    Dark = occupied (P → 1), Light = free (P → 0), Mid-grey = unknown (P = 0.5).

    Args:
        grid:         OccupancyGrid to visualize
        title:        Figure title
        save_path:    If provided, save PNG to this path
        show:         If True, call plt.show()
        smooth_sigma: Gaussian blur sigma in cells (0 = off). Try 0.8 to soften
                      LiDAR beam-ring gaps without losing obstacle edges.
        show_ego:     Draw a red cross at the grid-centre ego position.
    """
    prob = grid.get_probability()

    if smooth_sigma > 0.0:
        prob = gaussian_filter(prob, sigma=smooth_sigma)
        prob = np.clip(prob, 0.0, 1.0)

    half = grid.grid_size * grid.cell_size / 2  # metres to edge of grid
    extent = [-half, half, -half, half]

    fig, ax = plt.subplots(figsize=(7, 7))

    # imshow: row 0 is top of image; flip so forward (small row) is up
    im = ax.imshow(
        prob,
        cmap="gray_r",     # dark = occupied
        vmin=0.0,
        vmax=1.0,
        origin="upper",
        extent=extent,
    )

    if show_ego:
        ax.plot(0, 0, "r+", markersize=12, markeredgewidth=2, label="Ego vehicle")

    ax.set_xlabel("x (forward) →  metres")
    ax.set_ylabel("y (left) →  metres")
    ax.set_title(title)

    plt.colorbar(im, ax=ax, label="P(occupied)", fraction=0.046, pad=0.04)
    if show_ego:
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


def plot_bev_points(
    points_xy: np.ndarray,
    title: str = "LiDAR BEV",
    save_path: str | None = None,
    show: bool = True,
):
    """Plot bird's-eye-view points and optionally save the figure."""
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(points_xy[:, 0], points_xy[:, 1], s=0.5, c="k", alpha=0.4)
    ax.set_xlabel("x (forward) →  metres")
    ax.set_ylabel("y (left) →  metres")
    ax.set_title(title)
    ax.set_aspect("equal", adjustable="box")
    plt.tight_layout()

    if save_path is not None:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Saved → {save_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def plot_image(
    image_path: str,
    title: str = "Camera Image",
    save_path: str | None = None,
    show: bool = True,
):
    """Display a camera image and optionally save it."""
    image = plt.imread(image_path)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.imshow(image)
    ax.axis('off')
    ax.set_title(title)
    plt.tight_layout()

    if save_path is not None:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Saved → {save_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)
