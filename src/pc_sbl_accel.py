"""
Accelerated PC-SBL via non-overlapping submap partitioning.

Motivation
----------
Full PC-SBL on an 80×80 grid (N=6400) solves an N×N sparse linear system at
each EM iteration.  Sparse direct solvers scale roughly as O(N^1.5).

Submap partitioning splits the grid into (sg × sg) non-overlapping submaps of
size (G/sg × G/sg) each.  Each submap is an independent PCSBL solve:

  Total cost ≈ sg² × (N/sg²)^1.5  =  N^1.5 / sg

For sg=2  (2×2 = 4 submaps):  ~2× speedup
For sg=4  (4×4 = 16 submaps): ~4× speedup

Trade-off: cells at submap boundaries lose cross-boundary β-coupling →
slight NMSE increase at borders.  Interior cells are unaffected.

Usage
-----
    from src.pc_sbl_accel import PCSBLAccel

    accel = PCSBLAccel(
        submap_grid=2,          # 2×2 partition
        beta=1.0,
        max_iter=150,
        tol=2e-3,
        gamma_fixed=30.0,
        alpha_damping=0.3,
        hits_per_bin=3,
        grid_size_sbl=80,
        cell_size_sbl=0.5,
        free_weight=0.5,
        verbose=False,
    )
    result = accel.run(points_xy, ego_xy=np.zeros(2))
    # result["prob_map"]  → (80,80) stitched occupancy map
    # result["elapsed_s"] → total wall time
    # result["submap_times"] → list of per-submap solve times
"""

import time
import numpy as np

from .pc_sbl import PCSBL


class PCSBLAccel:
    """
    Accelerated PC-SBL: partition grid into sg×sg submaps, solve each
    independently with standard PCSBL, stitch results into full grid.

    Parameters
    ----------
    submap_grid : int
        Number of submaps per spatial dimension (sg).
        sg=1 → identical to plain PCSBL (baseline).
        sg=2 → 2×2 = 4 submaps.
        sg=4 → 4×4 = 16 submaps.
    **pcsbl_kwargs
        All keyword arguments forwarded to PCSBL (beta, max_iter, tol, etc.).
        grid_size_sbl and cell_size_sbl refer to the *full* grid; each submap
        gets grid_size_sbl // submap_grid cells per side.
    """

    def __init__(self, submap_grid: int = 2, **pcsbl_kwargs):
        self.sg = submap_grid
        self.pcsbl_kwargs = pcsbl_kwargs

    def run(self,
            points_xy: np.ndarray,
            ego_xy: np.ndarray | None = None) -> dict:
        """
        Run accelerated PC-SBL on a single LiDAR scan.

        Args:
            points_xy : (M, 2) BEV (x, y) in ego frame.
            ego_xy    : (2,)  ego position; None = origin.

        Returns dict with:
            prob_map     : (G, G) stitched occupancy map
            elapsed_s    : total wall time (seconds)
            submap_times : list of per-submap elapsed times
            n_submaps    : number of submaps (sg²)
            submap_iters : list of EM iteration counts per submap
        """
        if ego_xy is None:
            ego_xy = np.zeros(2)

        t0 = time.perf_counter()
        sg = self.sg
        G = self.pcsbl_kwargs.get('grid_size_sbl', 80)
        cs = self.pcsbl_kwargs.get('cell_size_sbl', 0.5)

        if G % sg != 0:
            raise ValueError(f"grid_size_sbl={G} must be divisible by submap_grid={sg}")

        sub_G = G // sg          # cells per submap side
        full_half = G * cs / 2.0

        prob_full = np.zeros((G, G), dtype=np.float32)
        submap_times: list[float] = []
        submap_iters: list[int] = []
        submap_converged: list[bool] = []

        # Build per-submap PCSBL kwargs (override grid_size_sbl only)
        sub_kwargs = dict(self.pcsbl_kwargs)
        sub_kwargs['grid_size_sbl'] = sub_G

        for row_idx in range(sg):
            for col_idx in range(sg):
                # Submap center in world coordinates
                # col_idx=0 → leftmost column → cx = ego_x - full_half + sub_half
                cx = ego_xy[0] - full_half + (col_idx + 0.5) * sub_G * cs
                # row_idx=0 → topmost row (largest y)
                cy = ego_xy[1] + full_half - (row_idx + 0.5) * sub_G * cs

                sub_center = np.array([cx, cy])

                # Translate to submap-local frame (submap centered at origin)
                pts_sub = points_xy - sub_center
                ego_sub = ego_xy - sub_center

                sbl = PCSBL(**sub_kwargs)
                res = sbl.run(pts_sub, ego_xy=ego_sub)

                # Place submap prob_map in correct slice of full grid
                r0 = row_idx * sub_G
                c0 = col_idx * sub_G
                prob_full[r0:r0 + sub_G, c0:c0 + sub_G] = res['prob_map']

                submap_times.append(res['elapsed_s'])
                submap_iters.append(res['iters'])
                submap_converged.append(res['converged'])

        elapsed = time.perf_counter() - t0

        return {
            'prob_map': prob_full,
            'elapsed_s': elapsed,
            'submap_times': submap_times,
            'submap_iters': submap_iters,
            'submap_converged': submap_converged,
            'n_submaps': sg * sg,
        }
