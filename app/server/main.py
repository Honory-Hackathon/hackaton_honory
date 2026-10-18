"""heat-app: time-slider UTCI map + trip planner (T10).

Env:
  FRAMES_PREFIX   gs:// prefix of rendered frames (default gs://hackaton-honory-heat/T10/frames)
  UTCI_FRAMES     frame source for UTCI (default pilot_utci)
  SHADE_FRAMES    frame source for shade (default pilot_shadow)
  UTCI_COG        33-band UTCI COG for point queries; may contain {e}_{n} for 1 km tiles
  SHADOW_COG      same for SOLWEIG shadow
  HEAT_SOURCE     passed to router/ (placeholder | stub | final)
  HEAT_VIEWS_URL  base URL of the T13 heat-views service ('' = links shown as 'coming soon')
"""
from __future__ import annotations

import collections
import datetime as dt
import io
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # repo root (router/ lives there) when run from the repo
sys.path.insert(0, "/srv")                    # repo root inside the container
log = logging.getLogger("heat-app")
logging.basicConfig(level=logging.INFO)

FRAMES_PREFIX = os.environ.get("FRAMES_PREFIX", "gs://hackaton-honory-heat/T10/frames").rstrip("/")
UTCI_FRAMES = os.environ.get("UTCI_FRAMES", "pilot_utci")
SHADE_FRAMES = os.environ.get("SHADE_FRAMES", "pilot_shadow")
UTCI_COG = os.environ.get("UTCI_COG", "gs://hackaton-honory-heat/T06/cog/utci_solweig.tif")
SHADOW_COG = os.environ.get("SHADOW_COG", "gs://hackaton-honory-heat/T06/cog/shadow_solweig.tif")
HEAT_VIEWS_URL = os.environ.get("HEAT_VIEWS_URL", "").rstrip("/")

app = FastAPI(title="heat-app")

# ------------------------------------------------------------------ router
ROUTER = {"impl": None, "name": "loading", "error": None}


def _load_router():
    try:
        import router as r  # T09 package
        if hasattr(r, "warmup"):
            r.warmup()
        elif hasattr(r, "get_router"):
            r.get_router()  # loads the bundle + heat once
        ROUTER.update(impl=r, name=f"router ({os.environ.get('HEAT_SOURCE', 'default')})")
        log.info("using T09 router")
    except Exception as e:  # noqa: BLE001
        ROUTER.update(name="failed", error=repr(e))
        log.exception("router/ not usable")


sys.path.insert(0, str(HERE))
_router_thread = threading.Thread(target=_load_router, daemon=True)
_router_thread.start()


def router():
    _router_thread.join(timeout=600)
    if ROUTER["impl"] is None:
        raise HTTPException(503, "router not ready")
    return ROUTER["impl"]


# ------------------------------------------------------------------ GCS frames proxy
_gcs = {"client": None}
_cache: "collections.OrderedDict[str, tuple[bytes, str]]" = collections.OrderedDict()
_cache_lock = threading.Lock()
CACHE_MAX_BYTES = 256 * 1024 * 1024
_cache_bytes = [0]


def gcs():
    if _gcs["client"] is None:
        from google.cloud import storage
        _gcs["client"] = storage.Client()
    return _gcs["client"]


def _empty_png() -> bytes:
    from PIL import Image
    b = io.BytesIO(); Image.new("P", (1, 1)).save(b, "PNG", transparency=0); return b.getvalue()


EMPTY_PNG = _empty_png()


def fetch_gs(uri: str) -> bytes | None:
    with _cache_lock:
        if uri in _cache:
            _cache.move_to_end(uri); return _cache[uri][0]
    bucket, _, name = uri[5:].partition("/")
    try:
        data = gcs().bucket(bucket).blob(name).download_as_bytes()
    except Exception as e:  # noqa: BLE001
        if "404" in str(e) or "No such object" in str(e):
            data = b""
        else:
            raise
    with _cache_lock:
        _cache[uri] = (data, ""); _cache_bytes[0] += len(data)
        while _cache_bytes[0] > CACHE_MAX_BYTES and _cache:
            _, (d, _) = _cache.popitem(last=False); _cache_bytes[0] -= len(d)
    return data or None


