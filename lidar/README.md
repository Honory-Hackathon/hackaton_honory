# lidar: T01 LiDAR → 1 m geometry

Method and results: root [`README.md`](../README.md). Outputs: `gs://hackaton-honory-heat/T01/output/`.

The Cloud Run jobs run the copy in `gs://hackaton-honory-heat/T01/code/`; this folder mirrors it. After changing a script here, copy it there before rerunning.

| File | Role |
|---|---|
| `index_sheets.py` | GUGiK LiDAR sheet index (WFS `Skorowidze`) → sheet lists per flight, `meta/manifest.tsv` |
| `download.sh` | Cloud Run task: streams its share of LAZ sheets from GUGiK into `T01/laz/{2023,2025}/` (skips files already there) |
| `process_tile.py` | Cloud Run task: one 1 km tile (+100 m buffer) → all layers as `T01/tiles/{e}_{n}.npz` |
| `mosaic.py` | Pastes tile centres into the COGs, `summary.json`, previews, tree-inventory comparison |
| `Dockerfile` | Image `europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t01-lidar:2`; downloads `T01/code/$SCRIPT` at start |

## Run

```bash
# 72 tiles = 4 regional jobs × 18 tasks, TILE_BASE 0/18/36/54 set on each job
# (Cloud Run quota is 20 vCPU per region)
for r in europe-central2 europe-west3 europe-west4 europe-north1; do
  gcloud run jobs execute t01-tiles --region $r
done
gcloud run jobs execute t01-mosaic --region europe-central2
```

Tile tasks use 2 vCPU / 8 GiB and take 2–6 min each; the mosaic uses 8 vCPU / 32 GiB, ~4 min.
