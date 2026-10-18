#!/bin/bash
# Cloud Run task: stream this task's share of LAZ sheets from GUGiK into GCS (skips files already there).
set -u
gcloud storage cp gs://hackaton-honory-heat/T01/meta/manifest.tsv /tmp/m.tsv -q
fail=0
awk -v n="$CLOUD_RUN_TASK_COUNT" -v i="$CLOUD_RUN_TASK_INDEX" '(NR-1)%n==i' /tmp/m.tsv > /tmp/mine.tsv
while IFS=$'\t' read -r url dest; do
  if gcloud storage objects describe "$dest" --format="value(size)" >/dev/null 2>&1; then echo "skip $dest"; continue; fi
  ok=0
  for a in 1 2 3; do
    t0=$(date +%s)
    if curl -sfL --retry 3 --connect-timeout 30 -o /tmp/f.laz "$url" && gcloud storage cp -q /tmp/f.laz "$dest"; then
      echo "ok $dest $(stat -c %s /tmp/f.laz) bytes $(( $(date +%s)-t0 ))s"; ok=1; break
    fi
    echo "retry $a $url"; sleep 5
  done
  rm -f /tmp/f.laz
  [ $ok = 1 ] || { echo "FAIL $url"; fail=1; }
done < /tmp/mine.tsv
exit $fail
