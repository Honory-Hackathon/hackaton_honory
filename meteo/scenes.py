"""T05 diagnostic: per-AOI QA flag shares for Landsat C2 L2 scenes around the heat day."""
import json

import numpy as np
import rasterio
import requests
from rasterio.features import geometry_mask
from rasterio.warp import transform_bounds, transform_geom
from rasterio.windows import from_bounds
from google.cloud import storage

BKT = storage.Client().bucket("hackaton-honory-heat")
API = "https://planetarycomputer.microsoft.com/api/stac/v1"
aoi = next(f["geometry"] for f in json.loads(BKT.blob("T01/meta/aoi_halo_grid.geojson").download_as_text())["features"]
           if f["properties"]["name"] == "aoi")
aoi_ll = transform_geom("EPSG:2180", "EPSG:4326", aoi)
token = requests.get(f"{API.replace('/stac/v1', '')}/sas/v1/token/landsat-c2-l2", timeout=60).json()["token"]
items = requests.post(f"{API}/search", timeout=60, json={
    "collections": ["landsat-c2-l2"], "intersects": aoi_ll, "datetime": "2026-06-10/2026-07-15", "limit": 50}).json()["features"]
out = []
for it in sorted(items, key=lambda i: i["properties"]["datetime"]):
    with rasterio.open(it["assets"]["qa_pixel"]["href"] + "?" + token) as src:
        g = transform_geom("EPSG:2180", src.crs, aoi)
        b = transform_bounds("EPSG:2180", src.crs, *rasterio.features.bounds(aoi))
        win = from_bounds(*b, src.transform).round_offsets().round_lengths()
        qa = src.read(1, window=win)
        m = ~geometry_mask([g], qa.shape, src.window_transform(win))
    q = qa[m]
    row = {"id": it["id"], "dt": it["properties"]["datetime"][:16], "scene_cc": it["properties"].get("eo:cloud_cover"),
           **{k: round(float(((q >> bit) & 1).mean()), 3) for k, bit in
              [("fill", 0), ("dilated", 1), ("cirrus", 2), ("cloud", 3), ("shadow", 4), ("clear", 6), ("water", 7)]},
           "usable": round(float(((q & 0b11111) == 0).mean()), 3)}
    out.append(row)
    print(json.dumps(row))
BKT.blob("T05/pmwc/scene_screen.json").upload_from_string(json.dumps(out, indent=1))
