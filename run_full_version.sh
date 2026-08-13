#!/usr/bin/env bash
# One-shot report snapshot: every benchmark this project reports on, in order.
# Run this whenever you need a clean, complete results set for the presentation/report.
#
# Usage:
#   ./run_full_version.sh
#
# All numeric results are appended to results/results_log.md (never overwritten).
# All figures/tables land in output/ (nuScenes) and output_kitti/ (KITTI single-scan).
#
# Expect ~45-90 minutes total — PC-SBL's EM loop does up to max_iter Hutchinson-probed
# sparse solves per scan, and several stages here run it across dozens of scenes/frames.


set -e
cd "$(dirname "$0")"
PY=~/.pyenv/versions/3.11.9/bin/python3
[ -x "$PY" ] || PY=python3

log() { echo -e "\n=== [$(date +%H:%M:%S)] $1 ===\n"; }

log "1/10 T1 — nuScenes, all 10 scenes, single-scan"
$PY main.py --dataset nuscenes --all-scenes --single-scan --eval --no-show --out output

log "2/10 T2 (PC-SBL beta=1) — nuScenes, all 10 scenes, single-scan"
$PY main.py --dataset nuscenes --all-scenes --single-scan --tier2 --beta 1.0 --eval --no-show --out output

log "3/10 Multi-frame sweep (T1+T2, w in {0,1,2,4}) — nuScenes, all 10 scenes"
$PY run_multiframe_benchmark.py --out output

log "4/10 KITTI 3D Object — T1/T2 generalization benchmark (50 frames)"
$PY run_kitti_benchmark.py

log "5/10 KITTI Odometry — T1/T2 single+multi-frame benchmark (50 frames, seq 00)"
$PY run_kitti_odometry_benchmark.py --sequence 00 --n-frames 50 --window 2

log "6/10 Hyperparameter — acceleration ablation (rectangular submap grid)"
$PY run_accel_benchmark.py --out output

log "7/10 Hyperparameter — angular sector ablation + precision metric"
$PY run_sector_benchmark.py --out output

log "8/10 Report figures (tradeoff, coupling, convergence, beta sweep)"
$PY build_report_figures.py

log "9/10 Cross-dataset comparison graph (nuScenes vs KITTI 3D vs KITTI Odometry)"
$PY build_cross_dataset_comparison.py

log "10/10 EM convergence trace and single-frame IoBB overlays (Figures 3, 5)"
$PY plot_convergence.py
$PY check_iobb_overlay.py

log "Done. Results in results/results_log.md, figures in output/."
