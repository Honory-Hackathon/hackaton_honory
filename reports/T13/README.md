# T13: Visualisations

Three public demo pages show the heat day 2026-06-28 over Śródmieście, from 06:00 to 22:00 CEST in 33 steps:

| Page | URL | Question it answers |
|---|---|---|
| Walking routes in the heat | https://heat-views-269831067246.europe-central2.run.app/routes/ | Where is it bearable? |
| 3D shadows | https://heat-views-269831067246.europe-central2.run.app/shadows3d/ | Why does shade move? |
| Temperature and albedo | https://heat-views-269831067246.europe-central2.run.app/temperature/ | What makes a surface hot? |

Each page has:
- a time slider over the 33 steps, with a play button, arrow keys, and a deep link (`#t=15:00`);
- the UTCI stress-category legend (< 26, 26–32, 32–38, 38–46, > 46 °C);
- one-line captions in Polish and English.

The pages work on a laptop and at 390 px phone width. On phones they use 4 m shadow frames and compact panels. The service only serves static files. Its code is in [`views/`](../../views/README.md).

## Where things are

| Output | Where |
|---|---|
| Cloud Run service `heat-views` (nginx, public, 1 vCPU / 512 MiB, max 4 instances) | europe-central2, https://heat-views-269831067246.europe-central2.run.app (also https://heat-views-gwnbln5o6q-lm.a.run.app) |
| Image | `europe-central2-docker.pkg.dev/hackaton-honory/hackathon/heat-views:latest` |
| Pages and build | `views/site/{routes,shadows3d,temperature}/index.html`, `views/site/common.{js,css}`, `views/build_data.py`, `views/cloudbuild.yaml` |
| Route heat data in the image | `data/routes.bin` (58,715 segments, 155 k vertices, 33 steps of UTCI and shade share; 2.9 MB gzipped), `data/routes_meta.json`, `data/stops.json` (360 stops) |
| T12 layers in the image | `data/t12/…`: the same layout as `gs://hackaton-honory-heat/T12/`, without `shadow_t02` and parquet files (≈165 MB) |
| Screenshots | this folder: `*_desktop.png` (1400 × 900) and `*_390px.png` (390 × 844) |

## Views

**Routes** (`routes_desktop.png`, `routes_390px.png`)
- Every T03 walk segment is coloured by its T08 `utci_mean` at the chosen step, on the same ramp as T12: hard steps at 26 / 32 / 38 / 46 °C. Stops are coloured by T08 stop UTCI.
- The T12 cool points are drawn by type (drinking water, fountains, water curtains, water bodies, parks, cool indoor places, shelters, toilets), along with park, water and indoor polygons.
- An optional layer shows benches whose shade share is at least 0.5 at the chosen step, so the set changes over the day.
- Hovering or tapping shows UTCI, category and shade per segment, stop or point.
- A line under the slider gives the length-weighted share of the 777 km network in each category at that step.

**3D shadows** (`shadows3d_desktop.png` at 15:00, `shadows3d_0800_desktop.png` at 08:00, `shadows3d_390px.png`)
- The scene has 27,169 extruded LiDAR building blocks and 57,619 trees (a trunk up to crown base and an octagonal crown of radius `r`).
- The SOLWEIG shadow frame for the step lies on the ground: navy is building shade, green is tree shade.
- deck.gl `_SunLight` lights the buildings from the step's timestamp, and its intensity drops with cloud okta.
- A sun compass shows elevation, azimuth, shadow length per metre of height, cloud okta, GHI and air temperature.
- The UTCI ground frame can be switched on. A line gives the sunlit share of paved ground and UTCI in sun and in shade.

**Temperature and albedo** (`temperature_desktop.png`, `temperature_390px.png`)
- The map shows UTCI or Tmrt per step, or the static Sentinel-2 albedo or Landsat LST (25.06, 11:25). Opacity is adjustable.
- Hovering or tapping reads the value from T12's grey value PNGs.
- Four charts, each with a marker at the current step:
  - UTCI per surface type with air temperature, over the UTCI category bands;
  - UTCI and Tmrt in sun and in shade;
  - estimated incoming and reflected shortwave per surface;
  - albedo and LST per surface type.
