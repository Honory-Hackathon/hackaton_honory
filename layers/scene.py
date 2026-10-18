"""3D scene assets: extruded building blocks (LiDAR), trees (treetops + crowns), sun/cloud per step.

MODE=scene
"""
import json
import os

import numpy as np
import pandas as pd
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.transform import from_origin
from scipy import ndimage as ndi
from shapely.geometry import shape
from shapely.ops import transform as stransform

from common import (OUT_PREFIX, STEP_LABELS, STEP_UTC, STEPS, aoi_polygon, fetch, log, upload_json, web_window)
from frames import read_grid_window

BIN = 3.0  # m height steps of building blocks
MIN_AREA = 6.0


def _to4326():
    t = Transformer.from_crs(2180, 4326, always_xy=True)
    return lambda x, y, z=None: t.transform(x, y)


def _round(g, nd=6):
    def rc(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [rc(x) for x in c]
    g["coordinates"] = rc(g["coordinates"])
    return g


def buildings(win):
    log("buildings")
    bm = read_grid_window("T01/output/building_mask.tif", win) == 1
    dsm = read_grid_window("T01/output/dsm.tif", win).astype(np.float32)
    dtm = read_grid_window("T01/output/dtm.tif", win).astype(np.float32)
    nd = dsm - dtm
    nd[~bm] = np.nan
    nd = ndi.median_filter(np.nan_to_num(nd, nan=0), size=3)
    hb = np.clip(np.round(nd / BIN), 1, 200).astype(np.int16)
    hb[~bm] = 0
    # drop specks
    hb = ndi.median_filter(hb, size=3)
    hb[~bm] = 0
    lab, nlab = ndi.label(bm)
    zg = ndi.minimum(np.nan_to_num(dtm, nan=1e4), lab, index=np.arange(1, nlab + 1)) if nlab else []
    e0, n0, e1, n1 = win
    t = from_origin(e0, n1, 1, 1)
    f = _to4326()
    aoi = aoi_polygon().buffer(300)
    feats = []
    combo = (lab.astype(np.int64) * 256 + hb).astype(np.int32)  # unique per building x height bin
    combo[hb == 0] = 0
    for geom, val in shapes(combo, mask=combo > 0, transform=t, connectivity=4):
        g = shape(geom)
        if g.area < MIN_AREA or not g.intersects(aoi):
            continue
        b = int(val) // 256
        h = (int(val) % 256) * BIN
        g = g.simplify(0.75, preserve_topology=True)
        if g.is_empty:
            continue
        z0 = float(zg[b - 1]) if b - 1 < len(zg) else None
        gj = _round(json.loads(json.dumps(stransform(f, g).__geo_interface__)))
        feats.append({"type": "Feature", "geometry": gj,
                      "properties": {"b": b, "h": h, "z0": None if z0 is None or z0 > 9e3 else round(z0, 1)}})
    log("building blocks", len(feats))
    fc = {"type": "FeatureCollection", "name": "t12_buildings",
          "description": "Building blocks from LiDAR 2025 (T01 dsm - dtm inside building_mask), heights in "
                         f"{BIN:.0f} m steps; h = height above ground (m), z0 = lowest ground under the building "
                         "(m EVRF2007), b = building id. Extrude h on a flat map.", "features": feats}
    upload_json(fc, f"{OUT_PREFIX}/scene/buildings.geojson", gzip_encode=True)
    hs = np.array([x["properties"]["h"] for x in feats])
    return {"path": "scene/buildings.geojson", "count": len(feats), "height_bin_m": BIN,
            "properties": {"h": "height above ground, m (top of 3 m bin)", "z0": "ground elevation, m EVRF2007",
                           "b": "building id (connected building_mask component)"},
            "h_p50_p95_max": [float(np.percentile(hs, 50)), float(np.percentile(hs, 95)), float(hs.max())],
            "content_encoding": "gzip (transparent over HTTP)"}


def trees(win):
    log("trees")
    from skimage.segmentation import watershed
    ch = read_grid_window("T01/output/canopy_height.tif", win).astype(np.float32)
    cb = read_grid_window("T01/output/crown_base_height.tif", win).astype(np.float32)
    dtm = read_grid_window("T01/output/dtm.tif", win).astype(np.float32)
    canopy = np.nan_to_num(ch) >= 2.0
    sm = ndi.gaussian_filter(np.nan_to_num(ch), 1.0)
    mx = ndi.maximum_filter(sm, size=5)
    tops = (sm == mx) & (sm >= 3.0) & canopy
    markers, nt = ndi.label(tops)
    log("treetops", nt)
    seg = watershed(-sm, markers, mask=canopy)
    idx = np.arange(1, nt + 1)
    area = ndi.sum(np.ones_like(sm), seg, idx)
    hmax = ndi.maximum(np.nan_to_num(ch), seg, idx)
    base = ndi.mean(np.nan_to_num(cb, nan=0), seg, idx)
    zg = ndi.mean(np.nan_to_num(dtm, nan=0), seg, idx)
    com = ndi.center_of_mass(tops, markers, idx)
    e0, n0, e1, n1 = win
    rr = np.array([c[0] for c in com])
    cc = np.array([c[1] for c in com])
    ee = e0 + cc + 0.5
    nn = n1 - rr - 0.5
    lon, lat = Transformer.from_crs(2180, 4326, always_xy=True).transform(ee, nn)
    from shapely import contains_xy
    aoi = aoi_polygon().buffer(300)
    inside = contains_xy(aoi, ee, nn)
    r = np.sqrt(area / np.pi)
    feats = []
    for i in np.where(inside & (area >= 3))[0]:
        b = float(base[i]) if base[i] > 0 else 0.25 * float(hmax[i])
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon[i], 6), round(lat[i], 6)]},
                      "properties": {"h": round(float(hmax[i]), 1), "base": round(min(b, float(hmax[i]) - 1), 1),
                                     "r": round(float(r[i]), 1), "z0": round(float(zg[i]), 1)}})
    log("trees", len(feats))
    fc = {"type": "FeatureCollection", "name": "t12_trees",
          "description": "Trees from LiDAR 2023 leaf-on canopy height (T01): treetops = local maxima (5 m window, "
                         ">= 3 m) of the smoothed canopy model, crowns by watershed. h = top height above ground (m), "
                         "base = crown base (m, T01 crown_base_height median, fallback 0.25 h), r = crown radius "
                         "(m, from crown area), z0 = ground (m EVRF2007).", "features": feats}
    upload_json(fc, f"{OUT_PREFIX}/scene/trees.geojson", gzip_encode=True)
    return {"path": "scene/trees.geojson", "count": len(feats),
            "properties": {"h": "top height above ground, m", "base": "crown base height, m", "r": "crown radius, m",
                           "z0": "ground elevation, m EVRF2007"}, "content_encoding": "gzip (transparent over HTTP)"}


