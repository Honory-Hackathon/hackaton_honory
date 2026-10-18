"""AOI, halo, grid and LiDAR sheet lists for T01 (small WFS lookups only)."""
import json
import math
import sys
from pathlib import Path

import requests
from lxml import etree
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "meta")
OUT.mkdir(exist_ok=True)

PRG = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"
LIDAR = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze"
NS = {"gml": "http://www.opengis.net/gml/3.2", "wfs": "http://www.opengis.net/wfs/2.0"}
JOBS = {"2023": "GI-FOTO.6201.22.2023", "2025": "GK-FOTO.6201.5.2025"}


def poslist_to_poly(el):
    v = [float(x) for x in el.text.split()]
    # EPSG:2180 urn axis order is northing, easting
    return Polygon([(v[i + 1], v[i]) for i in range(0, len(v), 2)])


def aoi():
    r = requests.get(PRG, params={
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": "A05_Granice_jednostek_ewidencyjnych",
        "FILTER": "<Filter><PropertyIsEqualTo><ValueReference>JPT_KOD_JE</ValueReference>"
                  "<Literal>146510_8</Literal></PropertyIsEqualTo></Filter>"}, timeout=120)
    r.raise_for_status()
    doc = etree.fromstring(r.content)
    polys = [poslist_to_poly(e) for e in doc.iterfind(".//gml:exterior//gml:posList", NS)]
    assert len(polys) == 1, len(polys)
    return polys[0]


def sheets(layer_year, bounds):
    e0, n0, e1, n1 = bounds
    feats, start = [], 0
    while True:
        r = requests.get(LIDAR, params={
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAMES": f"gugik:SkorowidzDanychPomiarowychLIDAR{layer_year}",
            "BBOX": f"{n0},{e0},{n1},{e1},urn:ogc:def:crs:EPSG::2180",
            "COUNT": 1000, "STARTINDEX": start}, timeout=300)
        r.raise_for_status()
        doc = etree.fromstring(r.content)
        members = doc.findall("wfs:member", NS)
        for m in members:
            f = m[0]
            rec = {etree.QName(c).localname: "".join(c.itertext()).strip() for c in f
                   if etree.QName(c).localname not in ("msGeometry", "boundedBy")}
            rec["geometry"] = poslist_to_poly(f.find(".//gml:posList", NS))
            feats.append(rec)
        if len(members) < 1000:
            return feats
        start += 1000


a = aoi()
halo = a.buffer(1000)
e0, n0, e1, n1 = halo.bounds
grid = (math.floor(e0 / 1000) * 1000, math.floor(n0 / 1000) * 1000,
        math.ceil(e1 / 1000) * 1000, math.ceil(n1 / 1000) * 1000)
print("AOI area km2", round(a.area / 1e6, 3), "halo km2", round(halo.area / 1e6, 3), "grid", grid,
      "cells", (grid[2] - grid[0]) * (grid[3] - grid[1]))

json.dump({"type": "FeatureCollection", "crs": "EPSG:2180", "features": [
    {"type": "Feature", "properties": {"name": "aoi", "teryt": "146510_8"}, "geometry": mapping(a)},
    {"type": "Feature", "properties": {"name": "halo", "buffer_m": 1000}, "geometry": mapping(halo)},
    {"type": "Feature", "properties": {"name": "grid"}, "geometry": mapping(box(*grid))}]},
    open(OUT / "aoi_halo_grid.geojson", "w"))
json.dump({"crs": "EPSG:2180", "res": 1.0, "bounds": grid,
           "width": grid[2] - grid[0], "height": grid[3] - grid[1]}, open(OUT / "grid.json", "w"), indent=1)

gridbox = box(*grid)
for year, job in JOBS.items():
    feats = [f for f in sheets(year, grid) if f.get("nr_zglosz") == job]
    sel = [f for f in feats if f["geometry"].intersects(gridbox)]
    cover = gridbox.difference(unary_union([f["geometry"] for f in sel]))
    rows = []
    for f in sel:
        g = f.pop("geometry")
        f["in_halo"] = g.intersects(halo)
        f["in_aoi"] = g.intersects(a)
        f["bounds"] = [round(x, 2) for x in g.bounds]
        rows.append(f)
    rows.sort(key=lambda r: r["godlo"])
    json.dump(rows, open(OUT / f"sheets_{year}.json", "w"), indent=1, ensure_ascii=False)
    dates = sorted({r["akt_data"] for r in rows})
    print(year, job, "sheets in grid", len(rows), "in halo", sum(r["in_halo"] for r in rows),
          "in aoi", sum(r["in_aoi"] for r in rows), "dates", dates,
          "uncovered grid km2", round(cover.area / 1e6, 3),
          "uncovered halo km2", round(halo.difference(unary_union([box(*r["bounds"]) for r in rows])).area / 1e6, 3))
