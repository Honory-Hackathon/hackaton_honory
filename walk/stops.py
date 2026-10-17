"""Stop waiting areas: one polygon per GTFS tram/bus stop_id, matched to OSM platforms."""

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from .osm import CRS

MATCH_RADIUS = 30.0
LENGTH = {"bus": 15.0, "tram": 30.0, "bus+tram": 30.0}  # waiting zone along the kerb (m)
DEPTH = 3.0
STOP_BUFFER = 25.0  # the AOI boundary runs along avenue axes: median and far-kerb stops sit just outside
MAX_AREA = 1500.0  # larger OSM platform polygons are whole interchanges, not one stop post


def _is_platform(t):
    return (t.get("public_transport") == "platform" or t.get("highway") in ("bus_stop", "platform")
            or t.get("railway") == "platform")


def platforms(ways, nodes, areas):
    """All OSM platform features: polygons first, then lines, then points (best geometry wins per ref)."""
    rows = []
    for df, otype, rank in ((areas, None, 0), (ways, "way", 1), (nodes, "node", 2)):
        for r in df.itertuples():
            if not _is_platform(r.tags):
                continue
            g = r.geometry
            if rank == 1 and (g.is_closed and r.tags.get("area") == "yes"):
                continue  # closed platform ways come through `areas`
            if rank == 0 and g.area > MAX_AREA:
                continue
            rows.append({"osm_type": otype or r.osm_type, "osm_id": r.osm_id, "rank": rank,
                         "ref": r.tags.get("ref"), "shelter": r.tags.get("shelter"),
                         "bench": r.tags.get("bench"), "tram": r.tags.get("tram") == "yes" or
                         r.tags.get("railway") == "platform", "geometry": g})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS).sort_values("rank", kind="stable")


def _rectangle(p, direction, length):
    d = direction / np.linalg.norm(direction)
    n = np.array([-d[1], d[0]])
    c = np.array(p.coords[0])
    h, k = d * length / 2, n * DEPTH / 2
    return shapely.Polygon([c - h - k, c + h - k, c + h + k, c - h + k])


def _direction(p, segments):
    """Local direction of the closest sidewalk-like segment (falls back to any segment)."""
    near = segments.iloc[segments.sindex.nearest(p, return_all=False)[1]]
    line = near.geometry.iloc[0]
    s = line.project(p)
    a, b = line.interpolate(max(s - 2, 0)), line.interpolate(min(s + 2, line.length))
    v = np.array(b.coords[0]) - np.array(a.coords[0])
    return v if np.linalg.norm(v) > 0 else np.array([1.0, 0.0])


def build(gtfs, plat, segments, nodes, aoi):
    """gtfs: DataFrame stop_id, stop_name, lat, lon, mode. Returns GeoDataFrame of waiting areas."""
    pts = gpd.GeoSeries(gpd.points_from_xy(gtfs.lon, gtfs.lat), crs="EPSG:4326").to_crs(CRS)
    stops = gpd.GeoDataFrame(gtfs.reset_index(drop=True), geometry=pts.values, crs=CRS)
    stops = stops[stops.distance(aoi) <= STOP_BUFFER].reset_index(drop=True)

    by_ref = plat.dropna(subset=["ref"]).drop_duplicates("ref").set_index("ref")
    spatial = plat[~plat.ref.isin(stops.stop_id)]  # platforms not claimed by another stop's ref
    sidewalks = segments[segments.kind.isin(["sidewalk", "platform", "footway", "street_side"])
                         & ~segments.indoor]
    main = nodes[nodes.component == 0]

    out = []
    for s in stops.itertuples():
        m, method = None, "none"
        if s.stop_id in by_ref.index:
            m, method = by_ref.loc[s.stop_id], "ref"
        else:
            cand = spatial[spatial.distance(s.geometry) <= MATCH_RADIUS]
            if len(cand):
                cand = cand.assign(d=cand.distance(s.geometry)).sort_values(["d", "rank"])
                m, method = cand.iloc[0], "spatial"
        g = m.geometry if m is not None else None
        if g is not None and g.geom_type in ("Polygon", "MultiPolygon"):
            area, src = g, "osm_polygon"
        elif g is not None and g.geom_type == "LineString":
            area, src = g.buffer(DEPTH / 2, cap_style="flat"), "osm_line"
        else:
            p = g if g is not None else s.geometry
            area = _rectangle(p, _direction(p, sidewalks), LENGTH[s.mode])
            src = "osm_point" if g is not None else "gtfs_point"
        c = area.centroid
        k = main.sindex.nearest(c, return_all=False)[1][0]
        out.append({
            "stop_id": s.stop_id, "stop_name": s.stop_name, "mode": s.mode, "in_aoi": aoi.contains(s.geometry),
            "shelter": None if m is None or m.shelter is None else m.shelter == "yes",
            "bench": None if m is None or m.bench is None else m.bench == "yes",
            "match": method, "geometry_source": src,
            "osm_type": None if m is None else m.osm_type,
            "osm_id": pd.NA if m is None else int(m.osm_id),
            "match_dist_m": round(float(g.distance(s.geometry)), 1) if g is not None else None,
            "access_node_id": int(main.node_id.iloc[k]),
            "access_dist_m": round(float(main.geometry.iloc[k].distance(c)), 1),
            "geometry": area,
        })
    res = gpd.GeoDataFrame(out, geometry="geometry", crs=CRS)
    res["osm_id"] = res.osm_id.astype("Int64")
    return res