def sun():
    fz = pd.read_csv(fetch("T05/forcing/forcing_20260628.csv"))
    rows = []
    for i in range(STEPS):
        r = fz.iloc[i]
        az, el = float(r.sun_azim_deg), float(r.sun_elev_deg)
        a, e = np.radians(az), np.radians(el)
        rows.append({"step": i, "label_local": STEP_LABELS[i], "ts_utc": STEP_UTC[i],
                     "sun_azimuth_deg": round(az, 2), "sun_elevation_deg": round(el, 2), "sun_up": el > 0,
                     "sun_vector_enu": [round(np.sin(a) * np.cos(e), 4), round(np.cos(a) * np.cos(e), 4),
                                        round(np.sin(e), 4)],
                     "cloud_okta": None if pd.isna(r.cloud_okta) else float(r.cloud_okta),
                     "cloud_era5_pct": None if pd.isna(r.cloud_era5_pct) else float(r.cloud_era5_pct),
                     "ghi_wm2": round(float(r.ghi_wm2), 1), "dni_wm2": round(float(r.dni_wm2), 1),
                     "dhi_wm2": round(float(r.dhi_wm2), 1), "ta_c": round(float(r.ta_c), 2),
                     "shadow_length_per_m_height": round(1 / np.tan(e), 2) if el > 0.5 else None})
    doc = {"description": "Sun position (pvlib NREL SPA, T05) and cloud cover (IMGW okta, ERA5 %) per step on "
                          "2026-06-28. sun_vector_enu points from the ground to the sun: x east, y north, z up. "
                          "azimuth clockwise from north.", "location_lonlat": [21.012, 52.228], "steps": rows}
    upload_json(doc, f"{OUT_PREFIX}/scene/sun_cloud.json")
    return {"path": "scene/sun_cloud.json", "steps": STEPS}


def run():
    win = web_window()
    parts = {"sun_cloud": sun()}
    parts["trees"] = trees(win)
    parts["buildings"] = buildings(win)
    upload_json(parts, f"{OUT_PREFIX}/parts/scene.json")
    log("scene done")


if __name__ == "__main__":
    run()
