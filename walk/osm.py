"""Read the OSM objects T03 needs from a PBF into GeoDataFrames (EPSG:2180)."""

import geopandas as gpd
import osmium
import shapely

CRS = "EPSG:2180"
PARKS = {"park", "garden"}


def _keep_node(t):
    return (t.get("highway") in ("crossing", "bus_stop") or "crossing" in t
            or t.get("public_transport") in ("platform", "stop_position") or t.get("railway") == "tram_stop")


def _keep_way(t):
    return "highway" in t or t.get("public_transport") == "platform"


def _keep_area(t):
    return ("building" in t or t.get("leisure") in PARKS or t.get("public_transport") == "platform"
            or (t.get("highway") in ("pedestrian", "footway") and t.get("area") == "yes")
            or t.get("area:highway") in ("pedestrian", "footway") or t.get("place") == "square")


def read(pbf):
    """Return (ways, nodes, areas). Ways keep their OSM node ids, aligned with geometry coordinates."""
    ways, nodes, areas = [], [], []
    fab = osmium.geom.WKBFactory()
    for o in osmium.FileProcessor(str(pbf)).with_locations().with_areas():
        kind = o.type_str()
        if kind == "n":
            t = dict(o.tags)
            if t and _keep_node(t):
                nodes.append({"osm_id": o.id, "tags": t, "geometry": shapely.Point(o.location.lon, o.location.lat)})
        elif kind == "w":
            t = dict(o.tags)
            if not _keep_way(t):
                continue
            try:
                coords = [(n.lon, n.lat) for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
            if len(coords) >= 2:
                ways.append({"osm_id": o.id, "tags": t, "node_ids": [n.ref for n in o.nodes],
                             "geometry": shapely.LineString(coords)})
        elif kind == "a":
            t = dict(o.tags)
            if not _keep_area(t):
                continue
            try:
                g = shapely.from_wkb(fab.create_multipolygon(o))
            except RuntimeError:
                continue
            areas.append({"osm_type": "way" if o.from_way() else "relation", "osm_id": o.orig_id(),
                          "tags": t, "geometry": g})
    to = lambda rows: gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326").to_crs(CRS)
    return to(ways), to(nodes), to(areas)
