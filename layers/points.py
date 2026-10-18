"""Cool and water points: OSM extraction, shade/UTCI per step at each point, GeoJSON/GeoParquet/BigQuery.

MODE=points: extract from OSM PBF (T03 input), sample T02 shade shares, write outputs.
enrich_with_solweig(): called by frames (SOURCE=solweig) to add SOLWEIG shadow/UTCI/Tmrt per step.
"""
import json
import os
import subprocess

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.windows import Window
from shapely.geometry import mapping, shape

from common import (GE0, GN1, GS, OUT_PREFIX, STEP_LABELS, STEPS, WORK, aoi_polygon, exists, fetch, gcs, log,
                    upload, upload_json, web_window)

PBF = "T03/input/mazowieckie-2026-10-16.osm.pbf"
BUFFER = 300  # m around AOI
PARQUET = f"{OUT_PREFIX}/points/cool_points.parquet"

COOL_INDOOR = {("amenity", "place_of_worship"): "church_or_temple", ("amenity", "library"): "library",
               ("shop", "mall"): "shopping_mall", ("tourism", "museum"): "museum",
               ("amenity", "community_centre"): "community_centre"}


def classify(tags):
    """Return (category, subtype) or None."""
    g = tags.get
    name = (g("name") or "").lower()
    if g("amenity") in ("drinking_water", "water_point") or (g("man_made") == "water_tap" and g("drinking_water") != "no") \
            or g("drinking_water") == "yes" and g("amenity") not in ("toilets", "fountain", "restaurant", "cafe"):
        op = (g("operator") or "") + " " + name
        sub = "mpwik_fountain" if ("mpwik" in op.lower() or "poide" in op.lower() or "zdrój" in op.lower()) else (
            g("amenity") or g("man_made") or "drinking_water")
        return "drinking_water", sub
    if "kurtyn" in name or g("man_made") == "water_curtain" or g("leisure") == "splash_pad" \
            or g("playground") == "splash_pad" or g("amenity") == "shower" and g("access") != "private":
        return "water_curtain_or_splash", g("leisure") or g("playground") or g("amenity") or "named_curtain"
    if g("amenity") == "fountain" or g("man_made") == "fountain":
        return "fountain", g("fountain") or "decorative"
    if g("amenity") == "toilets" and g("access") not in ("private", "customers", "no"):
        return "toilet", g("fee") == "yes" and "paid" or "free_or_unknown"
    if g("amenity") == "bench" or g("leisure") == "picnic_table":
        return "bench", g("backrest") == "yes" and "backrest" or "bench"
    if g("amenity") == "shelter":
        return "shelter", g("shelter_type") or "shelter"
    if g("leisure") in ("park", "garden") and g("access") not in ("private", "no"):
        return "park", g("leisure")
    if g("natural") == "water" or g("water") or g("waterway") in ("riverbank",) or g("leisure") == "swimming_pool" \
            and g("access") in ("yes", "public"):
        return "water_body", g("water") or g("leisure") or "water"
    for (k, v), sub in COOL_INDOOR.items():
        if g(k) == v:
            return "cool_indoor", sub
    return None


