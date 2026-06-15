"""
Tier 2: Pattern-Coupled Sparse Bayesian Learning (PC-SBL) for OGM.
Implements the EM algorithm from Önen 2024 (IEEE Sensors Journal 24(7))
with the canonical coupled prior from Fang et al. 2015 (IEEE TSP).

Model
-----
  Observation:  y = C·f + e,   e ~ N(0, (1/γ)·I_M)
  Sparse prior: f[n] ~ N(0, 1/α[n])
  PC prior:     ξ_n = α_n + β · Σ_{j ∈ L_n} α_j        (Fang 2015 §III)
                where L_n = 4-connected grid neighbours of cell n.
                ξ is the per-cell prior precision in the E-step.

  → coupling strength β ∈ [0, 1]:
      β = 0  →  decoupled SBL (ablation baseline)
      β = 1  →  full PC-SBL (Önen default)

C matrix — two-equation model (§2.3, improvement_v2.md)
---------------------------------------------------------
  For each occupied angular bin b:
    ROW TYPE A — Occupied:  C[b,   n_terminal] = 1,  y[b]   = 1
    ROW TYPE B — Free:      C[b+B, n] = 1 for ALL n on Bresenham ray, y[b+B] = 0

  Empty bins: zero rows, contribute nothing to CᵀC / Cᵀy.

  Effect:
    - Free rows create off-diagonal entries in CᵀC → A is no longer diagonal
      → β coupling propagates occupancy from terminal cells to neighbours.
    - Free rows actively push through-cells toward f=0.

E-step  (posterior over f)
---------
  ξ[n]  = α[n] + β · Σ_{j ∈ L_n} α[j]     (coupled prior precision)
  A     = γ·CᵀC + diag(ξ)                  (Fang 2015 Eq. 13)
  A·μ   = γ·Cᵀ·y
  diag(Φ) via Hutchinson estimator (K Rademacher probes).

M-step  (update hyperparameters)
---------
  v̂[n]  = μ[n]² + Φ_nn                     (posterior second moment)
  ω[n]  = v̂[n] + β · Σ_{j ∈ L_n} v̂[j]    (coupled second moment)
  α[n]  = (2a + 1) / (2b + ω[n])            (Fang 2015 Eq. 14)
           ≈ 1/ω[n] for a=b≈0
  γ     = M / (||y-Cμ||² + tr(CΦCᵀ))
  α-damping: α_new = (1-λ)·α_old + λ·α_update

Warm-start
----------
  Terminal-only bisection solve (α=1, γ_fixed) gives μ_0.
  α_init[n] = (2a+1) / (2b + v̂_bisection[n])
  This avoids cold-start over-pruning.

Probability mapping
-------------------
  P(occ)[n] = clip(μ[n], 0, 1)
"""

import time
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


# ──────────────────────────── Bresenham ─────────────────────────────────────

def _bresenham(r0: int, c0: int, r1: int, c1: int) -> list[tuple[int, int]]:
    cells = []
    dr, dc = abs(r1 - r0), abs(c1 - c0)
    sr = 1 if r1 > r0 else -1
    sc = 1 if c1 > c0 else -1
    err = dr - dc
    r, c = r0, c0
    while True:
        cells.append((r, c))
        if r == r1 and c == c1:
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc
            r += sr
        if e2 < dr:
            err += dr
            c += sc
    return cells


# ──────────────────────────── C matrix builder ──────────────────────────────

