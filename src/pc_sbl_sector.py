"""
Accelerated PC-SBL via angular-sector partitioning.

Why sectors, not rectangular tiles
-----------------------------------
In PC-SBL the measurement matrix C is built from radial LiDAR rays originating
at the ego.  Each row of C is either:
  - an occupied row: one nonzero at the terminal cell
  - a free row:      nonzeros at every cell along the Bresenham ray

Rectangular tile partitioning (pc_sbl_accel.py, sg≥2) *severs* these rays —
the terminal cell and the free cells belonging to the same ray end up in
different submaps, breaking both the free-space constraint and the β-coupling.
NMSE collapses to ~1.0 (no skill).

Angular sectors fix this: each sector is a wedge centred on the ego, so every
ray stays entirely within one sector.  Terminal + free cells for a given hit
are never split.  Only the thin seams *between* sectors lose cross-sector
β-coupling (a small fraction of boundary cells).

Implementation
--------------
For K equal angular sectors:
  1. Partition 360° into K wedges of width 2π/K.
  2. For each wedge, run PCSBL on the full G×G grid using *only* the LiDAR
     points in that angular range.  Free-row rays stay whole.
  3. Merge: element-wise maximum of the K prob_maps.

Note on speedup
---------------
This implementation solves K independent N×N systems (same grid size as the
baseline).  The A = γ·CᵀC + diag(ξ) matrix is sparser (K× fewer C rows), so
each individual SpSolve is faster, but the total cost is still dominated by the
N×N structural solve.

Full speedup would require a *polar-indexed* grid where each sector's cells are
contiguous, reducing the solve to (N/K) × (N/K) per sector.  That requires a
coordinate-system rewrite and is identified as future work.

This module establishes the *correct decomposition* and verifies accuracy
preservation; the runtime benchmark is therefore a decomposition-quality
experiment rather than a deployment speedup.

Usage
-----
    from src.pc_sbl_sector import PCSBLSector

    sbl = PCSBLSector(
        n_sectors=4,
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
    result = sbl.run(points_xy, ego_xy=np.zeros(2))
    # result["prob_map"]      → (80, 80) merged occupancy map
    # result["elapsed_s"]     → total wall time
    # result["sector_times"]  → per-sector elapsed times
"""

import time
import numpy as np

from .pc_sbl import PCSBL


class PCSBLSector:
    """
    Angular-sector PC-SBL: run one PCSBL per angular wedge, merge with max.

    Parameters
    ----------
    n_sectors : int
        Number of equal angular sectors K (4 or 8 recommended).
        K=1 → identical to plain PCSBL (baseline).
    **pcsbl_kwargs
        All keyword arguments forwarded to PCSBL.
    """

    def __init__(self, n_sectors: int = 4, **pcsbl_kwargs):
        self.K = n_sectors
        self.pcsbl_kwargs = pcsbl_kwargs

    def run(self,
            points_xy: np.ndarray,
            ego_xy: np.ndarray | None = None) -> dict:
        """
        Run angular-sector PC-SBL on a single LiDAR scan.

        Args:
            points_xy : (M, 2) BEV (x, y) in ego frame.
            ego_xy    : (2,) ego position; None = origin.

        Returns dict with:
            prob_map      : (G, G) merged occupancy map (element-wise max)
            elapsed_s     : total wall time
            sector_times  : per-sector elapsed times
            sector_iters  : EM iteration counts per sector
            sector_converged : convergence flags per sector
            n_sectors     : K
        """
        if ego_xy is None:
            ego_xy = np.zeros(2)

        t0 = time.perf_counter()
        K = self.K
        G = self.pcsbl_kwargs.get('grid_size_sbl', 80)

        # Precompute angles of each point relative to ego
        rel = points_xy - ego_xy
        angles = np.arctan2(rel[:, 1], rel[:, 0])   # in (-π, π)
        # Shift to [0, 2π)
        angles_pos = (angles + 2 * np.pi) % (2 * np.pi)

        sector_width = 2 * np.pi / K

        # Accumulated prob_map (element-wise max across sectors)
        prob_merged = np.zeros((G, G), dtype=np.float32)

        sector_times: list[float] = []
        sector_iters: list[int] = []
        sector_converged: list[bool] = []

        for k in range(K):
            lo = k * sector_width
            hi = lo + sector_width

            # Select points in this angular wedge [lo, hi)
            in_sector = (angles_pos >= lo) & (angles_pos < hi)
            pts_sector = points_xy[in_sector]

            if len(pts_sector) < 3:
                sector_times.append(0.0)
                sector_iters.append(0)
                sector_converged.append(False)
                continue

            sbl = PCSBL(**self.pcsbl_kwargs)
            res = sbl.run(pts_sector, ego_xy=ego_xy)

            # Merge: keep the maximum probability at each cell
            prob_merged = np.maximum(prob_merged, res['prob_map'])

            sector_times.append(res['elapsed_s'])
            sector_iters.append(res['iters'])
            sector_converged.append(res['converged'])

        elapsed = time.perf_counter() - t0

        return {
            'prob_map': prob_merged,
            'elapsed_s': elapsed,
            'sector_times': sector_times,
            'sector_iters': sector_iters,
            'sector_converged': sector_converged,
            'n_sectors': K,
        }
