#!/usr/bin/env bash
# Phase 5 final sweep: 10 scenes, beta in {0,1}, Phase 5 optimal config.
# Config: gamma=30, free_weight=0.5, hits=3, tol=2e-3, max_iter=150, eta_th=0.5

PY=~/.pyenv/versions/3.11.9/bin/python3
SCENES=(
  "scene-0061" "scene-0103" "scene-0553" "scene-0655"
  "scene-0757" "scene-0796" "scene-0916" "scene-1077"
  "scene-1094" "scene-1100"
)

for BETA in 0 1; do
  echo "========== beta=${BETA} =========="
  for SCENE in "${SCENES[@]}"; do
    echo "  ${SCENE}"
    $PY main.py \
      --scene "$SCENE" \
      --scan-index 0 \
      --single-scan \
      --tier2 \
      --beta "$BETA" \
      --sbl-grid 80 \
      --sbl-gamma 30.0 \
      --sbl-hits 3 \
      --sbl-threshold 0.5 \
      --sbl-free-weight 0.5 \
      --sbl-max-iter 150 \
      --eval \
      --eval-threshold 0.5 \
      --no-show \
      2>&1 | grep -E "Tier|NMSE|IoBB|done|ERROR"
  done
done

echo "Sweep complete. Results in results/results_log.md"