def extract():
    import osmium
    win = web_window()
    to4326 = Transformer.from_crs(2180, 4326, always_xy=True)
    w, s = to4326.transform(win[0] - 200, win[1] - 200)
    e, n = to4326.transform(win[2] + 200, win[3] + 200)
    src = fetch(PBF)
    small = os.path.join(WORK, "aoi.osm.pbf")
    if not os.path.exists(small):
        subprocess.run(["osmium", "extract", "-b", f"{w},{s},{e},{n}", "-s", "smart", "--overwrite", "-o", small, src],
                       check=True)
    log("extract done", os.path.getsize(small))
    rows = []
    wkb = osmium.geom.WKBFactory()
    from shapely import wkb as swkb

    class H(osmium.SimpleHandler):
        def node(self, nd):
            c = classify(dict(nd.tags))
            if c and nd.location.valid():
                rows.append(("n", nd.id, c, dict(nd.tags), swkb.loads(wkb.create_point(nd), hex=True)))

        def way(self, wy):
            if wy.is_closed():
                return  # areas handled below
            c = classify(dict(wy.tags))
            if c and c[0] in ("bench", "fountain", "drinking_water", "water_curtain_or_splash", "shelter", "toilet"):
                try:
                    rows.append(("w", wy.id, c, dict(wy.tags), swkb.loads(wkb.create_linestring(wy), hex=True)))
                except Exception:
                    pass

        def area(self, a):
            c = classify(dict(a.tags))
            if c:
                try:
                    g = swkb.loads(wkb.create_multipolygon(a), hex=True)
                except Exception:
                    return
                rows.append(("w" if a.from_way() else "r", a.orig_id(), c, dict(a.tags), g))

    H().apply_file(small, locations=True, idx="flex_mem")
    log("osm features", len(rows))
    import geopandas as gpd
    df = gpd.GeoDataFrame({"osm_type": [r[0] for r in rows], "osm_id": [r[1] for r in rows],
                           "category": [r[2][0] for r in rows], "subtype": [str(r[2][1]) for r in rows],
                           "name": [r[3].get("name") for r in rows], "operator": [r[3].get("operator") for r in rows],
                           "opening_hours": [r[3].get("opening_hours") for r in rows],
                           "tags": [json.dumps(r[3], ensure_ascii=False) for r in rows]},
                          geometry=[r[4] for r in rows], crs="EPSG:4326").to_crs(2180)
    aoi = aoi_polygon()
    zone = aoi.buffer(BUFFER)
    df = df[df.intersects(zone)].copy()
    df["in_aoi"] = df.intersects(aoi)
    df["area_m2"] = df.geometry.area.round(1)
    # drop tiny decorative water and huge non-public water
    df = df[~((df.category == "water_body") & (df.area_m2 < 20))]
    df = df[~((df.category == "park") & (df.area_m2 < 300))]
    rep = df.geometry.representative_point()
    df["e"] = rep.x.round(2)
    df["n"] = rep.y.round(2)
    lon, lat = Transformer.from_crs(2180, 4326, always_xy=True).transform(df.e.values, df.n.values)
    df["lon"] = np.round(lon, 7)
    df["lat"] = np.round(lat, 7)
    df["point_id"] = df.osm_type + df.osm_id.astype(str)
    df["source"] = "OpenStreetMap (Geofabrik Mazowieckie 2026-10-16)"
    df = df.drop_duplicates("point_id")
    log(df.category.value_counts().to_dict())
    return df


def sample_stack(path_fn, e, n, steps=STEPS, radius=1, scale=1.0, nodata=None, bands=None):
    """3x3 mean per point for each band from a full-grid COG."""
    out = np.full((len(e), steps), np.nan, np.float32)
    col = (np.asarray(e) - GE0).astype(int)
    row = (GN1 - np.asarray(n)).astype(int)
    path = fetch(path_fn)
    with rasterio.open(path) as s:
        r0, r1 = max(row.min() - 2, 0), min(row.max() + 3, s.height)
        c0, c1 = max(col.min() - 2, 0), min(col.max() + 3, s.width)
        for i in range(steps):
            a = s.read(i + 1, window=Window(c0, r0, c1 - c0, r1 - r0)).astype(np.float32)
            if nodata is not None:
                a[a == nodata] = np.nan
            acc = np.zeros(len(e), np.float32)
            cnt = np.zeros(len(e), np.float32)
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    rr = np.clip(row - r0 + dr, 0, a.shape[0] - 1)
                    cc = np.clip(col - c0 + dc, 0, a.shape[1] - 1)
                    v = a[rr, cc]
                    ok = np.isfinite(v)
                    acc[ok] += v[ok]
                    cnt[ok] += 1
            with np.errstate(invalid="ignore"):
                out[:, i] = np.where(cnt > 0, acc / cnt, np.nan) * scale
    return out


