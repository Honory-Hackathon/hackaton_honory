"""Build the walkable mask, walk network and stop waiting areas for the AOI.

usage: python -m walk.run IN_DIR OUT_DIR

IN_DIR (filled by walk/fetch.sh): halo.osm.pbf, aoi_halo_grid.geojson, grid.json, gtfs_stops.csv.
OUT_DIR: walkable.tif, segments.parquet, nodes.parquet, segment_pixels.parquet,
stop_areas.parquet, stats.json, preview_*.png.
"""

import argparse
import json
import os
import time
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
import rasterio.shutil
import shapely
from shapely.geometry import shape

from . import mask as wmask
from . import network, osm, stops

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLOURS = {0: (1, 1, 1), 1: (0.55, 0.55, 0.55), 2: (0.85, 0.15, 0.15), 3: (0.95, 0.75, 0.30),
           4: (0.30, 0.65, 0.30), 5: (0.20, 0.40, 0.90), 6: (0.55, 0.30, 0.65), 7: (0.0, 0.0, 0.0),
           8: (0.80, 0.80, 0.70)}
ZOOMS = {"plac_zbawiciela": (52.21971, 21.01864), "centralna": (52.22887, 21.00800)}


def load_aoi(path):
    fc = json.load(open(path))
    return next(shape(f["geometry"]) for f in fc["features"] if f["properties"].get("name") == "aoi")


def write_cog(arr, path, transform):
    tmp = str(path) + ".tmp.tif"
    with rasterio.open(tmp, "w", driver="GTiff", dtype="uint8", count=1, width=arr.shape[1], height=arr.shape[0],
                       crs=osm.CRS, transform=transform, tiled=True, blockxsize=512, blockysize=512,
                       compress="DEFLATE") as dst:
        dst.write(arr, 1)
        dst.update_tags(**{f"category_{k}": v for k, v in wmask.CATEGORIES.items()}, category_0="not walkable")
        dst.write_colormap(1, {k: tuple(int(255 * c) for c in v) for k, v in COLOURS.items()})
    rasterio.shutil.copy(tmp, path, driver="COG", compress="DEFLATE", overview_resampling="nearest")
    os.remove(tmp)


def rgb(m):
    lut = np.array([COLOURS.get(i, (1, 1, 1)) for i in range(256)], np.float32)
    return lut[m]