def build_C_matrix(points_xy: np.ndarray,
                   grid_size: int,
                   cell_size: float,
                   n_angles: int = 360,
                   ego_xy: np.ndarray | None = None,
                   hits_per_bin: int = 1,
                   free_weight: float = 1.0,
                   ) -> tuple[sp.csr_matrix, np.ndarray]:
    """
    Build two-equation C and measurement vector y.

    Returns C of shape (2*n_occ_bins, N) with:
      Rows 0..B-1:   occupied rows (terminal cell only, y=1)
      Rows B..2B-1:  free rows     (all Bresenham cells,  y=0)
    where B = number of occupied angular bins.

    The free rows create off-diagonal structure in CᵀC, enabling β coupling.
    """
    if ego_xy is None:
        ego_xy = np.zeros(2)

    half = grid_size * cell_size / 2.0
    N = grid_size * grid_size

    rel = points_xy - ego_xy
    angles_hit = np.arctan2(rel[:, 1], rel[:, 0])
    bin_size = 2 * np.pi / n_angles
    bins = ((angles_hit + np.pi) / bin_size).astype(int) % n_angles

    ranges = np.linalg.norm(rel, axis=1)

    # Collect up to hits_per_bin closest hits per angular bin
    bin_hits: dict[int, list[int]] = {}
    sorted_order = np.argsort(ranges)  # closest first
    for idx in sorted_order:
        b = int(bins[idx])
        hits = bin_hits.setdefault(b, [])
        if len(hits) < hits_per_bin:
            hits.append(idx)

    if not bin_hits:
        return sp.csr_matrix((0, N), dtype=np.float32), np.zeros(0, dtype=np.float32)

    # Ego pixel position (centre of grid)
    ego_col = int(np.clip((ego_xy[0] + half) / cell_size, 0, grid_size - 1))
    ego_row = int(np.clip((half - ego_xy[1]) / cell_size, 0, grid_size - 1))

    # Collect all (bin, hit_idx) pairs
    all_hits = [(b, idx) for b, idxs in bin_hits.items() for idx in idxs]
    B = len(all_hits)          # total occupied measurements
    total_rows = 2 * B
    y = np.zeros(total_rows, dtype=np.float32)

    rows_list, cols_list, data_list = [], [], []

    for row_idx, (b, idx) in enumerate(all_hits):
        px, py = points_xy[idx]
        hit_col = int(np.clip((px + half) / cell_size, 0, grid_size - 1))
        hit_row = int(np.clip((half - py) / cell_size, 0, grid_size - 1))
        n_term = hit_row * grid_size + hit_col

        # --- ROW A: occupied (terminal only, y=1) ---
        rows_list.append(row_idx)
        cols_list.append(n_term)
        data_list.append(1.0)
        y[row_idx] = 1.0

        # --- ROW B: free (all Bresenham cells, y=0); weight=free_weight ---
        free_row = row_idx + B
        ray_cells = _bresenham(ego_row, ego_col, hit_row, hit_col)
        for (rr, cc) in ray_cells:
            n = rr * grid_size + cc
            rows_list.append(free_row)
            cols_list.append(n)
            data_list.append(free_weight)
        # y[free_row] = 0 already

    data = np.array(data_list, dtype=np.float32)
    C = sp.coo_matrix((data, (rows_list, cols_list)),
                      shape=(total_rows, N)).tocsr()
    return C, y


# ──────────────────────────── Neighbour index builder ───────────────────────

def _build_neighbour_lists(grid_size: int) -> list[list[int]]:
    N = grid_size * grid_size
    neighbours = [[] for _ in range(N)]
    for r in range(grid_size):
        for c in range(grid_size):
            n = r * grid_size + c
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < grid_size and 0 <= nc < grid_size:
                    neighbours[n].append(nr * grid_size + nc)
    return neighbours


# ──────────────────────────── Hutchinson diagonal estimator ─────────────────

def _hutchinson_diag(A_sp: sp.csr_matrix, K: int = 16,
                     rng: np.random.Generator | None = None) -> np.ndarray:
    """Estimate diag(A⁻¹) via K Rademacher probes."""
    if rng is None:
        rng = np.random.default_rng(42)
    N = A_sp.shape[0]
    diag_est = np.zeros(N, dtype=np.float64)
    for _ in range(K):
        z = rng.choice([-1.0, 1.0], size=N).astype(np.float64)
        Az = spla.spsolve(A_sp, z)
        diag_est += z * Az
    return diag_est / K


# ──────────────────────────── PC-SBL main class ─────────────────────────────