- A "now" table gives the forcing at the step and, per surface type, its sunlit share, Tmrt and UTCI.

## What the pages show (numbers from the data in them)

- **15:00 is the worst step on the walk network:**
  - 85 % of network length is ≥ 38 °C UTCI (very strong heat stress), and only 2 % is below 32 °C.
  - The length-weighted mean is 40.0 °C, and 60 % of the length is shaded.
  - At 06:00, 98 % is below 26 °C. By 09:00, 31 % is already above 32 °C.
  - Source: final T08 field.
- **Shade is worth about 6 K of UTCI at 15:00:** 43.8 °C in sun vs 38.0 °C in shade, with air at 37.2 °C. Tree cover keeps ground pixels at 8 % sunlit all day, against 70–78 % for paved ground at midday.
- **Evening:** after 20:00 almost the whole network is in shade (97 %), but UTCI stays at 30–33 °C. The values for 21:00–22:00 are a lower bound, because SOLWEIG has no heat release from pavement after sunset (spec §6). The pages say this for steps 30–32.

## Data swap (T08)

The routes page uses the final `warsaw_heat.t08_segment_heat` / `t08_stop_heat` when `gs://hackaton-honory-heat/T08/_SUCCESS` exists, and the `t08_*_stub` tables otherwise. Swapping is one build step:

```bash
gcloud builds submit --config views/cloudbuild.yaml --region europe-central2 views
```

The build picks the source on its own (`_HEAT=auto`; `final` or `stub` can be forced). The page shows the source as a badge.

- **Status:** T08 wrote `_SUCCESS` at about 03:10 UTC. The service was rebuilt at 03:14 UTC and now serves the **final** tables (green badge "Dane: T08 (SOLWEIG, końcowe)").
- **Final vs stub:** the length-weighted summaries of the final field match the earlier stub build (the T08 stand-in on the same 27 SOLWEIG tiles) to 0.01 °C at every step.
- **Rerun:** run the command again if T08 or T12 change.

## Decisions

- **Hosting:** one nginx container on Cloud Run with all data baked into the image, rather than a GCS proxy. The bucket is private, the data is static, and nginx gzips GeoJSON and the binary route file.
- **Route heat format:** a custom binary file loaded by deck.gl `PathLayer` with typed arrays, instead of GeoJSON (which would be over 20 MB). Recolouring for a new step is a lookup per vertex in the browser.
- **3D:** deck.gl over MapLibre. Shade comes from the SOLWEIG frames rather than deck.gl's experimental shadow pass, so the 3D page shows the model's shade. Terrain height (`z0`) is ignored: the scene is flat with heights above ground, because the ground frames are 2D.
- **Map frames:** T12's single-image frames are used as they are (MapLibre image source / deck.gl BitmapLayer). T10's XYZ tiles are not used. Each page preloads the frames around the current step.
- **Links:** the paths are `/routes/`, `/shadows3d/` and `/temperature/`, matching T10's `views` list. Each page links back to heat-app ("Planer").

## Open issues

- **heat-app links here:** `HEAT_VIEWS_URL` on heat-app was set by the T10 verify step (commit 7f0de63); `/api/config` returns `heat_views_url` and the three view paths. (Verified 03:20 UTC.)
- **Water pixels have the highest mean UTCI in the per-surface chart** (43.3 °C at 15:00). Water is open and sunlit, and SOLWEIG runs without land cover, so it treats water like any other ground. The chart shows this as it is.
- **2 % of network length stays below 32 °C all day.** These are the 1,546 indoor segments, which T08's `indoor` rule sets to a constant 26 °C.
- **The 2 m shadow frames are about 1.1 MB each**, so playing the desktop 3D view on a slow connection loads frames late. The 4 m frames are used on phones.
- **Screenshots** come from headless Chrome with SwiftShader WebGL. Real GPUs render faster and show the same content.
