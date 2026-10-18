"""T01 Cloud Run task: one 1 km grid tile (+100 m buffer) -> all 1 m geometry layers as npz.

2025-04-27 flight: ground, buildings, water. 2023-09-05 (leaf-on) flight: canopy, crown base,
gap fraction, ground cover. GUGiK classes: 2 ground, 3/4/5 veg <0.4/0.4-2/>2 m, 6 buildings
and structures (cranes etc. are kept as buildings), 7 noise, 8 water (per GUGiK docs; the data actually use 9), 12 overlap.
"""
import io
import json
import os
import time

import laspy
import numpy as np
from google.cloud import storage
from scipy import ndimage as ndi

T, B = 1000, 100
W = T + 2 * B
N = W * W
BKT = storage.Client().bucket("hackaton-honory-heat")
t_start = time.time()

idx = int(os.environ["TILE"]) if "TILE" in os.environ else int(os.environ.get("TILE_BASE", "0")) + int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
grid = json.loads(BKT.blob("T01/meta/grid.json").download_as_text())
ge0, gn0, ge1, gn1 = grid["bounds"]
nx = (ge1 - ge0) // T
e0, n0 = ge0 + (idx % nx) * T, gn0 + (idx // nx) * T
xw, yn = e0 - B, n0 + T + B  # buffered window: west edge, north edge


def load(year, first):
    sheets = json.loads(BKT.blob(f"T01/meta/sheets_{year}.json").download_as_text())
    parts, hist = [], np.zeros(32, np.int64)
    for s in sheets:
        bx0, by0, bx1, by1 = s["bounds"]
        if bx1 < xw or bx0 > xw + W or by1 < yn - W or by0 > yn:
            continue
        path = f"/tmp/{s['godlo']}.laz"
        BKT.blob(f"T01/laz/{year}/{s['godlo']}.laz").download_to_filename(path)
        with laspy.open(path, laz_backend=laspy.LazBackend.LazrsParallel) as f:
            for ch in f.chunk_iterator(3_000_000):
                c, r = np.asarray(ch.x) - xw, yn - np.asarray(ch.y)
                m = (c >= 0) & (c < W) & (r >= 0) & (r < W)
                if not m.any():
                    continue
                d = {"cell": r[m].astype(np.int32) * W + c[m].astype(np.int32),
                     "z": np.asarray(ch.z)[m].astype(np.float32),
                     "cls": np.asarray(ch.classification)[m].astype(np.uint8)}
                if first:
                    d["first"] = np.asarray(ch.return_number)[m] == 1
                hist += np.bincount(d["cls"], minlength=32)[:32]
                parts.append(d)
        os.remove(path)
    pts = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return pts, hist


def count(cell):
    return np.bincount(cell, minlength=N).reshape(W, W)


def mean(cell, v):
    n = np.bincount(cell, minlength=N)
    s = np.bincount(cell, weights=v, minlength=N)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (s / n).reshape(W, W)


def vmax(cell, v):
    out = np.full(N, -np.inf, np.float32)
    np.maximum.at(out, cell, v)
    out[np.isinf(out)] = np.nan
    return out.reshape(W, W)


def inpaint(a):
    """Fill NaNs by a mean pyramid (coarse-to-fine), smooth inside holes."""
    levels = [(a.astype(np.float64), np.isfinite(a))]
    while not levels[-1][1].all() and min(levels[-1][0].shape) > 1:
        x, m = levels[-1]
        h, w = x.shape
        h2, w2 = (h + 1) // 2, (w + 1) // 2
        xs, ms = np.zeros((2 * h2, 2 * w2)), np.zeros((2 * h2, 2 * w2))
        xs[:h, :w], ms[:h, :w] = np.where(m, x, 0), m
        s, n = xs.reshape(h2, 2, w2, 2).sum((1, 3)), ms.reshape(h2, 2, w2, 2).sum((1, 3))
        levels.append((np.where(n > 0, s / np.maximum(n, 1), np.nan), n > 0))
    x, m = levels[-1]
    filled = np.where(m, x, np.nanmean(x) if m.any() else 0.0)
    for x, m in reversed(levels[:-1]):
        h, w = x.shape
        up = ndi.uniform_filter(np.repeat(np.repeat(filled, 2, 0), 2, 1)[:h, :w], 3, mode="nearest")
        filled = np.where(m, x, up)
    return filled


def drop_small(mask, min_px):
    lab, n = ndi.label(mask)
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_px
    keep[0] = False
    return keep[lab]


def fill_small_holes(mask, max_px):
    holes = ndi.binary_fill_holes(mask) & ~mask
    return mask | (holes & ~drop_small(holes, max_px + 1))


def wsum(a, k=3):
    return ndi.uniform_filter(a.astype(np.float64), k, mode="nearest") * k * k


S3 = np.ones((3, 3), bool)
p25, hist25 = load("2025", first=False)
t_load25 = time.time() - t_start
c25, z25, k25 = p25["cell"], p25["z"], p25["cls"]
del p25

# Ground (DTM): 2025 ground, then 2025 water surface, then 2023 ground, then inpainting.
g25 = mean(c25[k25 == 2], z25[k25 == 2])
w25 = (k25 == 8) | (k25 == 9)
water_z = mean(c25[w25], z25[w25])
water_n = count(c25[w25])
bmax = vmax(c25[k25 == 6], z25[k25 == 6])
veg25 = count(c25[(k25 >= 3) & (k25 <= 5)])
dens25 = count(c25[(k25 != 7) & (k25 != 12)])
del c25, z25, k25

p23, hist23 = load("2023", first=True)
t_load23 = time.time() - t_start - t_load25
c23, z23, k23, f23 = p23["cell"], p23["z"], p23["cls"], p23["first"]
del p23
g23 = mean(c23[k23 == 2], z23[k23 == 2])
water_n = water_n + count(c23[(k23 == 8) | (k23 == 9)])

dtm0 = np.where(np.isfinite(g25), g25, water_z)
hb_raw = bmax - inpaint(dtm0)
bldg_raw = np.nan_to_num(hb_raw) >= 2.0
dtm0 = np.where(np.isfinite(dtm0) | bldg_raw, dtm0, g23)
dtm = inpaint(dtm0).astype(np.float32)

# Buildings (2025, class 6 incl. cranes/structures), >= 2 m above ground.
bldg = ndi.binary_closing(np.nan_to_num(bmax - dtm) >= 2.0, S3)
bldg = drop_small(fill_small_holes(bldg, 25), 10)
btop = np.where(bldg & np.isfinite(bmax), bmax, np.nan)
if (bldg & ~np.isfinite(btop)).any():
    btop = inpaint(btop)
dsm = np.where(bldg, np.maximum(btop, dtm), dtm).astype(np.float32)

# Water: class 8/9 in either flight, closed and hole-filled.
water = ndi.binary_closing(water_n > 0, np.ones((5, 5), bool))
water = drop_small(ndi.binary_fill_holes(water), 50) & ~bldg

# Canopy (2023 class 5), dropped where 2025 shows no vegetation in a 5x5 window (felled)
# or where a 2025 building stands.
h23 = z23 - dtm.ravel()[c23]
v5 = (k23 == 5) & (h23 >= 1.0)
vtop = vmax(c23[v5], z23[v5])
cth = np.nan_to_num(vtop - dtm)
canopy_2023 = (cth >= 2.5) & ~water
felled = canopy_2023 & ~bldg & (wsum(veg25, 5) < 0.5)
canopy = drop_small(canopy_2023 & ~bldg & ~felled, 3)
canopy_height = np.where(canopy, cth, 0).astype(np.float32)

# Crown base: 10th percentile of class-5 point heights per cell (>= 4 points),
# 3x3 mean over cells with an estimate; fallback 25% of top height.
cv, hv = c23[v5], h23[v5]
o = np.lexsort((hv, cv))
cv, hv = cv[o], hv[o]
n = np.bincount(cv, minlength=N)
start = np.concatenate([[0], np.cumsum(n)[:-1]])
ok = n >= 4
p10 = np.full(N, np.nan, np.float32)
p10[ok] = hv[start[ok] + np.floor(0.1 * (n[ok] - 1)).astype(np.int64)]
p10 = p10.reshape(W, W)
valid = np.isfinite(p10) & canopy
with np.errstate(invalid="ignore", divide="ignore"):
    cbh = wsum(np.where(valid, p10, 0)) / wsum(valid)
cbh = np.where(np.isfinite(cbh), cbh, 0.25 * cth)
cbh = np.clip(cbh, 0, np.maximum(cth - 0.5, 0))
crown_base = np.where(canopy, cbh, np.nan).astype(np.float32)
del cv, hv, o

# Gap fraction: share of first returns (excluding noise) reaching < 1 m above ground,
# summed over canopy cells in a 3x3 window.
fr = f23 & (k23 != 7)
nf = count(c23[fr])
nlow = count(c23[fr & (h23 < 1.0)])
with np.errstate(invalid="ignore", divide="ignore"):
    gap = wsum(nlow * canopy) / wsum(nf * canopy)
gap = np.where(canopy & (wsum(nf * canopy) >= 5), gap, np.nan).astype(np.float32)

# Ground cover from 2023 low points, 3x3 window.
n2, n3, n4 = (wsum(count(c23[k23 == k])) for k in (2, 3, 4))
tot = n2 + n3 + n4
with np.errstate(invalid="ignore", divide="ignore"):
    f3, f4 = n3 / tot, n4 / tot
surface = np.full((W, W), 6, np.uint8)  # other / no low returns
surface[tot > 0] = 5  # paved / bare
surface[(tot > 0) & (f3 >= 0.3)] = 3  # grass / low vegetation
surface[(tot > 0) & (f4 >= 0.3) & (f4 >= f3)] = 4  # shrubs
surface[water] = 2
surface[bldg] = 1
dens23 = count(c23[(k23 != 7) & (k23 != 12)])
del c23, z23, k23, f23, h23

crop = (slice(B, B + T), slice(B, B + T))
layers = {
    "dtm": dtm, "dsm": dsm, "building_mask": bldg.astype(np.uint8), "water_mask": water.astype(np.uint8),
    "canopy_height": canopy_height, "crown_base_height": crown_base, "crown_gap_fraction": gap,
    "surface_type": surface, "canopy_felled_2023_2025": felled.astype(np.uint8),
    "density_2023": np.minimum(dens23, 65535).astype(np.uint16),
    "density_2025": np.minimum(dens25, 65535).astype(np.uint16),
}
buf = io.BytesIO()
np.savez_compressed(buf, **{k: v[crop] for k, v in layers.items()})
BKT.blob(f"T01/tiles/{e0}_{n0}.npz").upload_from_string(buf.getvalue())
stats = {"tile": idx, "e0": e0, "n0": n0, "class_hist_2023": hist23.tolist(), "class_hist_2025": hist25.tolist(),
         "load25_s": round(t_load25), "load23_s": round(t_load23), "total_s": round(time.time() - t_start)}
BKT.blob(f"T01/tiles/{e0}_{n0}.json").upload_from_string(json.dumps(stats))
print(json.dumps(stats))
