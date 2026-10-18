"""T05 Cloud Run task: Landsat surface-heat anomaly `pmwc` (K) on a 10 m grid aligned to the shared 1 m grid.

Primary scene LC09 2026-06-25 (warm, 3 days before the heat day) is cloud/shadow-masked; its gaps are filled with the
fully clear LC08 2026-06-17 anomaly, rescaled by a linear fit on pixels clear in both scenes."""
import json

import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds
from google.cloud import storage

BKT = storage.Client().bucket("hackaton-honory-heat")
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2/items/"
SCENE = "LC09_L2SP_187024_20260625_02_T1"
FILL_SCENE = "LC08_L2SP_187024_20260617_02_T1"
RES = 10.0
QA_BAD = 0b11111  # bits 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow

grid = json.loads(BKT.blob("T01/meta/grid.json").download_as_text())
ge0, gn0, ge1, gn1 = grid["bounds"]
W, H = int((ge1 - ge0) / RES), int((gn1 - gn0) / RES)
dst_tf = from_origin(ge0, gn1, RES, RES)
aoi = next(f["geometry"] for f in json.loads(BKT.blob("T01/meta/aoi_halo_grid.geojson").download_as_text())["features"]
           if f["properties"]["name"] == "aoi")

token = requests.get("https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2", timeout=60).json()["token"]
in_aoi = ~geometry_mask([aoi], (H, W), dst_tf)


def scene_lst(scene_id):
    """Cloud-masked LST (K) and water flag on the 10 m grid, plus the STAC item."""
    item = requests.get(STAC + scene_id, timeout=60).json()
    arrs = {}
    for asset in ("lwir11", "qa_pixel"):
        with rasterio.open(item["assets"][asset]["href"] + "?" + token) as src:
            b = transform_bounds(grid["crs"], src.crs, ge0 - 500, gn0 - 500, ge1 + 500, gn1 + 500)
            win = from_bounds(*b, src.transform).round_offsets().round_lengths()
            arrs[asset] = (src.read(1, window=win), src.window_transform(win), src.crs)
    (dn, tf, crs), (qa, tf_qa, _) = arrs["lwir11"], arrs["qa_pixel"]
    assert tf == tf_qa and dn.shape == qa.shape
    st = item["assets"]["lwir11"]["raster:bands"][0]
    lst_native = dn.astype("float32") * st["scale"] + st["offset"]
    lst_native[(dn == 0) | ((qa & QA_BAD) != 0)] = np.nan
    lst = np.full((H, W), np.nan, "float32")
    reproject(lst_native, lst, src_transform=tf, src_crs=crs, src_nodata=np.nan, dst_transform=dst_tf,
              dst_crs=grid["crs"], dst_nodata=np.nan, resampling=Resampling.bilinear)
    water = np.zeros((H, W), "uint8")
    reproject(((qa >> 7) & 1).astype("uint8"), water, src_transform=tf, src_crs=crs, dst_transform=dst_tf,
              dst_crs=grid["crs"], resampling=Resampling.nearest)
    return lst, water, item, st


lst, water, item, st = scene_lst(SCENE)
lst_fill, _, item_fill, _ = scene_lst(FILL_SCENE)
ref = float(np.nanmedian(lst[in_aoi]))
ref_fill = float(np.nanmedian(lst_fill[in_aoi]))
pmwc_primary = lst - ref
anom_fill = lst_fill - ref_fill
both = np.isfinite(pmwc_primary) & np.isfinite(anom_fill)
slope, intercept = np.polyfit(anom_fill[both], pmwc_primary[both], 1)
r = float(np.corrcoef(anom_fill[both], pmwc_primary[both])[0, 1])
resid_sd = float(np.std(pmwc_primary[both] - (slope * anom_fill[both] + intercept)))
gap = ~np.isfinite(pmwc_primary) & np.isfinite(anom_fill)
pmwc = pmwc_primary.copy()
pmwc[gap] = slope * anom_fill[gap] + intercept
# Re-reference so the gap-filled field has an AOI median of exactly 0 (the 06-25 clear pixels are not a random sample).
shift = float(np.nanmedian(pmwc[in_aoi]))
pmwc -= shift
pmwc_primary -= shift
ref += shift
source = np.zeros((H, W), "uint8")
source[np.isfinite(pmwc_primary)] = 1
source[gap] = 2

f32 = dict(driver="COG", width=W, height=H, count=1, dtype="float32", crs=grid["crs"], transform=dst_tf,
           nodata=np.nan, compress="deflate", predictor=3)
u8 = {**f32, "dtype": "uint8", "nodata": 0, "predictor": 2}
for name, arr, prof in [("pmwc_10m", pmwc, f32), ("pmwc_source_10m", source, u8),
                        ("pmwc_20260625_masked_10m", pmwc_primary, f32), ("lst_20260625_masked_10m", lst, f32)]:
    with rasterio.open(f"/tmp/{name}.tif", "w", **prof) as dst:
        dst.write(arr, 1)
    BKT.blob(f"T05/pmwc/{name}.tif").upload_from_filename(f"/tmp/{name}.tif")

a = pmwc[in_aoi]
land = in_aoi & (water == 0)
meta = {
    "scene": SCENE, "datetime_utc": item["properties"]["datetime"],
    "scene_cloud_cover_pct": item["properties"].get("eo:cloud_cover"),
    "sun_elevation_deg": item["properties"].get("view:sun_elevation"),
    "lwir11_scale_offset": [st["scale"], st["offset"]],
    "grid": {"crs": grid["crs"], "res": RES, "bounds": grid["bounds"], "width": W, "height": H},
    "reference_lst_k_aoi_median": round(ref, 2), "rereference_shift_k": round(shift, 3),
    "fill_scene": FILL_SCENE, "fill_datetime_utc": item_fill["properties"]["datetime"],
    "reference_lst_k_aoi_median_fill_scene": round(ref_fill, 2),
    "fill_fit": {"pmwc_0625 = slope * anom_0617 + intercept": [round(float(slope), 3), round(float(intercept), 3)],
                 "pearson_r": round(r, 3), "residual_sd_k": round(resid_sd, 2), "n_pixels_10m": int(both.sum())},
    "aoi_share_by_source": {"0625": round(float((source[in_aoi] == 1).mean()), 4),
                            "0617_rescaled": round(float((source[in_aoi] == 2).mean()), 4),
                            "none": round(float((source[in_aoi] == 0).mean()), 4)},
    "aoi_clear_fraction_0625": round(float(np.isfinite(pmwc_primary[in_aoi]).mean()), 4),
    "grid_filled_fraction": round(float(np.isfinite(pmwc).mean()), 4),
    "aoi_water_fraction": round(float((water[in_aoi] == 1).mean()), 4),
    "aoi_pmwc_percentiles_k": {p: round(float(np.nanpercentile(a, p)), 2) for p in [1, 5, 25, 50, 75, 95, 99]},
    "aoi_land_pmwc_percentiles_k": {p: round(float(np.nanpercentile(pmwc[land], p)), 2) for p in [1, 5, 50, 95, 99]},
    "aoi_lst_min_max_k": [round(float(np.nanmin(lst[in_aoi])), 2), round(float(np.nanmax(lst[in_aoi])), 2)],
}
BKT.blob("T05/pmwc/meta.json").upload_from_string(json.dumps(meta, indent=1))
print(json.dumps(meta, indent=1))
