# app/ — heat-app (T10)

Public web app: a time-slider UTCI map (33 steps of 2026-06-28) with a shade toggle, a click-for-time-series point
query, and a trip planner with option cards (*coolest*, *balanced*, *fastest*, *walk only*). Polish and English.
No accounts, no persistence.

```
app/
  Dockerfile          heat-app image; build context = repo root so router/ (T09) is vendored when present
  cloudbuild.yaml     builds the heat-app image
  .gcloudignore       limits the upload to app/ and router/
  server/main.py      FastAPI: static page, /frames proxy, /api/config, /api/plan, /api/price, /api/point
  server/stub_router.py   STUB with the T09 contract shape (fake numbers); used only when router/ cannot be imported
  server/static/      index.html, app.js (Leaflet 1.9.4 from unpkg), style.css
  render/render_frames.py  any 33-band raster(s) -> EPSG:3857 XYZ PNG frames in GCS (Cloud Run Job)
  render/Dockerfile   t10-render image
```

Live: **https://heat-app-269831067246.europe-central2.run.app** (Cloud Run service `heat-app`, europe-central2, public).
Current env: `UTCI_FRAMES=t07_utci SHADE_FRAMES=t07_shade UTCI_COG=…/T07/utci.tif SHADOW_COG=…/T07/sun_solweig.tif HEAT_SOURCE=placeholder`.
**Once T08 writes `_SUCCESS`, the only change needed is:**
`gcloud run services update heat-app --region europe-central2 --update-env-vars HEAT_SOURCE=final`
(plus `HEAT_VIEWS_URL=<heat-views url>` once T13 is up).

## Frames (map layers)

The rasters are EPSG:2180 at 1 m (72 Mpx × 33 steps for the district), so they are **pre-rendered** once into
EPSG:3857 XYZ tiles (z12–17; z17 ≈ 0.73 m on the ground in Warsaw), palette PNG, transparent where there is no
data (buildings, water, outside the field). Empty tiles are not written; the app returns a 1×1 transparent PNG for them.

Layout: `gs://hackaton-honory-heat/T10/frames/<source>/<step:02d>/<z>/<x>/<y>.png`, plus `<source>/meta.json`
(inputs, bounds, zooms, step labels, colour encoding) and `<source>/<step>/stats.json` (tile count, p05/p50/p95).

- `--kind utci`: colours on the UTCI stress categories with hard steps at 26 / 32 / 38 / 46 °C and a lightness ramp
  inside each category (palette index = 1 + (UTCI − 10) / 0.25).
- `--kind shadow`: SOLWEIG `shadow` (1 = sunlit, 0 = shade, 0.03 under trees) → dark-blue overlay with alpha ∝ shade.
  Steps where every pixel is 1 (SOLWEIG writes all-sunlit after sunset, 21:00–22:00) are treated as night: no tiles.

Inputs: one COG or several (globs allowed) on the same grid; they are mosaicked in memory. Bands are split over Cloud
Run tasks (`CLOUD_RUN_TASK_INDEX/COUNT`), task 0 writes `meta.json`.

Sources rendered so far:

| source | input |
|---|---|
| `pilot_utci`, `pilot_shadow` | `T06/cog/{utci,shadow}_solweig.tif` (pilot 1.2 × 0.8 km) |
| `t06_district_utci`, `t06_district_shadow` | `T06/district/tiles/*/{utci,shadow}.tif`, all 27 tiles, not masked |
| **`t07_utci`, `t07_shade`** (live) | `T07/utci.tif`, `T07/sun_solweig.tif` (jobs `t10-render-t07-utci`, `t10-render-t07-shade`) |

### Rerun on T07 (integration)

```bash
source ~/warsaw-open-data/.envrc; export CLOUDSDK_CORE_ACCOUNT=teatr.sgh@gmail.com
IMG=europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t10-render:latest
# rebuild the image only if render_frames.py changed:
#   (cd app/render && gcloud builds submit --tag $IMG --region europe-central2 .)
gcloud run jobs deploy t10-render-t07-utci --image $IMG --region europe-central2 \
  --cpu 4 --memory 12Gi --tasks 6 --parallelism 1 --max-retries 2 --task-timeout 3600 \
  --set-env-vars '^@^RENDER_INPUT=gs://hackaton-honory-heat/T07/utci.tif@RENDER_KIND=utci@RENDER_SOURCE=t07_utci@RENDER_NOTE=T07 UTCI walkable' \
  --execute-now
# shade from T07 (uint8 x 250, 255 = nodata; handled by the renderer):
#   RENDER_INPUT=gs://hackaton-honory-heat/T07/sun_solweig.tif  RENDER_KIND=shadow  RENDER_SOURCE=t07_shade
```