def preview_overview(m, aoi, transform, path, block=4):
    h, w = (s // block * block for s in m.shape)
    small = m[:h, :w].reshape(h // block, block, w // block, block).max(axis=(1, 3))
    x0, y1 = transform * (0, 0)
    fig = plt.figure(figsize=(small.shape[1] / 200, small.shape[0] / 200 + 0.5), dpi=200)
    plt.imshow(rgb(small), extent=(x0, x0 + w, y1 - h, y1), interpolation="nearest")
    plt.plot(*aoi.exterior.xy, color="k", lw=0.5)
    plt.title(f"Walkable mask (block max {block} m)", fontsize=8)
    _legend()
    plt.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _legend():
    hs = [plt.Rectangle((0, 0), 1, 1, color=COLOURS[k]) for k in wmask.CATEGORIES]
    plt.legend(hs, list(wmask.CATEGORIES.values()), fontsize=5, loc="lower left", framealpha=0.9)


def preview_zoom(m, seg, stop_areas, transform, centre, path, half=300):
    x, y = gpd.GeoSeries(gpd.points_from_xy([centre[1]], [centre[0]]), crs="EPSG:4326").to_crs(osm.CRS).iloc[0].coords[0]
    inv = ~transform
    c0, r0 = (int(v) for v in inv * (x - half, y + half))
    sub = m[r0:r0 + 2 * half, c0:c0 + 2 * half]
    ext = (x - half, x + half, y - half, y + half)
    fig = plt.figure(figsize=(7, 7), dpi=150)
    plt.imshow(rgb(sub), extent=ext, interpolation="nearest")
    box = shapely.box(ext[0], ext[2], ext[1], ext[3])
    s = seg[seg.intersects(box)]
    for indoor, style in ((False, "-"), (True, ":")):
        part = s[s.indoor == indoor]
        part[part.side == "c"].plot(ax=plt.gca(), color="k", lw=0.4, linestyle=style)
        part[part.side != "c"].plot(ax=plt.gca(), color="m", lw=0.8, linestyle=style)
    stop_areas[stop_areas.intersects(box)].boundary.plot(ax=plt.gca(), color="b", lw=1)
    plt.xlim(ext[0], ext[1])
    plt.ylim(ext[2], ext[3])
    plt.title(f"{path.stem}: mask, segments (dotted = indoor, magenta = synthetic side), stop areas",
              fontsize=7)
    _legend()
    plt.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def stats(seg, nodes, pix, m, stop_areas, forced):
    km = lambda s: round(float(s.length_m.sum()) / 1000, 2)
    a = seg[seg.in_aoi]
    comp = seg.groupby("component").length_m.sum().sort_values(ascending=False)
    return {
        "segments": len(seg), "nodes": len(nodes),
        "segment_length_m": {"max": float(seg.length_m.max()), "median": float(seg.length_m.median())},
        "km_in_aoi_by_kind": {k: km(g) for k, g in a.groupby("kind")},
        "km_in_aoi_flags": {f: km(a[a[f]]) for f in ("crossing", "crossing_signals", "steps", "covered", "indoor")},
        "crossings_in_aoi": {"ways": int(a.crossing.sum()), "signalised": int(a.crossing_signals.sum())},
        "components": {"count": int(seg.component.nunique()),
                       "main_share_of_length": round(float(comp.iloc[0] / comp.sum()), 4),
                       "next_10_m": [round(float(v), 1) for v in comp.iloc[1:11]]},
        "mask_pixels_by_category": {wmask.CATEGORIES[k]: int((m == k).sum()) for k in wmask.CATEGORIES},
        "connectors": int((seg.kind == "connector").sum()),
        "segments_forced_line_pixels": forced,
        "segment_pixels_rows": len(pix),
        "outdoor_segments_without_pixels": int((~seg.indoor & (seg.pixel_count == 0)).sum()),
        "stops": {"count": len(stop_areas),
                  "in_aoi": int(stop_areas.in_aoi.sum()),
                  "by_mode": stop_areas["mode"].value_counts().to_dict(),
                  "by_match": stop_areas["match"].value_counts().to_dict(),
                  "by_geometry_source": stop_areas.geometry_source.value_counts().to_dict(),
                  "shelter": stop_areas.shelter.map({True: "yes", False: "no"}).fillna("unknown").value_counts().to_dict(),
                  "access_dist_m_p95": float(stop_areas.access_dist_m.quantile(0.95))},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_dir")
    ap.add_argument("out_dir")
    args = ap.parse_args()
    src, out = Path(args.in_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    log = lambda msg: print(f"[{time.perf_counter() - t0:6.1f}s] {msg}", flush=True)

    aoi = load_aoi(src / "aoi_halo_grid.geojson")
    ways, nodes_osm, areas = osm.read(src / "halo.osm.pbf")
    log(f"osm: {len(ways)} ways, {len(nodes_osm)} nodes, {len(areas)} areas")

    crossing_nodes = {r.osm_id: r.tags for r in nodes_osm.itertuples() if "crossing" in r.tags}
    seg, node_xy = network.build(ways, crossing_nodes, aoi)
    seg, nodes = network.graph(seg, node_xy)
    n_comp = seg.component.nunique()
    seg, nodes = network.graph(network.connect(seg, nodes, aoi), node_xy)
    log(f"network: {len(seg)} segments, {len(nodes)} nodes, components {n_comp} -> {seg.component.nunique()}")

    extent = aoi.buffer(100)
    at = areas[areas.intersects(extent)]
    t = at.tags
    buildings = at[t.map(lambda x: "building" in x and x["building"] not in ("roof", "no"))].geometry
    parks = shapely.union_all(at[t.map(lambda x: x.get("leisure") in osm.PARKS)].geometry.values)
    keep = aoi.buffer(network.KEEP_BUFFER)
    plazas = at[t.map(lambda x: x.get("highway") in ("pedestrian", "footway")
                      or x.get("area:highway") in ("pedestrian", "footway"))].geometry.intersection(keep)

    gtfs = pd.read_csv(src / "gtfs_stops.csv", dtype={"stop_id": str})
    plat = stops.platforms(ways, nodes_osm, areas)
    stop_areas = stops.build(gtfs, plat, seg, nodes, aoi)
    log(f"stops: {len(stop_areas)} in AOI")

    transform, shp = wmask.grid(src / "grid.json")
    outdoor = seg[~seg.indoor]
    cat = wmask.category(outdoor, parks)
    m = wmask.rasterize(outdoor, cat, plazas.values, stop_areas.geometry.values, buildings.values, transform, shp)
    pix, forced = wmask.segment_pixels(outdoor, cat, m, transform)
    seg["mask_category"] = cat.reindex(seg.index)
    seg["pixel_count"] = seg.segment_id.map(pix.segment_id.value_counts()).fillna(0).astype("int32")
    log(f"mask: {int((m > 0).sum())} walkable pixels, {len(pix)} segment-pixel rows, {forced} forced")

    write_cog(m, out / "walkable.tif", transform)
    seg.to_parquet(out / "segments.parquet")
    nodes.to_parquet(out / "nodes.parquet")
    pix.to_parquet(out / "segment_pixels.parquet", index=False)
    stop_areas.to_parquet(out / "stop_areas.parquet")
    st = stats(seg, nodes, pix, m, stop_areas, forced)
    (out / "stats.json").write_text(json.dumps(st, indent=1, ensure_ascii=False))
    preview_overview(m, aoi, transform, out / "preview_overview.png")
    for name, centre in ZOOMS.items():
        preview_zoom(m, seg, stop_areas, transform, centre, out / f"preview_{name}.png")
    log("done")


if __name__ == "__main__":
    main()
