"""
Multi-frame accumulation for LiDAR OGM (Tier 1 and Tier 2).

Transform chain (improvement_v7 §2)
------------------------------------
Bring scan j's points into the eval keyframe k's ego frame:

    p_ego_k = R(q_k)ᵀ · ( R(q_j) · p_ego_j + t_j − t_k )

Steps:
  1. Points start in sensor frame of scan j.
  2. transform_to_ego (existing) → ego(j) frame.
  3. Ground removal and height filter in ego(j) — must be done BEFORE
     transforming so z is correctly relative to the vehicle roof/floor at
     scan j's position (fixes the 54.9% bug: ground is z<0.3 in ego(j),
     but after rotating into ego(k) the ground plane tilts and z-filter fails).
  4. Apply R(q_j), add t_j  → global frame.
  5. Subtract t_k, apply R(q_k)ᵀ  → ego(k) frame.
  6. Range-filter in ego(k) and project to BEV.

Tier-1 multi-frame
-------------------
Decay-weighted log-odds fusion. For eval frame k, window W=[k-w, k+w]:

    L(cell) = clip( Σ_{j∈W} λ^{|k-j|} · l_update_j(cell), -5, +5 )

λ=1 → equal weight (max coverage); λ≈0.7 → recent/eval frame dominates.

Tier-2 multi-frame
-------------------
Stack one C matrix per windowed scan (all points expressed in ego(k)).
Row weights scaled by λ^{|k-j|}. Solve once with PCSBL.
More rows per cell = the coverage sparse SBL was starved for.
"""

import numpy as np
import scipy.sparse as sp
from scipy.spatial.transform import Rotation

from typing import Any

from .data_loader import NuScenesLoader
from .preprocessor import transform_to_ego, height_filter, range_filter, project_bev, discretize
from .occupancy_grid import OccupancyGrid
from .sensor_model import update_grid_from_scan
from .pc_sbl import PCSBL, build_C_matrix


# ── ego-pose helpers ──────────────────────────────────────────────────────────

def _pose_R_t(ego_pose: dict):
    """Return (R 3×3, t (3,)) from an ego_pose dict."""
    q = ego_pose["rotation"]       # [w, x, y, z]
    R = Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()
    t = np.array(ego_pose["translation"], dtype=np.float64)
    return R, t


def transform_ego_j_to_ego_k(pts_ego_j: np.ndarray,
                               pose_j: dict,
                               pose_k: dict) -> np.ndarray:
    """
    Transform (N,3+) points from ego(j) to ego(k) frame.

    p_ego_k = R_k^T · (R_j · p_ego_j + t_j - t_k)
    """
    R_j, t_j = _pose_R_t(pose_j)
    R_k, t_k = _pose_R_t(pose_k)

    xyz_j = pts_ego_j[:, :3].astype(np.float64)
    xyz_global = xyz_j @ R_j.T + t_j          # ego(j) → global
    xyz_k = (xyz_global - t_k) @ R_k          # global → ego(k)

    result = pts_ego_j.copy().astype(np.float64)
    result[:, :3] = xyz_k
    return result.astype(np.float32)


# ── load + preprocess window of scans ────────────────────────────────────────

def load_window(loader: Any,
                scene_name: str,
                k: int = 0,
                w: int = 2,
                grid_size: int = 80,
                cell_size: float = 0.5) -> list[dict]:
    """
    Load w frames before and after eval keyframe k, all projected into ego(k).

    Returns a list of dicts per frame j ∈ [k-w, k+w] ∩ valid range:
        {
          'pts_xy':   (M, 2) float32 — BEV points in ego(k) frame
          'offset':   int   — j - k  (0 = eval frame)
          'n_raw':    int   — points before range filter
        }

    Ground removal and height filter are applied in ego(j) BEFORE transforming.
    """
    scene = loader.get_scene_by_name(scene_name)
    tokens = loader.get_lidar_tokens_for_scene(scene)
    n_frames = len(tokens)

    if k >= n_frames:
        raise ValueError(f"k={k} out of range (scene has {n_frames} frames)")

    sd_k = tokens[k]
    pose_k = loader.get_ego_pose(sd_k)
    half = grid_size * cell_size / 2.0

    frames = []
    for j in range(max(0, k - w), min(n_frames, k + w + 1)):
        sd_j = tokens[j]
        pose_j = loader.get_ego_pose(sd_j)

        # Load → sensor frame
        pts_raw = loader.load_lidar_points(sd_j)
        cs = loader.get_calibrated_sensor(sd_j)

        # sensor → ego(j)
        pts_ego_j = transform_to_ego(pts_raw, cs)

        # Filter in ego(j) frame (ground/height correct here)
        pts_ego_j = height_filter(pts_ego_j)

        if len(pts_ego_j) == 0:
            continue

        # ego(j) → ego(k)
        pts_ego_k = transform_ego_j_to_ego_k(pts_ego_j, pose_j, pose_k)

        # Range filter and project in ego(k)
        pts_ego_k = range_filter(pts_ego_k, half)
        if len(pts_ego_k) == 0:
            continue

        pts_xy = project_bev(pts_ego_k)
        frames.append({
            'pts_xy': pts_xy,
            'offset': j - k,
            'n_raw': len(pts_raw),
        })

    return frames