def write_outputs(df, note, areas=True):
    import geopandas as gpd
    os.makedirs(os.path.join(WORK, "pts"), exist_ok=True)
    pts = gpd.GeoDataFrame(df.drop(columns="geometry"),
                           geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
    # list columns -> rounded lists
    p = os.path.join(WORK, "pts", "cool_points.parquet")
    pts.to_parquet(p)
    upload(p, PARQUET, "application/octet-stream")
    # GeoJSON (browser): compact properties, arrays rounded
    keep = ["point_id", "category", "subtype", "name", "operator", "opening_hours", "in_aoi", "area_m2",
            "source", "solweig_covered", "shade_share_t02", "shade_share", "utci", "tmrt", "shaded_rest",
            "shade_hours_10_18"]
    feats = []
    for r in pts.itertuples():
        pr = {}
        for k in keep:
            if hasattr(r, k):
                v = getattr(r, k)
                if isinstance(v, (list, np.ndarray)):
                    v = [None if (x is None or not np.isfinite(x)) else round(float(x), 2) for x in v]
                elif isinstance(v, (np.bool_, bool)):
                    v = bool(v)
                elif isinstance(v, (np.floating, float)):
                    v = None if not np.isfinite(v) else round(float(v), 2)
                elif v is None or (isinstance(v, float) and np.isnan(v)):
                    v = None
                pr[k] = v
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                      "properties": pr})
    fc = {"type": "FeatureCollection", "name": "t12_cool_points", "steps": STEP_LABELS, "note": note,
          "features": feats}
    upload_json(fc, f"{OUT_PREFIX}/points/cool_points.geojson", gzip_encode=True)
    if areas:
        write_areas(df)
    to_bq(pts)


def write_areas(df):
    import geopandas as gpd
    # areas (parks, water) as simplified polygons for context
    ar = df[df.category.isin(["park", "water_body", "cool_indoor"]) & (df.geometry.geom_type.isin(
        ["Polygon", "MultiPolygon"]))].copy()
    ar["geometry"] = ar.geometry.simplify(1.0)
    ar = gpd.GeoDataFrame(ar[["point_id", "category", "subtype", "name", "area_m2", "in_aoi"]],
                          geometry=ar.geometry, crs=2180).to_crs(4326)
    afc = json.loads(ar.to_json(drop_id=True))
    for f in afc["features"]:
        f["geometry"] = _round_geom(f["geometry"])
    upload_json(afc, f"{OUT_PREFIX}/points/cool_areas.geojson", gzip_encode=True)


