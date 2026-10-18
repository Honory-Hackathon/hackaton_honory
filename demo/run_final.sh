#!/usr/bin/env bash
# T11 integration rerun on the final field (T08 tables). One command, from the repo root:
#   bash demo/run_final.sh            # final T08 field (default)
#   bash demo/run_final.sh stub       # t08_*_stub tables
# Needs: GOOGLE_APPLICATION_CREDENTIALS (or gcloud ADC), the T09 `router` package in the repo (optional:
# without it the walk-only fallback runs), and T11/bldg_only/*_shade.parquet from the t11-bldg job.
set -euo pipefail
HEAT="${1:-final}"
cd "$(dirname "$0")/.."
if [ "$HEAT" = "final" ]; then
  python3 -m demo.bldg_only --calib t08      # building-only costs calibrated on the final field
fi
python3 -m demo.run_demo --heat "$HEAT"     # also writes headline_<tag>.md and weights_summary_<tag>.csv
gsutil cp demo/demo_trips.json gs://hackaton-honory-heat/T11/demo_trips.json
