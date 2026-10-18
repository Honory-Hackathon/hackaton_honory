# demo: T11 demo trips, baselines and dose-weight comparison

Report: [`reports/T11/README.md`](../reports/T11/README.md). Brief: [`tasks/T11`](../tasks/T11_validation_demo.md).

| File | What |
|---|---|
| `demo_trips.json` | The 5 demo trips (origin/destination `[lon, lat]`, window, one-line reason). Copy: `gs://hackaton-honory-heat/T11/demo_trips.json` |
| `bldg_shade_job.py`, `Dockerfile.job` | Building-only shade per segment and stop: `B = f_bldg + f_both` (plus `f_tree`, `f_sun`) from T02, aggregated over T03 segment pixels and stop areas. Image `t11-bldg` (Cloud Run Job `t11-bldg`, europe-north1). Output `T11/bldg_only/{segment,stop}_shade.parquet` |
| `bldg_only.py` | Turns the shares into T08-column heat tables: building-only costs (`T11/segment_heat_bldg_only.parquet`, `stop_heat_bldg_only.parquet`) and the T02-zone stand-in field (`*_standin.parquet`). `--calib pilot` (T06 pilot zone Tmrt + T05 forcing) or `--calib t08` / `t08_stub` (fit on the T08 field) |
| `ors_shaded.py` | ORS Shaded client; every answer cached in `T11/ors_cache/` |
| `engine.py` | Walk-only pricer/router on any T08-column table: like-for-like pricing of foreign lines (ORS, router legs) and the walk-only candidates |
| `run_demo.py` | Comparison table per trip + dose-weight sensitivity + maps (`--heat final|stub|standin`) |
| `headline.py` | Pitch numbers from the comparison |
| `run_final.sh` | **Integration rerun on the final field: `bash demo/run_final.sh`** |

Router: `run_demo` imports `router` from the repo root (or `ROUTER_PATH`); without it, it runs walk-only and tags outputs `_walkonly`.
`T11_NO_ROUTER=1` forces walk-only. Credentials: `GOOGLE_APPLICATION_CREDENTIALS` or gcloud ADC. Local deps: pandas, numpy, geopandas, pyproj, shapely 2, igraph, matplotlib, requests, pythermalcomfort, google-cloud-{storage,bigquery}.
