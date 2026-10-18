"""Render a 33-band raster (UTCI or SOLWEIG shadow) to web map tiles (EPSG:3857 XYZ PNG).

Input: one or more 33-band GeoTIFF/COG paths (gs:// or local, globs allowed), all on the same CRS and pixel grid
(e.g. the T06 pilot COG, the T06 district tiles, or the T07 district COG). They are mosaicked in memory.

Output: gs://<bucket>/T10/frames/<source>/<step:02d>/<z>/<x>/<y>.png  plus  <source>/meta.json

    python render_frames.py --input gs://hackaton-honory-heat/T06/cog/utci_solweig.tif \
        --kind utci --source pilot_utci --out gs://hackaton-honory-heat/T10/frames

Cloud Run Job: bands are split over tasks via CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT; task 0 writes meta.json.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import fnmatch
import io
import json
import os
import sys
import time

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from PIL import Image

N_STEPS = 33
T0_UTC = dt.datetime(2026, 6, 28, 4, 0, tzinfo=dt.timezone.utc)
WEB_HALF = 20037508.342789244
TILE = 256

# UTCI colour anchors (degC -> RGB). Category edges 26 / 32 / 38 / 46 are hard steps between hues,
# with a lightness ramp inside each category so the spatial detail stays visible.
UTCI_STOPS = [
    (10.0, (49, 104, 160)), (25.99, (120, 190, 200)),          # < 26  no / weak stress: blue-teal
    (26.0, (255, 241, 168)), (31.99, (250, 214, 100)),         # 26-32 moderate: yellow
    (32.0, (253, 174, 97)), (37.99, (240, 120, 50)),           # 32-38 strong: orange
    (38.0, (215, 48, 39)), (45.99, (160, 20, 40)),             # 38-46 very strong: red
    (46.0, (110, 10, 90)), (55.0, (50, 0, 60)),                # > 46 extreme: purple
]
UTCI_MIN, UTCI_STEP = 10.0, 0.25  # palette index i (1..254) <-> UTCI = UTCI_MIN + (i-1)*UTCI_STEP
SHADE_RGB = (20, 30, 70)
SHADE_MAX_ALPHA = 170


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def step_meta(i: int) -> dict:
    ts = T0_UTC + dt.timedelta(minutes=30 * i)
    loc = ts + dt.timedelta(hours=2)
    return {"step": i, "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "label_local": loc.strftime("%H:%M")}


# ---------------------------------------------------------------- palettes
def utci_palette() -> tuple[bytes, bytes]:
    vals = np.array([s[0] for s in UTCI_STOPS])
    cols = np.array([s[1] for s in UTCI_STOPS], dtype=float)
    pal = np.zeros((256, 3), np.uint8)
    alpha = np.full(256, 215, np.uint8)
    alpha[0] = 0
    for i in range(1, 255):
        u = UTCI_MIN + (i - 1) * UTCI_STEP
        pal[i] = [np.interp(u, vals, cols[:, c]) for c in range(3)]
    pal[255] = pal[254]
    return pal.tobytes(), alpha.tobytes()


def shade_palette() -> tuple[bytes, bytes]:
    pal = np.tile(np.array(SHADE_RGB, np.uint8), (256, 1))
    alpha = np.zeros(256, np.uint8)
    for i in range(1, 101):  # index = 1 + round(100 * shade_fraction)
        alpha[i] = int(round(SHADE_MAX_ALPHA * (i - 1) / 100))
    return pal.tobytes(), alpha.tobytes()


def to_index(arr: np.ndarray, kind: str) -> np.ndarray:
    out = np.zeros(arr.shape, np.uint8)
    ok = np.isfinite(arr)
    if kind == "utci":
        out[ok] = np.clip(np.rint((arr[ok] - UTCI_MIN) / UTCI_STEP) + 1, 1, 254).astype(np.uint8)
    else:  # shadow: SOLWEIG 1 = sunlit, 0 = shade (0.03 under trees) -> shade fraction
        shade = 1.0 - np.clip(arr[ok], 0, 1)
        out[ok] = (1 + np.rint(100 * shade)).astype(np.uint8)
    return out


# ---------------------------------------------------------------- input
def expand_inputs(patterns: list[str]) -> list[str]:
    out = []
    for p in patterns:
        if p.startswith("gs://") and any(ch in p for ch in "*?["):
            from google.cloud import storage
            bucket, _, pat = p[5:].partition("/")
            prefix = pat.split("*")[0].split("?")[0].split("[")[0]
            names = [b.name for b in storage.Client().list_blobs(bucket, prefix=prefix)]
            out += [f"gs://{bucket}/{n}" for n in sorted(names) if fnmatch.fnmatchcase(n, pat)]
        elif any(ch in p for ch in "*?["):
            import glob
            out += sorted(glob.glob(p))
        else:
            out.append(p)
    return out


def vsi(p: str) -> str:
    return "/vsigs/" + p[5:] if p.startswith("gs://") else p


def localise(paths: list[str], workdir: str = "/tmp/render_in") -> list[str]:
    """Download gs:// inputs once (GDAL /vsigs/ auth is unreliable on Cloud Run; whole-file reads are faster)."""
    if os.environ.get("RENDER_VSIGS") == "1" or not any(p.startswith("gs://") for p in paths):
        return paths
    from google.cloud import storage
    cl = storage.Client(); os.makedirs(workdir, exist_ok=True)

    def dl(p):
        if not p.startswith("gs://"):
            return p
        bucket, _, name = p[5:].partition("/")
        dst = os.path.join(workdir, name.replace("/", "__"))
        if not os.path.exists(dst):
            cl.bucket(bucket).blob(name).download_to_filename(dst)
        return dst

    with cf.ThreadPoolExecutor(8) as ex:
        out = list(ex.map(dl, paths))
    log(f"downloaded {len(out)} inputs")
    return out


