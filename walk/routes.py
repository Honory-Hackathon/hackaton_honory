"""Routability check: shortest walking paths (by length) between named places.

usage: python -m walk.routes OUT_DIR   (reads segments/nodes/walkable.tif written by walk.run)
Writes routes.parquet, routes.csv and preview_routes.png into OUT_DIR.
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib
import networkx as nx
import numpy as np
import pandas as pd
import rasterio
import shapely

from .osm import CRS
from .run import rgb

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PLACES = {  # (lat, lon)
    "Plac Zbawiciela": (52.21971, 21.01864),
    "Krakowskie Przedmieście (Uniwersytet)": (52.24007, 21.01661),
    "Dworzec Centralny": (52.22887, 21.00404),
    "Łazienki main gate (Al. Ujazdowskie)": (52.21636, 21.02645),
    "Rondo Dmowskiego": (52.22983, 21.01173),
    "Plac Trzech Krzyży": (52.22830, 21.02340),
    "Centrum Nauki Kopernik": (52.24190, 21.02870),
    "Hala Mirowska": (52.23880, 20.99860),
}
PAIRS = [
    ("Plac Zbawiciela", "Krakowskie Przedmieście (Uniwersytet)"),
    ("Dworzec Centralny", "Łazienki main gate (Al. Ujazdowskie)"),
    ("Rondo Dmowskiego", "Plac Trzech Krzyży"),
    ("Centrum Nauki Kopernik", "Hala Mirowska"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    out = Path(ap.parse_args().out_dir)
    seg = gpd.read_parquet(out / "segments.parquet", columns=[
        "segment_id", "u", "v", "kind", "length_m", "crossing", "crossing_signals", "steps", "indoor", "geometry"])
    nodes = gpd.read_parquet(out / "nodes.parquet")
    main_nodes = nodes[nodes.component == 0].reset_index(drop=True)
    g = nx.Graph()
    for s in seg.itertuples():
        if g.has_edge(s.u, s.v) and g[s.u][s.v]["length"] <= s.length_m:
            continue
        g.add_edge(s.u, s.v, length=s.length_m, sid=s.segment_id)

    pts = gpd.GeoSeries(gpd.points_from_xy([p[1] for p in PLACES.values()], [p[0] for p in PLACES.values()]),
                        index=list(PLACES), crs="EPSG:4326").to_crs(CRS)
    snap = {k: main_nodes.sindex.nearest(p, return_all=False)[1][0] for k, p in pts.items()}
    by_id = seg.set_index("segment_id")
    rows = []
    for a, b in PAIRS:
        na, nb = main_nodes.iloc[snap[a]], main_nodes.iloc[snap[b]]
        path = nx.shortest_path(g, na.node_id, nb.node_id, weight="length")
        sids = [g[x][y]["sid"] for x, y in zip(path[:-1], path[1:])]
        r = by_id.loc[sids]
        rows.append({
            "from": a, "to": b, "segments": len(sids), "length_m": round(r.length_m.sum()),
            "straight_m": round(pts[a].distance(pts[b])), "detour": round(r.length_m.sum() / pts[a].distance(pts[b]), 2),
            "crossings": int(r.crossing.sum()), "signalised": int(r.crossing_signals.sum()),
            "steps": int(r.steps.sum()), "indoor_m": round(r[r.indoor].length_m.sum()),
            "synthetic_side_m": round(r[r.kind == "street_side"].length_m.sum()),
            "snap_from_m": round(na.geometry.distance(pts[a]), 1), "snap_to_m": round(nb.geometry.distance(pts[b]), 1),
            "segment_ids": [int(x) for x in sids],
            "geometry": shapely.line_merge(shapely.MultiLineString(list(r.geometry.values))),
        })
    routes = gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)
    routes.to_parquet(out / "routes.parquet")
    routes.drop(columns=["geometry", "segment_ids"]).to_csv(out / "routes.csv", index=False)
    print(routes.drop(columns=["geometry", "segment_ids"]).to_string())

    with rasterio.open(out / "walkable.tif") as src:
        x0, y0, x1, y1 = routes.total_bounds + np.array([-300, -300, 300, 300])
        win = rasterio.windows.from_bounds(x0, y0, x1, y1, src.transform).round_offsets().round_lengths()
        m = src.read(1, window=win)
        b = rasterio.windows.bounds(win, src.transform)
    fig = plt.figure(figsize=(8, 8 * m.shape[0] / m.shape[1]), dpi=150)
    plt.imshow(rgb(m), extent=(b[0], b[2], b[1], b[3]), interpolation="nearest", alpha=0.6)
    for (_, r), c in zip(routes.iterrows(), plt.cm.tab10.colors):
        plt.plot(*np.asarray(shapely.get_coordinates(r.geometry)).T, color=c, lw=0, marker=".", ms=1.2,
                 label=f"{r['from']} → {r['to']}: {r.length_m} m")
    for k, p in pts.items():
        plt.plot(p.x, p.y, "k^", ms=4)
    plt.legend(fontsize=5, loc="lower left", markerscale=6)
    plt.axis("off")
    plt.title("Shortest walking routes (by length) on the T03 network", fontsize=8)
    fig.savefig(out / "preview_routes.png", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
