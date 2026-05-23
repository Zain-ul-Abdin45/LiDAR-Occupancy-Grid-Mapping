"""
Occupancy grid data structure using log-odds representation.

Each cell stores L (log-odds), where:
  L = log( P(occupied) / P(free) )
  P(occupied) = 1 / (1 + exp(-L))   (sigmoid)

Initial L = 0  →  P = 0.5  (maximum uncertainty)
"""

import numpy as np


LOG_ODDS_MIN = -5.0  # corresponds to P ≈ 0.007
LOG_ODDS_MAX = +5.0  # corresponds to P ≈ 0.993


class OccupancyGrid:
    def __init__(self, grid_size: int = 80, cell_size: float = 0.5):
        """
        grid_size: number of cells per side (80 → 40 m at 0.5 m/cell)
        cell_size: metres per cell
        """
        self.grid_size = grid_size
        self.cell_size = cell_size
        # Log-odds grid, initialized to 0 (P = 0.5, maximum uncertainty)
        self.log_odds: np.ndarray = np.zeros((grid_size, grid_size), dtype=np.float32)

    def reset(self):
        self.log_odds[:] = 0.0

    def update(self, rows: np.ndarray, cols: np.ndarray, delta: float):
        """
        Add delta to the log-odds of the given cells and clamp.

        Args:
            rows, cols: 1-D int arrays of valid grid indices
            delta:      log-odds increment (positive = more occupied,
                        negative = more free)
        """
        self.log_odds[rows, cols] = np.clip(
            self.log_odds[rows, cols] + delta,
            LOG_ODDS_MIN,
            LOG_ODDS_MAX,
        )

    def get_probability(self) -> np.ndarray:
        """Return P(occupied) for every cell via sigmoid."""
        return 1.0 / (1.0 + np.exp(-self.log_odds))

    def get_binary_map(self, threshold: float = 0.5) -> np.ndarray:
        """Return bool array: True where P(occupied) > threshold."""
        return self.get_probability() > threshold

    @property
    def ego_row(self) -> int:
        return self.grid_size // 2

    @property
    def ego_col(self) -> int:
        return self.grid_size // 2
