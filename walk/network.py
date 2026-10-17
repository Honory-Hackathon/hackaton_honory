"""Walk network: OSM ways -> nodes and short segments with stable ids.

Ways are split at every node shared by two walkable ways (and at way ends), then each
edge is cut into equal pieces of at most MAX_LEN metres. Streets whose sidewalks are not
mapped separately get one synthetic edge per side, offset from the centreline but
attached to the centreline nodes, so the router can pick the shadier side.
"""

import hashlib
import math
import re

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely.ops import substring

from .osm import CRS

MAX_LEN = 25.0
KEEP_BUFFER = 50.0  # segments are kept up to this distance outside the AOI
SNAP = 5.0  # dead ends this close to the main component are treated as mapping gaps
FOOT = {"footway", "path", "pedestrian", "steps", "track", "corridor", "platform", "elevator", "bridleway"}
SHARED = {"service", "living_street"}
# Default carriageway width (m) for streets without width/lanes tags
CARRIAGEWAY = {"trunk": 14.0, "primary": 12.0, "secondary": 10.0, "tertiary": 8.0, "residential": 6.0,
               "unclassified": 6.0}
STREETS = set(CARRIAGEWAY) | {k + "_link" for k in ("trunk", "primary", "secondary", "tertiary")}
SIDE_BAND = 2.0  # width of a synthetic sidewalk band (m)
WIDTH = {"sidewalk": 2.5, "connector": 2.0, "crossing": 3.0, "footway": 2.0, "path": 2.0, "steps": 2.0,
         "pedestrian": 8.0, "platform": 2.5, "shared_street": 4.0, "street_side": SIDE_BAND, "corridor": 3.0}
YES = ("yes", "designated", "permissive")


def stable_id(*parts):
    return int.from_bytes(hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest(), "big") >> 2


def _num(v):
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(m)?\s*$", v or "")
    return float(m.group(1).replace(",", ".")) if m else None


def _sides(t):
    """Sidewalk state per side ('yes', 'no', 'separate'); untagged counts as 'yes' except on trunks."""
    out = {}
    for side in ("left", "right"):
        v = t.get(f"sidewalk:{side}") or t.get("sidewalk:both")
        s = t.get("sidewalk")
        if v is None and s is not None:
            v = {"both": "yes", "yes": "yes", "none": "no", "no": "no", "separate": "separate",
                 side: "yes"}.get(s, "no" if s in ("left", "right") else s)
        out[side] = v or ("no" if t["highway"].startswith("trunk") else "yes")
    return out


def classify(t):
    """List of (side, kind) edges a way contributes: side 'c' = on the way, 'l'/'r' = synthetic sidewalk."""
    h = t.get("highway")
    foot = t.get("foot")
    if foot in ("no", "use_sidepath") or (t.get("access") in ("no", "private") and foot not in YES):
        return []
    if h in FOOT or (h is None and t.get("public_transport") == "platform"):
        if h == "steps":
            return [("c", "steps")]
        if h in ("corridor", "elevator"):
            return [("c", "corridor")]
        if h in ("platform", None):
            # metro and rail platforms are out of scope; tram/bus platforms stay
            rail = t.get("subway") == "yes" or t.get("train") == "yes" or t.get("railway") == "platform"
            return [] if rail and t.get("tram") != "yes" and t.get("bus") != "yes" else [("c", "platform")]
        if h == "pedestrian":
            return [("c", "pedestrian")]
        sub = t.get("footway") or t.get("path")
        if sub == "crossing":
            return [("c", "crossing")]
        if sub == "sidewalk":
            return [("c", "sidewalk")]
        return [("c", "path" if h in ("path", "track", "bridleway") else "footway")]
    if h == "cycleway" and (foot in YES or t.get("segregated") == "yes"):
        return [("c", "crossing" if t.get("cycleway") == "crossing" else "footway")]
    if h in SHARED:
        return [("c", "shared_street")]
    if h in STREETS and t.get("tunnel") != "yes":
        s = _sides(t)
        return [(k[0], "street_side") for k in ("left", "right") if s[k] == "yes"]
    return []


def flags(t, kind):
    level = (t.get("level") or "") + ";" + (t.get("layer") or "")
    indoor = (kind == "corridor" or t.get("indoor") in ("yes", "room", "corridor", "area")
              or t.get("tunnel") == "yes" or t.get("location") == "underground"
              or ("-" in level and t.get("tunnel") is not None))
    covered = not indoor and (t.get("covered") in ("yes", "arcade", "colonnade")
                              or t.get("tunnel") in ("building_passage", "covered"))
    return {
        "crossing": kind == "crossing",
        "crossing_signals": t.get("crossing") == "traffic_signals" or t.get("crossing:signals") == "yes",
        "steps": kind == "steps",
        "covered": covered,
        "indoor": indoor,
    }


def footprint_width(t, kind):
    if kind == "street_side":
        return SIDE_BAND
    w = _num(t.get("width"))
    if w is not None and 0.5 <= w <= 30:
        return w
    if kind == "shared_street" and t.get("service") == "parking_aisle":
        return 5.0
    return WIDTH[kind]


def side_offset(t):
    """Distance (m) from the centreline to the middle of a synthetic sidewalk."""
    w = _num(t.get("width"))
    lanes = _num(t.get("lanes"))
    if w is None or not 2 <= w <= 40:
        w = 3.0 * lanes if lanes else CARRIAGEWAY.get(t["highway"].removesuffix("_link"), 6.0)
    return w / 2 + SIDE_BAND / 2