def _round_geom(g, nd=6):
    def rc(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [rc(x) for x in c]
    g["coordinates"] = rc(g["coordinates"])
    return g


def to_bq(pts):
    from google.cloud import bigquery
    bq = bigquery.Client(project="hackaton-honory")
    t = pts.drop(columns="geometry").copy()
    t["geog"] = pts.geometry.to_wkt()
    for c in ("shade_share_t02", "shade_share", "utci", "tmrt"):
        if c in t:
            t[c] = t[c].apply(lambda v: [float(x) if np.isfinite(x) else None for x in v] if v is not None else None)
            t[c] = t[c].apply(lambda v: json.dumps(v) if v is not None else None)
    stage = "hackaton-honory.warsaw_heat.t12_cool_points_stage"
    job = bq.load_table_from_dataframe(t, stage, job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
    job.result()
    bq.query(f"CREATE OR REPLACE TABLE `hackaton-honory.warsaw_heat.t12_cool_points` AS "
             f"SELECT * EXCEPT(geog), ST_GEOGFROMTEXT(geog) AS geog FROM `{stage}`").result()
    bq.delete_table(stage, not_found_ok=True)
    log("bq rows", job.output_rows)


def run():
    df = extract()
    log("sampling T02 shade")
    f_sun = sample_stack("T02/f_sun.tif", df.e.values, df.n.values, nodata=255, scale=1 / 250)
    df["shade_share_t02"] = list(np.round(1 - f_sun, 3))
    df["shade_share"] = df["shade_share_t02"]
    flag(df)
    df.to_parquet(os.path.join(WORK, "pts_base.parquet"))
    write_outputs(df, "shade_share from T02 shares (surface receiver); utci/tmrt pending SOLWEIG district run")
    upload_json({"points": {"count": int(len(df)), "by_category": df.category.value_counts().to_dict()}},
                f"{OUT_PREFIX}/parts/points.json")


def flag(df):
    sh = np.stack(df["shade_share"].values)
    day = slice(8, 25)  # 10:00..18:00 local
    df["shade_hours_10_18"] = np.round(np.nansum(sh[:, day] >= 0.5, axis=1) * 0.5, 1)
    df["shaded_rest"] = (df.category.isin(["bench", "shelter"])) & (np.nanmean(sh[:, 12:19], axis=1) >= 0.5)


def enrich_with_solweig():
    import geopandas as gpd
    from frames import list_tiles
    if not exists(PARQUET):
        raise RuntimeError("no points yet")
    base = pd.DataFrame(gpd.read_parquet(fetch(PARQUET)).drop(columns="geometry"))
    df = gpd.GeoDataFrame(base, geometry=gpd.points_from_xy(base.e, base.n), crs="EPSG:2180")
    tiles = list_tiles()
    res = {v: np.full((len(df), STEPS), np.nan, np.float32) for v in ("shadow", "utci", "tmrt")}
    e = df.e.values
    n = df.n.values
    for key in sorted(tiles):
        te, tn = map(int, key.split("_"))
        sel = np.where((e >= te + 1) & (e < te + 999) & (n >= tn + 1) & (n < tn + 999))[0]
        if not len(sel):
            continue
        for v in res:
            lp = fetch(f"T06/district/tiles/{key}/{v}.tif")
            with rasterio.open(lp) as s:
                a = s.read().astype(np.float32)
            os.remove(lp)
            rr = (tn + 1000 - n[sel]).astype(int)
            cc = (e[sel] - te).astype(int)
            acc = np.zeros((len(sel), STEPS), np.float32)
            cnt = np.zeros((len(sel), STEPS), np.float32)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    x = a[:, np.clip(rr + dr, 0, 999), np.clip(cc + dc, 0, 999)].T
                    ok = np.isfinite(x)
                    acc[ok] += x[ok]
                    cnt[ok] += 1
            with np.errstate(invalid="ignore"):
                res[v][sel] = np.where(cnt > 0, acc / cnt, np.nan)
    fz = pd.read_csv(fetch("T05/forcing/forcing_20260628.csv"))
    up = fz["sun_elev_deg"].values > 0
    shade = 1 - res["shadow"]
    shade[:, ~up] = np.nan
    have = np.isfinite(res["utci"]).any(axis=1)
    old = np.stack(df["shade_share_t02"].values)
    shade = np.where(have[:, None], shade, old)
    df["shade_share"] = list(np.round(shade, 3))
    df["utci"] = list(np.round(res["utci"], 2))
    df["tmrt"] = list(np.round(res["tmrt"], 2))
    df["solweig_covered"] = have
    flag(df)
    df = df.to_crs(4326)
    write_outputs(df, areas=False, note=f"shade_share/utci/tmrt from SOLWEIG district tiles ({len(tiles)} tiles; points outside "
                      f"tiles keep T02 shade, utci null); shade_share_t02 from T02")
    upload_json({"points": {"count": int(len(df)), "by_category": df.category.value_counts().to_dict(),
                            "solweig_covered": int(have.sum()), "tiles": len(tiles)}},
                f"{OUT_PREFIX}/parts/points.json")
    log("enriched", int(have.sum()), "of", len(df))


if __name__ == "__main__":
    run()
