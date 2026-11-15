#!/usr/bin/env bash
# v5 (unified targets 2003-2024) and v6 (real targets 2012-2024): weather + FFDI, year-block calibration;
# then v6 and the v5+v6 ensemble scored on 2025 against the same (unified) targets as v5.
set -e
cd "$(dirname "$0")"
echo "== v5 $(date +%H:%M)"
FCST_TARGETS=unified python run_pipeline.py --from tables --to evaluate --weather target --name v5_unified_ffdi_cv > output/run_v5.out 2>&1
rm -rf output/evaluation_v5 && cp -r output/evaluation output/evaluation_v5
echo "== v6 $(date +%H:%M)"
FCST_TARGETS=real python run_pipeline.py --from attributes --to train --weather target --name v6_real_ffdi_cv > output/run_v6.out 2>&1
V5=$(ls -d models_archive/*_v5_unified_ffdi_cv | tail -1)
echo "== evaluate v6 $(date +%H:%M)"
FCST_TARGETS=unified python evaluate.py --models models --out output/evaluation_v6 > output/eval_v6.out 2>&1
echo "== evaluate ensemble v5+v6 $(date +%H:%M)"
FCST_TARGETS=unified python evaluate.py --models "$V5" models --out output/evaluation_ensemble > output/eval_ens.out 2>&1
echo "== done $(date +%H:%M)"