@app.get("/frames/{source}/{step}/{z}/{x}/{y}.png")
def frame_tile(source: str, step: int, z: int, x: int, y: int):
    if "/" in source or ".." in source:
        raise HTTPException(400)
    data = fetch_gs(f"{FRAMES_PREFIX}/{source}/{step:02d}/{z}/{x}/{y}.png")
    return Response(data or EMPTY_PNG, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/frames/{source}/meta.json")
def frame_meta(source: str):
    data = fetch_gs(f"{FRAMES_PREFIX}/{source}/meta.json")
    if not data:
        raise HTTPException(404, f"no frames for {source}")
    return Response(data, media_type="application/json", headers={"Cache-Control": "public, max-age=60"})


# ------------------------------------------------------------------ config
@app.get("/api/config")
def config(utci: str | None = None, shade: str | None = None):
    def meta(src):
        d = fetch_gs(f"{FRAMES_PREFIX}/{src}/meta.json")
        return json.loads(d) if d else None
    u = utci or UTCI_FRAMES; s = shade or SHADE_FRAMES
    return {
        "utci": {"source": u, "meta": meta(u)},
        "shade": {"source": s, "meta": meta(s)},
        "router": ROUTER["name"], "router_error": ROUTER["error"],
        "heat_views_url": HEAT_VIEWS_URL,
        "views": [{"path": "/routes/", "pl": "Mapa tras", "en": "Route heat map"},
                  {"path": "/shadows3d/", "pl": "Cienie 3D", "en": "3D shadows"},
                  {"path": "/temperature/", "pl": "Temperatura i albedo", "en": "Temperature & albedo"}],
    }


# ------------------------------------------------------------------ point time series
_tok = {"value": None, "exp": 0}
SNAP_M = 15


def _token() -> str:
    if time.time() < _tok["exp"] - 120:
        return _tok["value"]
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
    creds.refresh(google.auth.transport.requests.Request())
    _tok.update(value=creds.token, exp=time.time() + 3000)
    return creds.token


def _sample(uri_t: str, x: float, y: float):
    import rasterio
    e, n = int(x // 1000 * 1000), int(y // 1000 * 1000)
    uri = uri_t.replace("{e}", str(e)).replace("{n}", str(n))
    url = "/vsicurl/https://storage.googleapis.com/" + uri[5:]
    with rasterio.Env(GDAL_HTTP_HEADERS=f"Authorization: Bearer {_token()}", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(url) as d:
            r, c = d.index(x, y)
            if not (0 <= r < d.height and 0 <= c < d.width):
                return None, None
            # walkable-only rasters (T07): snap to the nearest pixel with data within SNAP_M
            r0, c0 = max(0, r - SNAP_M), max(0, c - SNAP_M)
            win = d.read(window=((r0, min(d.height, r + SNAP_M + 1)), (c0, min(d.width, c + SNAP_M + 1))))
            u8 = d.dtypes[0] == "uint8"  # T07 sun_solweig.tif: share x 250, 255 = nodata
            valid = (win != 255).any(axis=0) if u8 else np.isfinite(win).any(axis=0)
            if not valid.any():
                return None, None
            rr, cc = np.nonzero(valid)
            k = int(np.argmin((rr + r0 - r) ** 2 + (cc + c0 - c) ** 2))
            dist = float(np.hypot(rr[k] + r0 - r, cc[k] + c0 - c))
            v = win[:, rr[k], cc[k]]
    if u8:
        return [None if int(v_) == 255 else round(int(v_) / 250, 3) for v_ in v], dist
    return [None if v_ != v_ else round(float(v_), 2) for v_ in v], dist


@app.get("/api/point")
def point(lon: float, lat: float):
    from pyproj import Transformer
    x, y = Transformer.from_crs(4326, 2180, always_xy=True).transform(lon, lat)
    out = {"lon": lon, "lat": lat, "x": round(x, 1), "y": round(y, 1)}
    for k, uri in (("utci", UTCI_COG), ("shadow", SHADOW_COG)):
        try:
            out[k], out[f"{k}_snap_m"] = _sample(uri, x, y)
        except Exception as e:  # noqa: BLE001
            out[k] = None; out[f"{k}_error"] = type(e).__name__
    return out


# ------------------------------------------------------------------ planner
@app.post("/api/plan")
async def api_plan(req: Request):
    try:
        b = await req.json()
        o = [float(v) for v in b["origin"]]; d = [float(v) for v in b["destination"]]
        e = str(b.get("earliest", "08:00")); l = str(b.get("latest", "09:00"))
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "need origin [lon,lat], destination [lon,lat], earliest, latest")
    t0 = time.time()
    res = router().plan(tuple(o), tuple(d), e, l, weights=b.get("weights"))
    res.setdefault("notes", [])
    res["router"] = ROUTER["name"]; res["elapsed_s"] = round(time.time() - t0, 2)
    return JSONResponse(res)


@app.post("/api/price")
async def api_price(req: Request):
    try:
        b = await req.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "body must be JSON {coords, depart}")
    if not hasattr(router(), "price_path"):
        raise HTTPException(501, "router has no price_path")
    res = router().price_path([tuple(c) for c in b["coords"]], str(b.get("depart", "08:00")), weights=b.get("weights"))
    return JSONResponse(res)


@app.get("/api/health")
def healthz():
    return {"ok": True, "router": ROUTER["name"]}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