def load_mosaic(paths: list[str], bands: list[int]) -> tuple[np.ndarray, rasterio.Affine, rasterio.crs.CRS]:
    """Mosaic the requested bands (1-based) of all inputs into float32 (len(bands), H, W), NaN = nodata."""
    metas = []
    for p in paths:
        with rasterio.open(vsi(p)) as d:
            if d.count < N_STEPS:
                raise SystemExit(f"{p}: {d.count} bands, need {N_STEPS}")
            metas.append((p, d.bounds, d.res, d.crs, d.nodata))
    res = metas[0][2]
    crs = metas[0][3]
    left = min(m[1].left for m in metas); right = max(m[1].right for m in metas)
    bottom = min(m[1].bottom for m in metas); top = max(m[1].top for m in metas)
    W = int(round((right - left) / res[0])); H = int(round((top - bottom) / res[1]))
    log(f"mosaic {len(paths)} inputs -> {W}x{H} px, {len(bands)} bands, crs {crs}")
    mos = np.full((len(bands), H, W), np.nan, np.float32)

    def read_one(m):
        p, b, _, _, nod = m
        with rasterio.open(vsi(p)) as d:
            raw = d.read(bands)
        a = raw.astype(np.float32)
        if raw.dtype == np.uint8:  # T07 sun_solweig.tif: share of direct beam x 250, 255 = nodata
            a[raw == 255] = np.nan
            a /= 250.0
        elif nod is not None and not np.isnan(nod):
            a[a == nod] = np.nan
        r0 = int(round((top - b.top) / res[1])); c0 = int(round((b.left - left) / res[0]))
        return r0, c0, a

    with cf.ThreadPoolExecutor(8) as ex:
        for r0, c0, a in ex.map(read_one, metas):
            sub = mos[:, r0:r0 + a.shape[1], c0:c0 + a.shape[2]]
            np.copyto(sub, a, where=np.isfinite(a))
    return mos, from_origin(left, top, res[0], res[1]), crs


# ---------------------------------------------------------------- tiling
def tile_range(bounds_3857, z):
    n = 2 ** z; size = 2 * WEB_HALF / n
    x0 = int(np.floor((bounds_3857[0] + WEB_HALF) / size)); x1 = int(np.floor((bounds_3857[2] + WEB_HALF) / size))
    y0 = int(np.floor((WEB_HALF - bounds_3857[3]) / size)); y1 = int(np.floor((WEB_HALF - bounds_3857[1]) / size))
    return x0, y0, x1, y1


