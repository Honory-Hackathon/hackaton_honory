# layers/ (T12): web-ready visual layers

Builds everything in `gs://hackaton-honory-heat/T12/` (see `T12/manifest.json`). Method and results: root [`README.md`](../README.md).
One image, `europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t12-layers`, run as Cloud Run Jobs:

| Job args | What |
|---|---|
| `points` | OSM cool/water points (osmium extract + pyosmium), T02 shade per step at each point → GeoJSON, GeoParquet, BigQuery `warsaw_heat.t12_cool_points` |
| `scene` | LiDAR building blocks (3 m height steps), treetops with crown radius/base, sun vector + clouds per step |
| `frames,t02` | Shadow frames from T02 shade shares (2 m, EPSG:3857 PNG) |
| `frames,static` | Albedo (Sentinel-2 2026-06-27) and Landsat LST 11:25 frames + 10 m COGs |
| `frames,solweig` | From T06 district tiles: shadow (2 m), UTCI and Tmrt (4 m) frames, grey value frames, per-surface series, statics; then re-samples points with SOLWEIG |
| `manifest` | Rebuild `T12/manifest.json` from `T12/parts/*.json` (every mode does this at the end) |

Build: `gcloud builds submit --tag europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t12-layers:vN layers/`.
