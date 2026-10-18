# T06: SOLWEIG on Śródmieście (pilot comparison + district production run)

Code for [tasks/T06](../tasks/T06_solweig_reference.md). Report: [reports/T06](../reports/T06/README.md).

| File | Role |
|---|---|
| `common.py` | Pilot bbox (EPSG:2180), time steps, forcing (T05 + ERA5 spin-up), T01 tile mosaic, T05 `pmwc`, COG writer |
| `simple_model.py` | Spec §13 `compute_inputs_for_utci` verbatim, a vectorised copy (checked against it), the tunable form |
| `run.py` | Cloud Run entry point, `MODE` = `pilot`, `analyze`, `bench`, `district`, `tile`, `mosaic` (see its docstring) |
| `analyze.py` | Pilot: model variants, fits with a spatial hold-out, UTCI (`pythermalcomfort`), heat dose, figures, COGs |

SOLWEIG implementation: [`solweig`](https://github.com/UMEP-dev/solweig) 0.1.0b96 (UMEP-dev Rust re-implementation of UMEP
SOLWEIG), CPU only (`solweig.disable_gpu()`), isotropic sky, defaults otherwise. The image needs `libexpat1` (rasterio's
bundled GDAL); the Dockerfile's import check fails the build otherwise.

## Image

```bash
IMG=europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t06-solweig:9
gcloud builds submit --region europe-central2 --tag $IMG .
```

Cloud Run limits in `hackaton-honory`: 5 regions per project (europe-central2, -west1, -west4, -west3, -north1 are
initialised), 20 vCPU and 40 GiB per region. europe-central2 had no capacity for 8-vCPU tasks and preempts 4-vCPU ones.

## District run (production)

27 tiles = the 1 km T01 tiles that intersect the Śródmieście AOI. One task per tile (`TILES` = comma list, picked by task
index). Halo 500 m, or 1000 m when a building over 100 m stands within 1 km (18 tiles). With `SVF_MARGIN=300` the sky view
factor is computed on core + 300 m only and padded into the full-halo run (shadow casters keep the full halo).
Memory: 1000 m-halo tiles (9 Mpx) peak at ~19 GB RSS plus `/tmp` (RAM on Cloud Run) → 32 GiB; 500 m tiles ~9 GB → 16 GiB.

```bash
gcloud run jobs create t06-tile --region europe-west1 --image $IMG --cpu 8 --memory 32Gi --tasks 5 --parallelism 1 \
  --max-retries 1 --task-timeout 21600s --set-env-vars "^@^MODE=tile@SVF_MARGIN=300@TILES=637000_486000,636000_487000,..."
gcloud run jobs execute t06-tile --region europe-west1 --async
```

Outputs per tile: `gs://hackaton-honory-heat/T06/district/tiles/{e0}_{n0}/{tmrt,shadow,utci}.tif` (1 km core, 33 bands,
band description = local time) and `timing.json`.

### Mosaic (after all 27 `timing.json` exist)

```bash
gcloud run jobs create t06-mosaic --region europe-west3 --image $IMG --cpu 8 --memory 32Gi --tasks 1 --max-retries 1 \
  --task-timeout 7200s --set-env-vars MODE=mosaic
gcloud run jobs execute t06-mosaic --region europe-west3 --wait
```

Writes `gs://hackaton-honory-heat/T06/district/{tmrt,shadow,utci}_20260628.tif` (COG, EPSG:2180, 1 m, bbox of the tile
cores E 635000–641000 / N 484000–491000, 33 bands 06:00–22:00 CEST, NaN outside the tiles) and `tiles_summary.json`.
It mosaics whatever tiles exist, so check the count first:
`gcloud storage ls "gs://hackaton-honory-heat/T06/district/tiles/*/timing.json" | wc -l` → 27.

## Pilot comparison and benchmarks

```bash
gcloud run jobs create t06-solweig --region europe-west1 --image $IMG --cpu 8 --memory 32Gi --tasks 1 \
  --max-retries 0 --task-timeout 86400s --set-env-vars MODE=pilot      # MODE=analyze,REZONE=1 redoes the comparison only
gcloud run jobs create t06-bench --region europe-west4 --image $IMG --cpu 8 --memory 16Gi --tasks 7 \
  --parallelism 2 --max-retries 0 --task-timeout 86400s --set-env-vars MODE=bench,TASK_BASE=1
```

Outputs: `gs://hackaton-honory-heat/T06/{cog,results,timings,forcing,work}/`.