def build(ways, crossing_nodes, aoi):
    """Return (segments, node coordinates by node id); segments in EPSG:2180.

    ways: GeoDataFrame from osm.read (geometry aligned with node_ids).
    crossing_nodes: dict osm node id -> tags of crossing nodes (for signalised crossings).
    """
    edges = []
    for w in ways.itertuples():
        for side, kind in classify(w.tags):
            edges.append((w, side, kind))
    use = {}
    for w in {e[0].osm_id: e[0] for e in edges}.values():  # once per way, even with two sides
        for i, n in enumerate(w.node_ids):
            use[n] = use.get(n, 0) + (2 if i in (0, len(w.node_ids) - 1) else 1)
    keep = shapely.buffer(aoi, KEEP_BUFFER)
    shapely.prepare(keep)
    shapely.prepare(aoi)

    segs = []
    node_xy = {}
    for w, side, kind in edges:
        ids = w.node_ids
        coords = np.asarray(w.geometry.coords)
        cut = [0] + [i for i in range(1, len(ids) - 1) if use[ids[i]] > 1] + [len(ids) - 1]
        fl = flags(w.tags, kind)
        width = footprint_width(w.tags, kind)
        off = side_offset(w.tags) if side != "c" else 0.0
        for a, b in zip(cut[:-1], cut[1:]):
            line = shapely.LineString(coords[a:b + 1])
            if line.length == 0:
                continue
            u_osm, v_osm = ids[a], ids[b]
            node_xy.setdefault(u_osm, coords[a])
            node_xy.setdefault(v_osm, coords[b])
            signals = fl["crossing_signals"] or any(
                crossing_nodes.get(n, {}).get("crossing") == "traffic_signals" for n in ids[a:b + 1])
            n_pieces = max(1, math.ceil(line.length / MAX_LEN))
            step = line.length / n_pieces
            prev = u_osm
            for k in range(n_pieces):
                piece = substring(line, k * step, (k + 1) * step) if n_pieces > 1 else line
                if k == n_pieces - 1:
                    nxt = v_osm
                else:
                    nxt = -stable_id(w.osm_id, u_osm, v_osm, k)
                    node_xy.setdefault(nxt, np.asarray(piece.coords[-1]))
                if keep.intersects(piece):
                    geom = piece if side == "c" else shapely.offset_curve(piece, off if side == "l" else -off)
                    segs.append({
                        "segment_id": stable_id(w.osm_id, side, u_osm, v_osm, k),
                        "u": prev, "v": nxt, "osm_way_id": w.osm_id, "side": side, "kind": kind,
                        "highway": w.tags.get("highway"), "name": w.tags.get("name"),
                        "surface": w.tags.get("surface"), "length_m": round(piece.length, 2),
                        "width_m": width, **fl, "crossing_signals": bool(kind == "crossing" and signals),
                        "in_aoi": aoi.intersects(piece), "geometry": geom,
                    })
                prev = nxt
    seg = gpd.GeoDataFrame(segs, geometry="geometry", crs=CRS)
    dup = seg.segment_id.duplicated()
    if dup.any():
        raise ValueError(f"{dup.sum()} duplicate segment ids")
    seg["footprint"] = _footprint(seg)
    return seg, node_xy


def _footprint(seg):
    return gpd.GeoSeries(shapely.buffer(seg.geometry.values, seg.width_m.values / 2, cap_style="flat",
                                        join_style="round"), index=seg.index, crs=CRS)


def graph(seg, node_xy):
    """Add a component number to segments (0 = largest) and build the node table."""
    g = nx.Graph()
    g.add_edges_from(zip(seg.u, seg.v))
    comps = sorted(nx.connected_components(g), key=len, reverse=True)
    comp_of = {n: i for i, c in enumerate(comps) for n in c}
    seg = seg.assign(component=seg.u.map(comp_of).astype("int32"))
    deg = dict(g.degree())
    used = sorted(comp_of)
    nodes = gpd.GeoDataFrame({
        "node_id": used,
        "osm_node_id": pd.array([n if n > 0 else None for n in used], dtype="Int64"),
        "degree": [deg[n] for n in used],
        "component": [comp_of[n] for n in used],
    }, geometry=shapely.points(np.array([node_xy[n] for n in used])), crs=CRS)
    return seg.sort_values("segment_id", ignore_index=True), nodes


def connect(seg, nodes, aoi):
    """Join dead ends of minor components to the nearest node of the closest main-component segment
    within SNAP metres, with straight 'connector' segments."""
    main = seg[seg.component == 0].reset_index(drop=True)
    xy = nodes.set_index("node_id").geometry
    dead = nodes[(nodes.component > 0) & (nodes.degree == 1)].reset_index(drop=True)
    pairs = main.sindex.nearest(dead.geometry, max_distance=SNAP, return_all=False)
    rows = []
    for i, j in zip(*pairs):
        a, s = dead.iloc[i], main.iloc[j]
        p = a.geometry
        b = s.u if xy[s.u].distance(p) <= xy[s.v].distance(p) else s.v
        line = shapely.LineString([p, xy[b]])
        rows.append({"segment_id": stable_id("connector", a.node_id, b), "u": a.node_id, "v": b,
                     "osm_way_id": None, "side": "c", "kind": "connector", "length_m": round(line.length, 2),
                     "width_m": WIDTH["footway"], "crossing": False, "crossing_signals": False,
                     "steps": False, "covered": False, "indoor": False, "in_aoi": aoi.intersects(line),
                     "geometry": line})
    con = gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)
    con["footprint"] = _footprint(con)
    out = pd.concat([seg.drop(columns="component"), con], ignore_index=True)
    out["osm_way_id"] = out.osm_way_id.astype("Int64")
    return gpd.GeoDataFrame(out, geometry="geometry", crs=CRS)