# ── Tier-1 multi-frame ────────────────────────────────────────────────────────

def multiframe_t1(loader: Any,
                  scene_name: str,
                  k: int = 0,
                  w: int = 2,
                  decay: float = 1.0,
                  grid_size: int = 80,
                  cell_size: float = 0.5) -> tuple[np.ndarray, float]:
    """
    Tier-1 multi-frame: decay-weighted log-odds fusion.

    Args:
        k:     eval keyframe index (default 0)
        w:     half-window size (frames [k-w, k+w])
        decay: λ ∈ (0,1]. Weight for frame j = λ^{|j-k|}.
               1.0 = equal weight; 0.7 = recent dominates.

    Returns:
        prob_map: (grid_size, grid_size) float32
        occ_pct:  fraction of cells with P > 0.5
    """
    frames = load_window(loader, scene_name, k=k, w=w,
                         grid_size=grid_size, cell_size=cell_size)
    half = grid_size * cell_size / 2.0
    log_odds = np.zeros((grid_size, grid_size), dtype=np.float64)

    for frame in frames:
        pts_xy = frame['pts_xy']
        lam = decay ** abs(frame['offset'])

        cells = discretize(pts_xy, half, cell_size, grid_size)
        if len(cells) == 0:
            continue

        # Use OccupancyGrid + update_grid_from_scan so free-cell deduplication
        # matches the original single-scan Tier-1 path (numpy repeated-index
        # writes don't accumulate, so each cell gets L_FREE once per scan).
        g = OccupancyGrid(grid_size, cell_size)
        update_grid_from_scan(g, cells)
        log_odds += lam * g.log_odds.astype(np.float64)

    log_odds = np.clip(log_odds, -5.0, 5.0)
    prob_map = (1.0 / (1.0 + np.exp(-log_odds))).astype(np.float32)
    occ_pct = float((prob_map > 0.5).mean())
    return prob_map, occ_pct


# ── Tier-2 multi-frame ────────────────────────────────────────────────────────

def multiframe_t2(loader: Any,
                  scene_name: str,
                  k: int = 0,
                  w: int = 2,
                  decay: float = 1.0,
                  grid_size: int = 80,
                  cell_size: float = 0.5,
                  **pcsbl_kwargs) -> dict:
    """
    Tier-2 multi-frame: stack C rows from all windowed scans, solve once.

    Each frame j contributes its C_j (built in ego(k)) scaled by λ^{|j-k|}.
    The combined [C; λ·C_1; ...] gives more measurements per cell, raising IoBB.

    Returns dict with keys: prob_map, elapsed_s, iters, converged, n_frames
    """
    import time
    frames = load_window(loader, scene_name, k=k, w=w,
                         grid_size=grid_size, cell_size=cell_size)

    n_angles     = pcsbl_kwargs.pop('n_angles', 360)
    hits_per_bin = pcsbl_kwargs.pop('hits_per_bin', 3)
    free_weight  = pcsbl_kwargs.pop('free_weight', 0.5)

    N = grid_size * grid_size
    all_C_parts = []
    all_y_parts = []

    for frame in frames:
        pts_xy = frame['pts_xy']
        lam = decay ** abs(frame['offset'])

        C_j, y_j = build_C_matrix(
            pts_xy,
            grid_size=grid_size,
            cell_size=cell_size,
            n_angles=n_angles,
            ego_xy=np.zeros(2),      # ego(k) is origin
            hits_per_bin=hits_per_bin,
            free_weight=free_weight,
        )
        if C_j.shape[0] == 0:
            continue

        # Scale rows by decay weight
        if lam != 1.0:
            C_j = C_j.multiply(lam)
            y_j = y_j * lam

        all_C_parts.append(C_j)
        all_y_parts.append(y_j)

    if not all_C_parts:
        return {
            'prob_map': np.zeros((grid_size, grid_size), dtype=np.float32),
            'elapsed_s': 0.0, 'iters': 0, 'converged': False, 'n_frames': 0,
        }

    C_stacked = sp.vstack(all_C_parts, format='csr')
    y_stacked = np.concatenate(all_y_parts)

    t0 = time.perf_counter()
    sbl = PCSBL(
        n_angles=n_angles,
        hits_per_bin=hits_per_bin,
        free_weight=free_weight,
        grid_size_sbl=grid_size,
        cell_size_sbl=cell_size,
        **pcsbl_kwargs,
    )

    # Run EM with the stacked C/y directly (bypass build_C_matrix inside run())
    res = _run_em(sbl, C_stacked, y_stacked, grid_size)
    res['elapsed_s'] = time.perf_counter() - t0
    res['n_frames'] = len(frames)
    return res


