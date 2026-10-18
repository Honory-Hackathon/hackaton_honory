"""Far-caster surface: ground + buildings over the caster extent, outside the shared grid.

Same rule as T01's `dsm`: per 1 m cell the max z of 2025 class 6 (buildings and structures), else the
mean z of class 2 (ground); no vegetation. Cells inside the shared grid are left NaN (the engine pastes
T01's own surface there), as are cells without returns (no caster).

usage: python -m shade.far_dsm OUT.tif sheet1.laz [sheet2.laz ...]
"""

import sys
from concurrent.futures import ProcessPoolExecutor

import laspy
import numpy as np
import rasterio
from rasterio.transform import from_origin

EXTENT = (630000, 483000, 643000, 494000)  # caster extent, root README.md
GRID = (634000, 483000, 642000, 492000)    # shared grid


def bin_sheet(path):
    """(row0, col0, building max, ground sum, ground count) over the sheet's window of the extent."""
    xmin, ymin, xmax, ymax = EXTENT
    with laspy.open(path) as f:
        h = f.header
        c0, c1 = max(0, int(h.mins[0] - xmin)), min(xmax - xmin, int(np.ceil(h.maxs[0] - xmin)) + 1)
        r0, r1 = max(0, int(ymax - h.maxs[1])), min(ymax - ymin, int(np.ceil(ymax - h.mins[1])) + 1)
        shape = (r1 - r0, c1 - c0)
        bmax = np.full(shape[0] * shape[1], -np.inf, np.float32)
        gsum = np.zeros(shape[0] * shape[1], np.float64)
        gcnt = np.zeros(shape[0] * shape[1], np.int32)
        for pts in f.chunk_iterator(5_000_000):
            x, y = np.asarray(pts.x), np.asarray(pts.y)
            cls = np.asarray(pts.classification)
            inside = (x >= GRID[0]) & (x < GRID[2]) & (y > GRID[1]) & (y <= GRID[3])
            ok = (x >= xmin) & (x < xmax) & (y > ymin) & (y <= ymax) & ~inside & ((cls == 2) | (cls == 6))
            col = (x[ok] - xmin).astype(np.int64) - c0
            row = (ymax - y[ok]).astype(np.int64) - r0
            keep = (col >= 0) & (col < shape[1]) & (row >= 0) & (row < shape[0])
            idx = (row * shape[1] + col)[keep]
            z = np.asarray(pts.z)[ok][keep]
            c = cls[ok][keep]
            b, g = c == 6, c == 2
            np.maximum.at(bmax, idx[b], z[b].astype(np.float32))
            np.add.at(gsum, idx[g], z[g])
            np.add.at(gcnt, idx[g], 1)
    return r0, c0, bmax.reshape(shape), gsum.reshape(shape), gcnt.reshape(shape)


def main():
    out, *paths = sys.argv[1:]
    xmin, ymin, xmax, ymax = EXTENT
    H, W = ymax - ymin, xmax - xmin
    bmax = np.full((H, W), -np.inf, np.float32)
    gsum = np.zeros((H, W), np.float64)
    gcnt = np.zeros((H, W), np.int32)
    with ProcessPoolExecutor() as ex:
        for i, (r0, c0, b, s, n) in enumerate(ex.map(bin_sheet, paths)):
            win = np.s_[r0:r0 + b.shape[0], c0:c0 + b.shape[1]]
            np.maximum(bmax[win], b, out=bmax[win])
            gsum[win] += s
            gcnt[win] += n
            if i % 25 == 0:
                print(f"{i + 1}/{len(paths)}", flush=True)
    ground = np.where(gcnt > 0, gsum / np.maximum(gcnt, 1), np.nan).astype(np.float32)
    surf = np.where(np.isfinite(bmax), np.fmax(bmax, ground), ground).astype(np.float32)
    profile = dict(driver="COG", dtype="float32", count=1, width=W, height=H, crs="EPSG:2180",
                   transform=from_origin(xmin, ymax, 1, 1), nodata=np.nan, compress="DEFLATE",
                   predictor=2, bigtiff="YES", num_threads="ALL_CPUS")
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(surf, 1)
    gr = np.s_[ymax - GRID[3]:ymax - GRID[1], GRID[0] - xmin:GRID[2] - xmin]
    outside = np.ones((H, W), bool)
    outside[gr] = False
    print(f"outside grid: {outside.sum() / 1e6:.1f} Mpx, with data {np.isfinite(surf[outside]).mean():.4f}, "
          f"building cells {np.isfinite(bmax[outside]).mean():.4f}, max height {np.nanmax(surf):.1f} m")


if __name__ == "__main__":
    main()
