# T08: heat per walk segment and per stop

Code for [tasks/T08](../tasks/T08_segment_stop_heat.md). Report: [reports/T08](../reports/T08/README.md).

| File | Role |
|---|---|
| `aggregate.py` | Cloud Run entry point: samples the UTCI field and SOLWEIG shadow at every T03 segment pixel and stop-area pixel (all 33 bands per row block), applies the special-case rules, writes Parquet + BigQuery |
| `report.py` | Local, on the small outputs: shady vs sunny side of Marszałkowska and Aleje Ujazdowskie, maps at 13:00/18:00, rule counts |

## Inputs

| Input | Where |
|---|---|
| UTCI (33 bands, shared 1 m grid or any sub-window of it; float32 or int16 × 0.01) | `SOURCE=t07`: `T07/utci*.tif`; `mosaic`: `T06/district/utci_20260628.tif`; `tiles`: `T06/district/tiles/*/utci.tif`; `pilot`: `T06/cog/utci_solweig.tif` |
| SOLWEIG shadow (1 = sunlit, 0 = shade, 0.03 = under canopy; 1 at night) | T06 mosaic if it exists, else the T06 tiles |
| Segments, segment pixels, stop areas, walkable mask | `T03/network/*.parquet`, `T03/stops/stop_areas.parquet`, `T03/walkable.tif` |
| Time steps, sun elevation | BigQuery `warsaw_heat.t05_forcing_20260628` |

`SOURCE=auto` (default) picks T07 when `T07/_SUCCESS` exists, else the T06 mosaic, else the T06 tiles.

## Outputs

| `OUT` | Parquet | BigQuery |
|---|---|---|
| `stub` (default) | `gs://hackaton-honory-heat/T08/stub/t08_{segment,stop}_heat.parquet` | `warsaw_heat.t08_segment_heat_stub`, `t08_stop_heat_stub` |
| `final` | `gs://hackaton-honory-heat/T08/t08_{segment,stop}_heat.parquet`, then `T08/_SUCCESS` (only for T07 or mosaic sources) | `warsaw_heat.t08_segment_heat`, `t08_stop_heat` |

`checks.json` next to the Parquet holds the source files, rule counts and coverage.

**`t08_segment_heat`** (58,715 segments × 33 steps, clustered by `segment_id`)

| Column | Meaning |
|---|---|
| `segment_id` | T03 id (int64), joins `t03_walk_segments` |
| `step`, `ts_utc`, `label_local` | 0..32, UTC timestamp, `'HH:MM'` CEST |
| `utci_mean`, `utci_p90` | mean and 90th percentile of UTCI over the segment's footprint pixels (°C) |
| `g` | dose rate (K): pixel mean of g(U) with the job's `WEIGHTS` (default 26:1, 32:1, 38:2 = spec §7) |
| `exc26`, `exc32`, `exc38`, `exc46` | pixel means of max(0, U − t). Any weights on these thresholds: g = Σ w_t·exc_t, exactly |
| `shade_share` | pixel mean of 1 − SOLWEIG shadow (tree shade counts 0.97); 1 when the sun is below the horizon |
| `n_pixels` | footprint pixels with a valid UTCI value at this step |
| `rule` | `pixels`, `indoor`, `covered_shade`, `neighbour_fill`, `step_median` |

**`t08_stop_heat`** (360 stops × 33 steps): `stop_id` (GTFS string), `step`, `ts_utc`, `label_local`, `utci` (waiting
spot), `g`, `exc*`, `utci_area_mean` (mean over the whole waiting area), `shade_share` (area), `shelter`, `rule`
(`best_spot`, `shelter`, `neighbourhood`, `access_segments`).

Traversal dose of a segment = `g × length_m / speed` (K·s), with `length_m` from `t03_walk_segments`.

## Run

```bash
IMG=europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t08-heat:2
gcloud builds submit --region europe-central2 --tag $IMG .
gcloud run jobs create t08-heat --region europe-central2 --image $IMG --cpu 4 --memory 16Gi --tasks 1 \
  --max-retries 0 --task-timeout 3600s --set-env-vars SOURCE=tiles,OUT=stub
gcloud run jobs execute t08-heat --region europe-central2 --update-env-vars SOURCE=t07,OUT=final --wait
```

Local test on the pilot (small): `LOCAL=<dir with the T03 parquet + walkable.tif> SOURCE=pilot DRYRUN=<out dir> python aggregate.py`.
Report: `python report.py <dir with segments.parquet, t08_*.parquet, streets.json> ../reports/T08`.
