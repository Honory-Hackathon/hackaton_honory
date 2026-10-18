# views/: heat-views (T13)

Three static pages that show the heat day 2026-06-28 (06:00–22:00 CEST, 33 steps) over Śródmieście. They are served by nginx on Cloud Run.

| Path | Page | Data |
|---|---|---|
| `/routes/` | Walking routes in the heat: every walk segment coloured by UTCI, plus stops, water and cool points, and benches in shade at the chosen step | `data/routes.bin` + `routes_meta.json` + `stops.json` (built from T03 + T08), T12 `points/` |
| `/shadows3d/` | 3D buildings and trees, SOLWEIG shade frame on the ground, sun light and sun compass, cloud okta per step | T12 `scene/`, `frames/shadow_solweig[_4m]/`, `frames/utci/` |
| `/temperature/` | UTCI / Tmrt per step, albedo and Landsat LST maps with value read-out on hover; per-surface charts (UTCI, sun vs shade, shortwave in and out, albedo and LST) | T12 `frames/`, `series/district_series.json` (forcing = `t05_forcing_20260628`) |

Live: **https://heat-views-269831067246.europe-central2.run.app** (`/` redirects to `/routes/`). Every page has a time slider with play and arrow-key control, the UTCI category legend (26 / 32 / 38 / 46 °C), and one-line captions in Polish and English. They work at 390 px. A deep link to a step is `#t=HH:MM`.

Libraries come from public CDNs: MapLibre GL 4.7.1 and deck.gl 9.0.38 (unpkg), Chart.js 4.4.4 (jsDelivr), and the Carto Positron basemap. The service does no computation; it only serves files.

## Files

```
views/
  build_data.py     BigQuery -> site/data/{routes.bin, routes_meta.json, stops.json}
  cloudbuild.yaml   one-step rebuild: copy T12 layers, build route heat, build image, deploy
  Dockerfile        nginx:1.27-alpine + site/
  nginx.conf        port 8080, gzip, /routes -> /routes/
  site/             common.js, common.css, routes/, shadows3d/, temperature/ (data/ is generated, not in git)
```

`routes.bin` holds the vertices as float32 lon/lat, the path start indices as uint32, then UTCI and shade share as uint8 in step-major order. UTCI is stored as `10 + byte/4` °C (255 = none) and shade share as `byte/250`. Offsets are in `routes_meta.json.layout`. The file is about 5.3 MB, or 2.9 MB gzipped. It holds 58,715 segments and 155 k vertices.

## Rebuild and the T08 swap

Use one command from the repo root:

```bash
source ~/warsaw-open-data/.envrc; export CLOUDSDK_CORE_ACCOUNT=teatr.sgh@gmail.com
gcloud builds submit --config views/cloudbuild.yaml --region europe-central2 views
```

- The heat source defaults to `auto`. If `gs://hackaton-honory-heat/T08/_SUCCESS` exists, it uses the final `warsaw_heat.t08_segment_heat` and `t08_stop_heat`. Otherwise it uses `t08_*_stub`. To force one, add `--substitutions _HEAT=final` or `_HEAT=stub`.
- The page shows which source it uses: a yellow "preliminary" badge for the stub, a green one for final.
- The build takes about 2.5 minutes. It copies about 165 MB of T12 layers into the image; `shadow_t02` frames and parquet files are left out.
- The deploy step keeps the existing public access (`allUsers` invoker was granted once by hand).

Local preview: run `python views/build_data.py --out views/site/data`, copy what you need from `gs://hackaton-honory-heat/T12/` into `views/site/data/t12/` with the same layout, then run `python -m http.server -d views/site`.
