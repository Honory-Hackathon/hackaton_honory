# meteo: T05 weather forcing and Landsat `pmwc`

Report: [`reports/T05`](../reports/T05/README.md). Outputs: `gs://hackaton-honory-heat/T05/` and BigQuery `hackaton-honory.warsaw_heat.t05_forcing_20260628`.

The Cloud Run job runs the copy in `gs://hackaton-honory-heat/T05/code/`; this folder mirrors it. After changing a script here, copy it there before rerunning.

| File | Output |
|---|---|
| `forcing.py` | `T05/forcing/forcing_20260628.{parquet,csv}`, `checks.json`, BigQuery table |
| `scenes.py` | `T05/pmwc/scene_screen.json`: Landsat scenes 06-10…07-15 screened by QA over the district |
| `pmwc.py` | `T05/pmwc/*.tif` (anomaly, source, masked LST) and `meta.json` |
| `report.py` | `T05/report/report.html` and the two PNGs |

## Run

Job `t05-meteo` (europe-central2, 2 vCPU / 8 GiB) on the `t01-lidar:2` image. It pip-installs pandas, pyarrow, pvlib, requests and google-cloud-bigquery, then runs `T05/code/$SCRIPT`:

```bash
for s in forcing.py scenes.py pmwc.py report.py; do
  gcloud run jobs execute t05-meteo --region europe-central2 --update-env-vars SCRIPT=$s --wait
done
```

Each script takes under a minute.
