"""Shared grid, bucket and web-frame helpers for T12 layers."""
import io
import json
import os
import time

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape

BUCKET = "hackaton-honory-heat"
GS = f"gs://{BUCKET}"
PUBLIC = f"https://storage.googleapis.com/{BUCKET}"
OUT_PREFIX = "T12"
WORK = os.environ.get("WORK", "/tmp/t12")
os.makedirs(WORK, exist_ok=True)

# Shared 1 m grid (tasks/README.md)
GE0, GE1, GN0, GN1 = 634000, 642000, 483000, 492000
GW, GH = GE1 - GE0, GN1 - GN0
GRID_TRANSFORM = from_origin(GE0, GN1, 1, 1)

# Web window: AOI bbox + 150 m, snapped to 20 m (so 2/4/10 m reductions divide it)
STEPS = 33
STEP_LABELS = [f"{6 + i // 2:02d}:{30 * (i % 2):02d}" for i in range(STEPS)]
STEP_UTC = [f"2026-06-28T{4 + i // 2:02d}:{30 * (i % 2):02d}:00Z" for i in range(STEPS)]

_gcs = None


def gcs():
    global _gcs
    if _gcs is None:
        from google.cloud import storage
        _gcs = storage.Client(project="hackaton-honory").bucket(BUCKET)
    return _gcs


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fetch(path, local=None):
    """Download gs object (path relative to bucket) to WORK, cached."""
    local = local or os.path.join(WORK, path.replace("/", "__"))
    if not os.path.exists(local):
        gcs().blob(path).download_to_filename(local)
    return local


def exists(path):
    return gcs().blob(path).exists()


def upload(local, path, content_type=None, cache="public, max-age=600"):
    b = gcs().blob(path)
    b.cache_control = cache
    b.upload_from_filename(local, content_type=content_type)
    return f"{GS}/{path}"


def upload_bytes(data, path, content_type, gzip_encode=False):
    b = gcs().blob(path)
    b.cache_control = "public, max-age=600"
    if gzip_encode:
        import gzip
        data = gzip.compress(data, 6)
        b.content_encoding = "gzip"
    b.upload_from_string(data, content_type=content_type)
    return f"{GS}/{path}"


def upload_json(obj, path, gzip_encode=False):
    ct = "application/geo+json" if path.endswith(".geojson") else "application/json"
    return upload_bytes(json.dumps(obj, separators=(",", ":"), allow_nan=False, ensure_ascii=False).encode(), path, ct,
                        gzip_encode)


def aoi_polygon():
    fc = json.load(open(fetch("T01/meta/aoi_halo_grid.geojson")))
    return next(shape(f["geometry"]) for f in fc["features"] if f["properties"].get("name") == "aoi")


def web_window():
    """(e0, n0, e1, n1) of the web extent in EPSG:2180."""
    a = aoi_polygon().bounds
    s = 20
    e0 = int(np.floor((a[0] - 150) / s) * s)
    n0 = int(np.floor((a[1] - 150) / s) * s)
    e1 = int(np.ceil((a[2] + 150) / s) * s)
    n1 = int(np.ceil((a[3] + 150) / s) * s)
    return max(e0, GE0), max(n0, GN0), min(e1, GE1), min(n1, GN1)


def grid_slices(win):
    e0, n0, e1, n1 = win
    return slice(GN1 - n1, GN1 - n0), slice(e0 - GE0, e1 - GE0)