Raise `--parallelism` once other jobs free the region (quota 40 GiB per region: parallelism × memory must fit, **and
the heat-app service's memory counts against the same regional quota**: a new revision cannot start while jobs hold
all 40 GiB). Render only from a finished input set: the glob is expanded per task, so tiles arriving mid-run give
steps with different coverage.
A district pass takes ~10 s per step per task plus ~30 s of download (6000 × 6000 px tiles input).
Locally (small inputs only): `python app/render/render_frames.py --input <cog> --kind utci --source test --out /tmp/frames --steps 14`.

## Service

Env vars (all optional):

| var | default | meaning |
|---|---|---|
| `UTCI_FRAMES` / `SHADE_FRAMES` | `pilot_utci` / `pilot_shadow` | frame sources shown on the map (override per page: `?utci=<source>&shade=<source>`) |
| `UTCI_COG` / `SHADOW_COG` | T06 pilot COGs | 33-band rasters for the click time series; may contain `{e}_{n}` for 1 km tiles, e.g. `gs://hackaton-honory-heat/T06/district/tiles/{e}_{n}/utci.tif` |
| `HEAT_SOURCE` | — | passed through to router/ (`placeholder` / `stub` / `final`) |
| `ROUTER` | `auto` | `auto` = import `router` (T09) if it imports, else the stub; `stub` = force stub |
| `HEAT_VIEWS_URL` | empty | base URL of the T13 `heat-views` service; nav links go to `<url>/routes`, `/shadows3d`, `/temperature`. Empty = greyed "soon" chips |
| `FRAMES_PREFIX` | `gs://hackaton-honory-heat/T10/frames` | |

The router is imported in a background thread at startup (calls `router.warmup()` if it exists), so the port opens at
once; `/api/plan` waits for it. `/api/config` reports which router is live (`router`, `router_error`).

### Build and deploy (integration)

From the **repo root** (so router/ is included):

```bash
source ~/warsaw-open-data/.envrc; export CLOUDSDK_CORE_ACCOUNT=teatr.sgh@gmail.com
gcloud builds submit --config app/cloudbuild.yaml --ignore-file=app/.gcloudignore --region europe-central2 .
gcloud run deploy heat-app --image europe-central2-docker.pkg.dev/hackaton-honory/hackathon/heat-app:latest \
  --region europe-central2 --allow-unauthenticated --cpu 2 --memory 2Gi --min-instances 1 --max-instances 3 \
  --update-env-vars 'HEAT_SOURCE=final,UTCI_FRAMES=t07_utci,SHADE_FRAMES=t07_shade,UTCI_COG=gs://hackaton-honory-heat/T07/utci.tif,SHADOW_COG=gs://hackaton-honory-heat/T07/sun_solweig.tif,HEAT_VIEWS_URL=https://heat-views-<...>.run.app'
```

If router/ needs more memory for its data, raise `--memory` (up to 32 GiB) and `--cpu`. router/requirements.txt, if
present, is installed into the image. Point queries need the service account to read the bucket (default compute SA
does).

### API

- `GET /api/config` — frame sources + meta, router name, T13 links.
- `POST /api/plan` `{origin:[lon,lat], destination:[lon,lat], earliest:"HH:MM", latest:"HH:MM", weights?}` → T09 `plan()` dict + `router`, `elapsed_s`.
- `POST /api/price` `{coords:[[lon,lat],…], depart:"HH:MM"}` → T09 `price_path()`.
- `GET /api/point?lon=&lat=` → `utci[33]`, `shadow[33]` at that pixel.
- `GET /frames/<source>/<step>/<z>/<x>/<y>.png`, `/frames/<source>/meta.json` — GCS proxy with a 256 MiB LRU cache.
- `GET /api/health`. (Cloud Run reserves `/healthz`.)

Page URL params: `lang=pl|en`, `step=0..32`, `utci=`, `shade=`, `shadeOn=1`.

## Local run

```bash
cd app/server && GOOGLE_APPLICATION_CREDENTIALS=... python -m uvicorn main:app --port 8765
```
