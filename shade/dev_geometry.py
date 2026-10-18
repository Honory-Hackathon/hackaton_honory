"""Development stand-in for T01: crude 1 m layers from a few classified LAZ sheets.

Only for testing the shade engine on real geometry before T01 delivers. Per 1 m cell:
  dtm             min z of ground (class 2), holes filled by nearest neighbour
  dsm             max z of buildings (class 6), small gaps closed; NaN off buildings
  canopy_height   max z of high vegetation (class 5) above dtm; NaN without canopy
  crown_base      min z of high vegetation above dtm (crude: catches trunks and lower branches)
  transmission    first-return ground fraction in a 5x5 window (LiDAR gap fraction, not shortwave transmissivity)
Classes 7 (noise) and 12 (overlap) are dropped.

usage: python -m shade.dev_geometry OUT_DIR XMIN YMIN XMAX YMAX sheet1.laz [sheet2.laz ...]   (EPSG:2180, metres)
"""

import sys
from pathlib import Path

import laspy
import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage


def build(laz_paths, bounds, res=1.0):
    xmin, ymin, xmax, ymax = bounds
    W, H = int(round((xmax - xmin) / res)), int(round((ymax - ymin) / res))
    n = W * H
    ground = np.full(n, np.inf, np.float32)
    bmax = np.full(n, -np.inf, np.float32)
    vmax = np.full(n, -np.inf, np.float32)
    vmin = np.full(n, np.inf, np.float32)
    first_all = np.zeros(n, np.int32)
    first_ground = np.zeros(n, np.int32)
    for path in laz_paths:
        with laspy.open(path) as f:
            for pts in f.chunk_iterator(5_000_000):
                x, y, z = np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z, dtype=np.float32)
                cls = np.asarray(pts.classification)
                ok = (x >= xmin) & (x < xmax) & (y > ymin) & (y <= ymax) & (cls != 7) & (cls != 12)
                col = ((x[ok] - xmin) / res).astype(np.int64)
                row = ((ymax - y[ok]) / res).astype(np.int64)
                idx = row * W + col
                z, cls = z[ok], cls[ok]
                first = np.asarray(pts.return_number)[ok] == 1
                g, b, v = cls == 2, cls == 6, cls == 5
                np.minimum.at(ground, idx[g], z[g])
                np.maximum.at(bmax, idx[b], z[b])
                np.maximum.at(vmax, idx[v], z[v])
                np.minimum.at(vmin, idx[v], z[v])
                np.add.at(first_all, idx[first], 1)
                np.add.at(first_ground, idx[first & ((cls == 2) | (cls == 3))], 1)
        print("read", path, flush=True)

    shape = (H, W)
    ground, bmax, vmax, vmin = (a.reshape(shape) for a in (ground, bmax, vmax, vmin))
    have = np.isfinite(ground)
    _, (ri, ci) = ndimage.distance_transform_edt(~have, return_indices=True)
    dtm = ground[ri, ci]

    bmask = np.isfinite(bmax)
    closed = ndimage.binary_closing(bmask, np.ones((3, 3)), iterations=2) | bmask
    bfill = ndimage.maximum_filter(np.where(bmask, bmax, -np.inf), size=5)
    dsm = np.where(bmask, bmax, np.where(closed & np.isfinite(bfill), bfill, np.nan)).astype(np.float32)

    vmask = np.isfinite(vmax) & ~closed
    ch = np.where(vmask, vmax - dtm, np.nan)
    ch[ch < 2.0] = np.nan
    cb = np.where(np.isfinite(ch), np.clip(vmin - dtm, 0.0, None), np.nan)

    k = np.ones((5, 5), np.float32)
    fa = ndimage.convolve(first_all.reshape(shape).astype(np.float32), k, mode="constant")
    fg = ndimage.convolve(first_ground.reshape(shape).astype(np.float32), k, mode="constant")
    tau = np.where(np.isfinite(ch) & (fa > 0), fg / np.maximum(fa, 1), np.nan)

    layers = {"dtm": dtm, "dsm": dsm, "canopy_height": ch, "crown_base": cb, "transmission": tau}
    return {k: v.astype(np.float32) for k, v in layers.items()}, from_origin(xmin, ymax, res, res)


def write(layers, transform, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, arr in layers.items():
        profile = dict(driver="GTiff", tiled=True, dtype="float32", count=1, width=arr.shape[1], height=arr.shape[0],
                       crs="EPSG:2180", transform=transform, nodata=np.nan, compress="DEFLATE")
        with rasterio.open(out_dir / f"{name}.tif", "w", **profile) as dst:
            dst.write(arr, 1)


if __name__ == "__main__":
    out, *b = sys.argv[1:6]
    layers, transform = build(sys.argv[6:], tuple(float(v) for v in b))
    write(layers, transform, out)