def nan_down2(a: np.ndarray) -> np.ndarray:
    h, w = a.shape
    b = a.reshape(h // 2, 2, w // 2, 2)
    ok = np.isfinite(b)
    s = np.where(ok, b, 0).sum(axis=(1, 3)); c = ok.sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(c > 0, s / c, np.nan).astype(np.float32)


def render_band(arr2180, src_transform, src_crs, bounds_3857, kind, zmin, zmax, pal, alpha):
    """Return list of (z, x, y, png_bytes) for one band."""
    x0, y0, x1, y1 = tile_range(bounds_3857, zmax)
    size = 2 * WEB_HALF / 2 ** zmax; px = size / TILE
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    dst = np.full((ny * TILE, nx * TILE), np.nan, np.float32)
    dst_t = from_origin(-WEB_HALF + x0 * size, WEB_HALF - y0 * size, px, px)
    reproject(arr2180, dst, src_transform=src_transform, src_crs=src_crs, src_nodata=np.nan,
              dst_transform=dst_t, dst_crs="EPSG:3857", dst_nodata=np.nan, resampling=Resampling.bilinear,
              num_threads=4)
    out = []
    z, tx0, ty0, a = zmax, x0, y0, dst
    while True:
        idx = to_index(a, kind)
        th, tw = idx.shape[0] // TILE, idx.shape[1] // TILE
        for j in range(th):
            for i in range(tw):
                t = idx[j * TILE:(j + 1) * TILE, i * TILE:(i + 1) * TILE]
                if not t.any():
                    continue
                im = Image.fromarray(t, "P"); im.putpalette(pal)
                buf = io.BytesIO(); im.save(buf, "PNG", transparency=alpha, optimize=False, compress_level=6)
                out.append((z, tx0 + i, ty0 + j, buf.getvalue()))
        if z == zmin:
            break
        # pad to even tile alignment, then halve
        pl = TILE if tx0 % 2 else 0; pt = TILE if ty0 % 2 else 0
        w_t = (a.shape[1] + pl) // TILE; h_t = (a.shape[0] + pt) // TILE
        pr = TILE if w_t % 2 else 0; pb = TILE if h_t % 2 else 0
        a = np.pad(a, ((pt, pb), (pl, pr)), constant_values=np.nan)
        a = nan_down2(a)
        tx0 = (tx0 - (1 if pl else 0)) // 2; ty0 = (ty0 - (1 if pt else 0)) // 2; z -= 1
    return out


# ---------------------------------------------------------------- output
class Writer:
    def __init__(self, out: str):
        self.out = out.rstrip("/")
        if self.out.startswith("gs://"):
            from google.cloud import storage
            bucket, _, self.prefix = self.out[5:].partition("/")
            self.bucket = storage.Client().bucket(bucket)
        else:
            self.bucket = None
        self.ex = cf.ThreadPoolExecutor(48)

    def put(self, rel: str, data: bytes, ctype: str, cache="public, max-age=3600"):
        if self.bucket is None:
            path = os.path.join(self.out, rel); os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(data); return
        blob = self.bucket.blob(f"{self.prefix}/{rel}")
        blob.cache_control = cache
        for k in range(5):
            try:
                blob.upload_from_string(data, content_type=ctype); return
            except Exception as e:  # noqa: BLE001
                if k == 4:
                    raise
                time.sleep(1 + 2 * k)

    def put_many(self, items):
        list(self.ex.map(lambda it: self.put(*it), items))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", nargs="+", default=os.environ.get("RENDER_INPUT", "").split() or None, required=False)
    ap.add_argument("--kind", choices=["utci", "shadow"], default=os.environ.get("RENDER_KIND", "utci"))
    ap.add_argument("--source", default=os.environ.get("RENDER_SOURCE"))
    ap.add_argument("--out", default=os.environ.get("RENDER_OUT", "gs://hackaton-honory-heat/T10/frames"))
    ap.add_argument("--minzoom", type=int, default=int(os.environ.get("RENDER_MINZOOM", 12)))
    ap.add_argument("--maxzoom", type=int, default=int(os.environ.get("RENDER_MAXZOOM", 17)))
    ap.add_argument("--steps", default=os.environ.get("RENDER_STEPS", "all"), help="'all' or e.g. '0,14,26'")
    ap.add_argument("--note", default=os.environ.get("RENDER_NOTE", ""))
    a = ap.parse_args(argv)
    if not a.input or not a.source:
        ap.error("--input and --source are required (or RENDER_INPUT / RENDER_SOURCE)")

    task_i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", 0)); task_n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", 1))
    steps = list(range(N_STEPS)) if a.steps == "all" else [int(s) for s in a.steps.split(",")]
    my_steps = [s for s in steps if s % task_n == task_i]
    paths = expand_inputs(a.input)
    if not paths:
        raise SystemExit(f"no inputs match {a.input}")
    log(f"task {task_i}/{task_n}: {len(paths)} inputs, steps {my_steps}")
    src_paths = paths
    paths = localise(paths)

    pal, alpha = utci_palette() if a.kind == "utci" else shade_palette()
    w = Writer(f"{a.out}/{a.source}")
    mos, tr, crs = load_mosaic(paths, [s + 1 for s in my_steps]) if my_steps else (None, None, None)

    if task_i == 0:
        with rasterio.open(vsi(paths[0])) as d:
            crs0 = d.crs
        # bounds of the union
        bs = []
        for p in paths:
            with rasterio.open(vsi(p)) as d:
                bs.append(d.bounds)
        u = (min(b.left for b in bs), min(b.bottom for b in bs), max(b.right for b in bs), max(b.top for b in bs))
        wgs = transform_bounds(crs0, "EPSG:4326", *u, densify_pts=21)
        meta = {
            "source": a.source, "kind": a.kind, "inputs": src_paths, "note": a.note,
            "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bounds_wgs84": [round(v, 6) for v in wgs], "minzoom": a.minzoom, "maxzoom": a.maxzoom,
            "tile_url": "{step:02d}/{z}/{x}/{y}.png",
            "steps": [step_meta(i) for i in range(N_STEPS)],
            "encoding": ({"utci_min": UTCI_MIN, "utci_step": UTCI_STEP, "stops": UTCI_STOPS} if a.kind == "utci"
                         else {"shade_rgb": SHADE_RGB, "max_alpha": SHADE_MAX_ALPHA}),
        }
        w.put("meta.json", json.dumps(meta, indent=1).encode(), "application/json", cache="no-cache")

    for k, s in enumerate(my_steps):
        band = mos[k]
        night = False
        if a.kind == "shadow":
            fin = band[np.isfinite(band)]
            night = fin.size == 0 or bool(np.all(fin >= 0.999))  # SOLWEIG: all-sunlit after sunset; T07: 255 (nodata)
            if night:
                log(f"step {s}: all pixels sunlit -> treated as night, no shade tiles")
        b3857 = transform_bounds(crs, "EPSG:3857", *rasterio.transform.array_bounds(band.shape[0], band.shape[1], tr))
        tiles = [] if night else render_band(band, tr, crs, b3857, a.kind, a.minzoom, a.maxzoom, pal, alpha)
        items = [(f"{s:02d}/{z}/{x}/{y}.png", b, "image/png") for z, x, y, b in tiles]
        w.put_many(items)
        fin = band[np.isfinite(band)]
        stats = {"step": s, "tiles": len(tiles), "night": night,
                 "p05": float(np.percentile(fin, 5)) if fin.size else None,
                 "p50": float(np.percentile(fin, 50)) if fin.size else None,
                 "p95": float(np.percentile(fin, 95)) if fin.size else None}
        w.put(f"{s:02d}/stats.json", json.dumps(stats).encode(), "application/json", cache="no-cache")
        log(f"step {s} ({step_meta(s)['label_local']}): {len(tiles)} tiles, p50 {stats['p50']}")
    log("done")


if __name__ == "__main__":
    sys.exit(main())
