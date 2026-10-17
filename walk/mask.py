"""Walkable mask on the shared 1 m grid and the segment -> pixel lookup."""

import json

import numpy as np
import pandas as pd
import rasterio.features
import shapely
from rasterio.transform import from_origin

CATEGORIES = {1: "sidewalk", 2: "crossing", 3: "plaza", 4: "park_path", 5: "stop_area", 6: "covered",
              7: "steps", 8: "shared_street"}
CODE = {v: k for k, v in CATEGORIES.items()}
# Burn order: later wins where footprints overlap
PRIORITY = ["shared_street", "sidewalk", "park_path", "plaza", "steps", "covered", "crossing", "stop_area"]


def grid(path):
    g = json.load(open(path))
    x0, y0, x1, y1 = g["bounds"]
    return from_origin(x0, y1, g["res"], g["res"]), (g["height"], g["width"])


def category(seg, parks):
    """Mask category per outdoor segment."""
    cat = pd.Series("sidewalk", index=seg.index)
    cat[seg.kind == "shared_street"] = "shared_street"
    in_park = seg.geometry.centroid.within(parks) & seg.kind.isin(["footway", "path"])
    cat[in_park] = "park_path"
    cat[seg.kind == "pedestrian"] = "plaza"
    cat[seg.steps] = "steps"
    cat[seg.covered] = "covered"
    cat[seg.crossing] = "crossing"
    return cat


def rasterize(seg, cat, plazas, stop_areas, buildings, transform, shape):
    """uint8 category raster; buildings are cut out except under covered segments."""
    shapes = {c: [] for c in PRIORITY}
    for g, c in zip(seg.footprint.values, cat.values):
        shapes[c].append(g)
    shapes["plaza"] += list(plazas)
    shapes["stop_area"] += list(stop_areas)
    ordered = [(g, CODE[c]) for c in PRIORITY for g in shapes[c] if g is not None and not g.is_empty]
    out = rasterio.features.rasterize(ordered, out_shape=shape, transform=transform, fill=0, dtype="uint8")
    bldg = rasterio.features.rasterize(((g, 1) for g in buildings), out_shape=shape, transform=transform,
                                       fill=0, dtype="uint8").astype(bool)
    out[bldg & (out != CODE["covered"])] = 0
    return out


def segment_pixels(seg, cat, mask, transform):
    """(segment_id, row, col) for every mask pixel whose centre lies in the segment footprint.

    A segment whose footprint holds no mask pixel (thin, or drawn over a building) is given the
    pixels under its own line, and those pixels are added to the mask with its category.
    """
    inv = ~transform
    rows, cols, ids = [], [], []
    forced = 0
    for sid, fp, line, c in zip(seg.segment_id.values, seg.footprint.values, seg.geometry.values, cat.values):
        x0, y0, x1, y1 = fp.bounds
        c0, r0 = (int(np.floor(v)) for v in inv * (x0, y1))
        c1, r1 = (int(np.ceil(v)) for v in inv * (x1, y0))
        r0, c0 = max(r0, 0), max(c0, 0)
        r1, c1 = min(r1, mask.shape[0]), min(c1, mask.shape[1])
        rr, cc = np.mgrid[r0:r1, c0:c1]
        xs, ys = transform * (cc + 0.5, rr + 0.5)
        hit = shapely.contains_xy(fp, xs, ys) & (mask[rr, cc] > 0)
        r, cl = rr[hit], cc[hit]
        if r.size == 0:
            pts = shapely.line_interpolate_point(line, np.linspace(0, line.length, max(2, int(line.length) + 1)))
            px = np.array([inv * p for p in shapely.get_coordinates(pts)]).astype(int)
            px = np.unique(px, axis=0)
            px = px[(px[:, 1] >= 0) & (px[:, 1] < mask.shape[0]) & (px[:, 0] >= 0) & (px[:, 0] < mask.shape[1])]
            cl, r = px[:, 0], px[:, 1]
            mask[r, cl] = np.where(mask[r, cl] == 0, CODE[c], mask[r, cl])
            forced += 1
        rows.append(r.astype(np.int32))
        cols.append(cl.astype(np.int32))
        ids.append(np.full(r.size, sid, np.int64))
    pix = pd.DataFrame({"segment_id": np.concatenate(ids), "row": np.concatenate(rows),
                        "col": np.concatenate(cols)})
    return pix, forced