def _run_em(sbl: PCSBL,
            C: sp.csr_matrix,
            y: np.ndarray,
            grid_size: int) -> dict:
    """
    Run PC-SBL EM given a pre-built C matrix and measurement vector y.
    Mirrors the core loop in PCSBL.run() but accepts C/y directly.
    """
    import scipy.sparse.linalg as spla
    from .pc_sbl import _build_neighbour_lists, _hutchinson_diag

    G = grid_size
    N = G * G
    Ct = C.T.tocsr()
    CtC = (Ct @ C).tocsr()
    Cty = np.array(Ct @ y, dtype=np.float64)
    y64 = y.astype(np.float64)
    C_col_nnz = np.array((C != 0).sum(axis=0)).ravel().astype(np.float64)
    nbrs = _build_neighbour_lists(G)

    # Warm-start from terminal rows only (rows where y==1)
    B = int((y > 0.5).sum())    # number of occupied rows
    occ_mask = y > 0.5
    C_term = C[occ_mask]
    y_term = y[occ_mask]
    alpha = sbl._warm_start_alpha(C_term, y_term, N)

    gamma = float(sbl.gamma_fixed) if sbl.gamma_fixed > 0 else float(sbl.gamma_init)
    mu = np.zeros(N, dtype=np.float64)
    M_obs = C.shape[0]
    converged = False
    conv_history = []

    for it in range(1, sbl.max_iter + 1):
        xi = alpha.copy()
        if sbl.beta > 0.0:
            for n, nb_list in enumerate(nbrs):
                if nb_list:
                    xi[n] += sbl.beta * alpha[nb_list].sum()

        D_xi = sp.diags(xi, format="csr")
        A = (gamma * CtC + D_xi).tocsr()
        mu_new = spla.spsolve(A, gamma * Cty)

        Phi_diag = _hutchinson_diag(A, sbl.K, sbl._rng)
        Phi_diag = np.maximum(Phi_diag, 0.0)
        v_hat = mu_new ** 2 + Phi_diag

        omega = v_hat.copy()
        if sbl.beta > 0.0:
            for n, nb_list in enumerate(nbrs):
                if nb_list:
                    omega[n] += sbl.beta * v_hat[nb_list].sum()

        alpha_update = (2 * sbl.a + 1) / (2 * sbl.b + omega)
        alpha_update = np.clip(alpha_update, sbl.alpha_min, sbl.alpha_max)
        lam = sbl.alpha_damping
        alpha_new = (1.0 - lam) * alpha + lam * alpha_update if (lam > 0 and it > 1) else alpha_update

        if sbl.gamma_fixed <= 0.0:
            residual = y64 - np.array(C @ mu_new, dtype=np.float64)
            tr_CPhiCt = float(np.dot(C_col_nnz, Phi_diag))
            gamma_new = float(M_obs) / max(float(np.dot(residual, residual)) + tr_CPhiCt, 1e-12)
            gamma_new = np.clip(gamma_new, 1e-3, 1e6)
        else:
            gamma_new = float(sbl.gamma_fixed)

        mu_norm = np.linalg.norm(mu_new)
        rel_change = float(np.linalg.norm(mu_new - mu) / max(mu_norm, 1e-12))
        conv_history.append(rel_change)

        alpha, gamma, mu = alpha_new, gamma_new, mu_new

        if rel_change < sbl.tol:
            converged = True
            break

    prob_map = np.clip(mu, 0.0, 1.0).reshape(G, G).astype(np.float32)
    return {
        'prob_map': prob_map,
        'mu': mu,
        'alpha': alpha,
        'iters': it,
        'converged': converged,
        'conv_history': conv_history,
    }