def aoi_mask(win, res=1):
    from rasterio.features import rasterize
    e0, n0, e1, n1 = win
    t = from_origin(e0, n1, res, res)
    return rasterize([aoi_polygon()], out_shape=((n1 - n0) // res, (e1 - e0) // res), transform=t,
                     fill=0, default_value=1, dtype="uint8").astype(bool)


def block_mean(a, k):
    """nan-aware block mean of 2-D or 3-D (.., H, W) array by factor k."""
    if k == 1:
        return a
    sh = a.shape
    r = a.reshape(sh[:-2] + (sh[-2] // k, k, sh[-1] // k, k))
    with np.errstate(invalid="ignore"):
        return np.nanmean(r, axis=(-3, -1))


class WebFrame:
    """Target EPSG:3857 raster for one ground resolution over the web window."""

    def __init__(self, win, ground_res):
        self.win = win
        self.ground_res = ground_res
        e0, n0, e1, n1 = win
        to3857 = Transformer.from_crs(2180, 3857, always_xy=True)
        xs, ys = to3857.transform([e0, e1, e0, e1], [n0, n0, n1, n1])
        to4326 = Transformer.from_crs(3857, 4326, always_xy=True)
        lat = Transformer.from_crs(2180, 4326, always_xy=True).transform((e0 + e1) / 2, (n0 + n1) / 2)[1]
        self.res = ground_res / np.cos(np.radians(lat))
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        self.width = int(np.ceil((x1 - x0) / self.res))
        self.height = int(np.ceil((y1 - y0) / self.res))
        self.transform = from_origin(x0, y1, self.res, self.res)
        x1 = x0 + self.width * self.res
        y0 = y1 - self.height * self.res
        self.bounds_3857 = [x0, y0, x1, y1]
        w, s = to4326.transform(x0, y0)
        e, n = to4326.transform(x1, y1)
        self.bounds_lonlat = [round(w, 7), round(s, 7), round(e, 7), round(n, 7)]
        # MapLibre image-source order: TL, TR, BR, BL
        self.coordinates = [[self.bounds_lonlat[0], self.bounds_lonlat[3]], [self.bounds_lonlat[2], self.bounds_lonlat[3]],
                            [self.bounds_lonlat[2], self.bounds_lonlat[1]], [self.bounds_lonlat[0], self.bounds_lonlat[1]]]

    def warp(self, arr2180, src_res, resampling=Resampling.bilinear):
        """arr2180: 2-D float array on the web window at src_res; returns float32 (H, W) in 3857."""
        e0, n0, e1, n1 = self.win
        out = np.full((self.height, self.width), np.nan, np.float32)
        reproject(np.ascontiguousarray(arr2180, dtype=np.float32), out,
                  src_transform=from_origin(e0, n1, src_res, src_res), src_crs="EPSG:2180", src_nodata=np.nan,
                  dst_transform=self.transform, dst_crs="EPSG:3857", dst_nodata=np.nan, resampling=resampling)
        return out

    def meta(self):
        return {"crs": "EPSG:3857", "width": self.width, "height": self.height,
                "ground_res_m": self.ground_res, "bounds_3857": [round(v, 2) for v in self.bounds_3857],
                "bounds_lonlat": self.bounds_lonlat, "coordinates_maplibre": self.coordinates,
                "deckgl_bitmap_bounds": self.bounds_lonlat}


# ---- colour ramps -------------------------------------------------------------------------------------------

# UTCI stress categories (spec): <26 none, 26-32 moderate, 32-38 strong, 38-46 very strong, >46 extreme
UTCI_STOPS = [(10, (49, 54, 149)), (18, (69, 117, 180)), (22, (116, 173, 209)), (26, (171, 217, 233)),
              (26.01, (255, 255, 191)), (32, (254, 224, 144)), (32.01, (253, 174, 97)), (38, (244, 109, 67)),
              (38.01, (215, 48, 39)), (46, (165, 0, 38)), (46.01, (110, 0, 70)), (54, (60, 0, 60))]
TMRT_STOPS = [(10, (49, 54, 149)), (20, (116, 173, 209)), (30, (255, 255, 191)), (40, (254, 224, 144)),
              (50, (253, 174, 97)), (60, (244, 109, 67)), (70, (215, 48, 39)), (80, (110, 0, 70))]
LST_STOPS = [(20, (49, 54, 149)), (28, (116, 173, 209)), (34, (255, 255, 191)), (40, (253, 174, 97)),
             (46, (215, 48, 39)), (52, (110, 0, 70))]
ALBEDO_STOPS = [(0.0, (20, 20, 30)), (0.08, (70, 60, 90)), (0.14, (150, 120, 120)), (0.2, (220, 190, 150)),
                (0.3, (255, 245, 220)), (0.45, (255, 255, 255))]


def ramp_lut(stops, vmin, vmax, n=256):
    """Palette of n-1 colours for codes 1..n-1 spanning vmin..vmax; code 0 = transparent."""
    xs = np.array([s[0] for s in stops], float)
    cs = np.array([s[1] for s in stops], float)
    vals = vmin + (np.arange(1, n) - 0.5) / (n - 1) * (vmax - vmin)
    lut = np.stack([np.interp(vals, xs, cs[:, i]) for i in range(3)], 1).round().astype(np.uint8)
    return lut, vals


def encode_codes(arr, vmin, vmax, n=256):
    """Float array -> uint8 codes 1..n-1 (0 = nodata)."""
    c = np.clip(np.floor((arr - vmin) / (vmax - vmin) * (n - 1)) + 1, 1, n - 1)
    c[~np.isfinite(arr)] = 0
    return c.astype(np.uint8)


def png_palette(codes, lut, alpha=200):
    """Paletted PNG bytes; code 0 transparent."""
    from PIL import Image
    im = Image.fromarray(codes, mode="P")
    pal = np.zeros((256, 3), np.uint8)
    pal[1:1 + len(lut)] = lut
    im.putpalette(pal.flatten().tolist())
    trans = bytes([0] + [alpha] * 255)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True, transparency=trans)
    return buf.getvalue()


def png_gray(codes):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(codes, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def stops_json(stops):
    return [{"value": v, "color": "#%02x%02x%02x" % c} for v, c in stops]