class PCSBL:
    """
    Pattern-Coupled Sparse Bayesian Learning occupancy grid estimator.

    Parameters
    ----------
    beta          : float   Coupling strength. 0 = decoupled SBL, 1 = full PC-SBL.
    a, b          : float   Gamma prior shape/rate. Default 1e-4 (non-informative).
    gamma_init    : float   Initial noise precision.
    gamma_fixed   : float   If > 0, hold γ fixed (no update). Recommended during tuning.
    alpha_max     : float   Upper clamp on α (stops over-pruning).
    alpha_min     : float   Lower clamp on α.
    alpha_damping : float   λ ∈ [0,1]. α_new = (1-λ)·α_old + λ·α_update.
    max_iter      : int     Maximum EM iterations.
    tol           : float   Convergence on relative change in μ.
    n_angles      : int     Angular bins for C (default 360).
    hutchinson_K  : int     Rademacher probes for diag(Φ).
    grid_size_sbl : int     SBL grid cells per side. Default 160 at 0.5 m → 80m FOV.
    cell_size_sbl : float   SBL cell size in metres. Default 0.5 (matches Tier 1).
    verbose       : bool    Print convergence progress.
    """

    def __init__(self,
                 beta: float = 1.0,
                 a: float = 1e-4,
                 b: float = 1e-4,
                 gamma_init: float = 50.0,
                 gamma_fixed: float = 50.0,
                 alpha_max: float = 1e3,
                 alpha_min: float = 1e-3,
                 alpha_damping: float = 0.3,
                 max_iter: int = 100,
                 tol: float = 1e-3,
                 n_angles: int = 360,
                 hits_per_bin: int = 3,
                 hutchinson_K: int = 16,
                 grid_size_sbl: int = 80,
                 cell_size_sbl: float = 0.5,
                 free_weight: float = 1.0,
                 verbose: bool = True):
        self.beta = beta
        self.a = a
        self.b = b
        self.gamma_init = gamma_init
        self.gamma_fixed = gamma_fixed
        self.alpha_max = alpha_max
        self.alpha_min = alpha_min
        self.alpha_damping = alpha_damping
        self.max_iter = max_iter
        self.tol = tol
        self.n_angles = n_angles
        self.hits_per_bin = hits_per_bin
        self.K = hutchinson_K
        self.G = grid_size_sbl
        self.cs = cell_size_sbl
        self.free_weight = free_weight
        self.verbose = verbose
        self._rng = np.random.default_rng(42)

    def _warm_start_alpha(self, C_term: sp.csr_matrix, y_term: np.ndarray,
                          N: int) -> np.ndarray:
        """
        Terminal-only bisection solve (α=1, γ_fixed) → warm-start α.
        Avoids cold-start over-pruning by initialising α from data.
        """
        gamma_ws = float(self.gamma_fixed) if self.gamma_fixed > 0 else float(self.gamma_init)
        Ct = C_term.T.tocsr()
        CtC = (Ct @ C_term).tocsr()
        D = sp.eye(N, format="csr")
        A_ws = gamma_ws * CtC + D
        rhs_ws = gamma_ws * np.array(Ct @ y_term.astype(np.float64))
        mu_ws = spla.spsolve(A_ws, rhs_ws)

        mu_max = float(mu_ws.max())
        n_active = int((mu_ws > 0.1).sum())
        if self.verbose:
            print(f"  [warm-start] γ={gamma_ws} μ_max={mu_ws.max():.4f} "
                  f"cells>0.1: {n_active}/{N}")
            if n_active > 5:
                print("    → DATA OK: μ has structure.")
            else:
                print("    → DATA PROBLEM: μ is flat — check C/y assembly.")

        # α_init from M-step formula applied to bisection μ
        Phi_approx = np.full(N, 1.0 / max(gamma_ws, 1e-6))  # rough diagonal estimate
        v_hat_ws = mu_ws ** 2 + Phi_approx
        alpha_init = (2 * self.a + 1) / (2 * self.b + v_hat_ws)
        alpha_init = np.clip(alpha_init, self.alpha_min, self.alpha_max)
        return alpha_init

    def run(self,
            points_xy: np.ndarray,
            ego_xy: np.ndarray | None = None
            ) -> dict:
        """
        Run PC-SBL EM on a single LiDAR scan.

        Args:
            points_xy: (M, 2) BEV (x, y) in ego frame
            ego_xy:    (2,) ego position; None = origin

        Returns dict with: prob_map, mu, alpha, iters, converged, elapsed_s
        """
        if ego_xy is None:
            ego_xy = np.zeros(2)

        t0 = time.perf_counter()
        G, cs = self.G, self.cs
        N = G * G

        half = G * cs / 2.0
        mask = ((np.abs(points_xy[:, 0] - ego_xy[0]) <= half) &
                (np.abs(points_xy[:, 1] - ego_xy[1]) <= half))
        pts = points_xy[mask]

        if len(pts) < 5:
            prob_map = np.zeros((G, G), dtype=np.float32)
            return {"prob_map": prob_map, "mu": np.zeros(N), "alpha": np.ones(N),
                    "iters": 0, "converged": False, "elapsed_s": 0.0, "conv_history": []}

        # Build two-equation C (occupied + free rows)
        C, y = build_C_matrix(pts, G, cs, self.n_angles, ego_xy, self.hits_per_bin,
                              self.free_weight)
        M_obs = C.shape[0]   # 2*n_occ_bins
        Ct = C.T.tocsr()
        CtC = (Ct @ C).tocsr()
        Cty = np.array(Ct @ y, dtype=np.float64)
        y64 = y.astype(np.float64)

        # Column nnz of C for tr(CΦCᵀ) approximation in γ-update
        C_col_nnz = np.array((C != 0).sum(axis=0)).ravel().astype(np.float64)

        if self.verbose:
            occ_bins = int((y[:M_obs // 2] > 0.5).sum())
            print(f"  PC-SBL start | G={G} N={N} M={M_obs} "
                  f"nnz(C)={C.nnz} β={self.beta} "
                  f"occ_bins={occ_bins} "
                  f"γ={'fixed@' + str(self.gamma_fixed) if self.gamma_fixed > 0 else 'adaptive'}")

        # Build terminal-only C for warm-start (single nonzero per occupied bin)
        B = M_obs // 2
        C_term = C[:B]       # occupied rows only — each has exactly 1 nonzero
        y_term = y[:B]

        # Neighbour lookup
        nbrs = _build_neighbour_lists(G)

        # Warm-start α from terminal-only bisection
        alpha = self._warm_start_alpha(C_term, y_term, N)
        gamma = float(self.gamma_fixed) if self.gamma_fixed > 0 else float(self.gamma_init)
        mu = np.zeros(N, dtype=np.float64)
        converged = False
        conv_history: list[float] = []

        for it in range(1, self.max_iter + 1):
            # ── E-step ──────────────────────────────────────────────────────
            # Coupled prior precision ξ_n = α_n + β·Σ_{j∈L_n} α_j  (Fang 2015)
            xi = alpha.copy()
            if self.beta > 0.0:
                for n, nb_list in enumerate(nbrs):
                    if nb_list:
                        xi[n] += self.beta * alpha[nb_list].sum()

            D_xi = sp.diags(xi, format="csr")
            A = (gamma * CtC + D_xi).tocsr()

            rhs = gamma * Cty
            mu_new = spla.spsolve(A, rhs)

            Phi_diag = _hutchinson_diag(A, self.K, self._rng)
            Phi_diag = np.maximum(Phi_diag, 0.0)

            v_hat = mu_new ** 2 + Phi_diag

            # ── M-step ──────────────────────────────────────────────────────
            # Coupled second moment ω_n = v̂_n + β·Σ_{j∈L_n} v̂_j  (Fang 2015)
            omega = v_hat.copy()
            if self.beta > 0.0:
                for n, nb_list in enumerate(nbrs):
                    if nb_list:
                        omega[n] += self.beta * v_hat[nb_list].sum()

            alpha_update = (2 * self.a + 1) / (2 * self.b + omega)
            alpha_update = np.clip(alpha_update, self.alpha_min, self.alpha_max)

            lam = self.alpha_damping
            if lam > 0.0 and it > 1:
                alpha_new = (1.0 - lam) * alpha + lam * alpha_update
            else:
                alpha_new = alpha_update

            if self.gamma_fixed <= 0.0:
                residual = y64 - np.array(C @ mu_new, dtype=np.float64)
                tr_CPhiCt = float(np.dot(C_col_nnz, Phi_diag))
                gamma_den = float(np.dot(residual, residual)) + tr_CPhiCt
                gamma_new = float(M_obs) / max(gamma_den, 1e-12)
                gamma_new = np.clip(gamma_new, 1e-3, 1e6)
            else:
                gamma_new = float(self.gamma_fixed)

            mu_norm = np.linalg.norm(mu_new)
            rel_change = np.linalg.norm(mu_new - mu) / max(mu_norm, 1e-12)
            conv_history.append(rel_change)

            alpha = alpha_new
            gamma = gamma_new
            mu = mu_new

            if self.verbose:
                occ_cells = int((mu > 0.1).sum())
                print(f"    iter {it:3d} | rel_Δμ={rel_change:.2e} "
                      f"γ={gamma:.2f} "
                      f"μ_max={mu.max():.3f} "
                      f"cells>0.1: {occ_cells}/{N}")

            if rel_change < self.tol:
                converged = True
                break

        elapsed = time.perf_counter() - t0

        prob_map = np.clip(mu, 0.0, 1.0).reshape(G, G).astype(np.float32)

        if self.verbose:
            occupied = (prob_map > 0.5).sum()
            print(f"  PC-SBL done | iters={it} converged={converged} "
                  f"elapsed={elapsed:.1f}s "
                  f"occupied={occupied}/{N} ({100*occupied/N:.1f}%) "
                  f"μ_max={mu.max():.4f}")

        return {
            "prob_map": prob_map,
            "mu": mu,
            "alpha": alpha,
            "iters": it,
            "converged": converged,
            "elapsed_s": elapsed,
            "conv_history": conv_history,
        }


# ──────────────────────────── Upsample SBL → Tier-1 resolution ─────────────

def upsample_prob_map(prob_map_sbl: np.ndarray,
                      target_size: int) -> np.ndarray:
    """Nearest-neighbour upsample from SBL resolution to Tier-1 grid size."""
    G_sbl = prob_map_sbl.shape[0]
    scale = target_size // G_sbl
    if scale < 1:
        scale = 1
    return np.kron(prob_map_sbl, np.ones((scale, scale), dtype=np.float32))[:target_size, :target_size]
