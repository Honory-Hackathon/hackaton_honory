"""Shared T06 configuration: pilot bbox, time steps, forcing, pilot rasters from T01 tiles."""

import io
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pvlib
import requests
from google.cloud import storage

BUCKET = "hackaton-honory-heat"
PREFIX = "T06"

# Pilot core (EPSG:2180, integer metres): Pl. Konstytucji, Pl. Zbawiciela, Marszałkowska canyon,
# Mokotowska, Al. Ujazdowskie and the western edge of Park Ujazdowski. 1200 m x 800 m = 0.96 km².
CORE = (637300, 485100, 638500, 485900)  # west, south, east, north
HALO = 500  # shadow casters only
DOMAIN = (CORE[0] - HALO, CORE[1] - HALO, CORE[2] + HALO, CORE[3] + HALO)
LAT, LON = 52.2195, 21.0200  # pilot centre
TILE = 1000

# 33 analysis steps 04:00Z..20:00Z every 30 min, plus 3 spin-up steps for SOLWEIG's ground-temperature lag.
STEPS = pd.date_range("2026-06-28 04:00", "2026-06-28 20:00", freq="30min")
SPINUP = pd.date_range("2026-06-28 02:30", "2026-06-28 03:30", freq="30min")

ERA5_URL = (
    "https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
    "&start_date=2026-06-28&end_date=2026-06-28&models=era5&timezone=UTC&wind_speed_unit=ms"
    "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,surface_pressure,"
    "shortwave_radiation,direct_normal_irradiance,diffuse_radiation,cloud_cover"
)

# T01 surface_type -> UMEP land cover (1 paved, 2 building, 5 grass, 7 water)
T01_TO_UMEP_LC = {1: 2, 2: 7, 3: 5, 4: 5, 5: 1, 6: 1}

_bucket = None


def bucket():
    global _bucket
    if _bucket is None:
        _bucket = storage.Client().bucket(BUCKET)
    return _bucket


def put_bytes(path, data, content_type=None):
    bucket().blob(f"{PREFIX}/{path}").upload_from_string(data, content_type=content_type)


def put_file(path, local):
    bucket().blob(f"{PREFIX}/{path}").upload_from_filename(local)


def get_bytes(path):
    return bucket().blob(path).download_as_bytes()


def forcing():
    """T05 forcing for the 33 steps; ERA5 (offset to T05's air temperature at 04:00Z) for the spin-up steps."""
    df = era5_forcing()
    t05 = pd.read_csv(io.BytesIO(get_bytes("T05/forcing/forcing_20260628.csv")))
    t05.index = pd.to_datetime(t05["ts_utc"]).dt.tz_localize(None)
    assert len(t05) == len(STEPS) and (t05.index == STEPS).all()
    dta = df.loc[STEPS[0], "ta"] - t05.loc[STEPS[0], "ta_c"]
    df.loc[SPINUP, "ta"] -= dta
    df["ws10_utci"] = np.maximum(df["ws10"], 0.5)
    df["ta_filtry"] = np.nan
    for col, src in [("ta", "ta_c"), ("rh", "rh_pct"), ("ws10", "wind_10m_ms"), ("ws10_utci", "wind_10m_utci_ms"),
                     ("ghi", "ghi_wm2"), ("dni", "dni_wm2"), ("dhi", "dhi_wm2"), ("sun_altitude", "sun_elev_deg"),
                     ("sun_azimuth", "sun_azim_deg"), ("ta_filtry", "ta_filtry_adj_c")]:
        df.loc[STEPS, col] = t05.loc[STEPS, src].values
    df["source"] = np.where(df["spinup"], "ERA5 spin-up (ta shifted to T05 at 04:00Z)", "T05 forcing_20260628")
    df.attrs["source"] = "gs://hackaton-honory-heat/T05/forcing/forcing_20260628.csv; spin-up " + df.attrs["source"]
    return df


def pmwc_core(core=CORE):
    """T05 Landsat surface-heat anomaly (K, 10 m) on the 1 m core grid (nearest; the 10 m cells nest exactly)."""
    from rasterio.io import MemoryFile
    from rasterio.windows import from_bounds
    with MemoryFile(get_bytes("T05/pmwc/pmwc_10m.tif")) as mf, mf.open() as src:
        a = src.read(1, window=from_bounds(*core, transform=src.transform)).astype(np.float32)
    return np.repeat(np.repeat(a, 10, 0), 10, 1)


def era5_forcing():
    """ERA5 (Open-Meteo archive) interpolated to the step times, with pvlib sun position.

    ERA5 temperature, humidity, wind and pressure are instantaneous at the hour; radiation is the
    mean of the preceding hour, so it is placed at HH-0:30 before linear interpolation.
    """
    r = requests.get(ERA5_URL.format(lat=LAT, lon=LON), timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]
    t = pd.to_datetime(h["time"])
    times = SPINUP.append(STEPS)
    x = (times - t[0]).total_seconds().values
    xi = (t - t[0]).total_seconds().values
    xr = xi - 1800.0
    df = pd.DataFrame(index=times)
    df.index.name = "time_utc"
    for k, col in [("temperature_2m", "ta"), ("relative_humidity_2m", "rh"),
                   ("wind_speed_10m", "ws10"), ("surface_pressure", "pressure")]:
        df[col] = np.interp(x, xi, h[k])
    for k, col in [("shortwave_radiation", "ghi"), ("direct_normal_irradiance", "dni"),
                   ("diffuse_radiation", "dhi"), ("cloud_cover", "cloud_cover")]:
        df[col] = np.interp(x, xr if col != "cloud_cover" else xi, h[k])
    sp = pvlib.solarposition.get_solarposition(times.tz_localize("UTC"), LAT, LON)
    df["sun_altitude"] = sp["apparent_elevation"].values
    df["sun_azimuth"] = sp["azimuth"].values
    night = df["sun_altitude"] <= 0
    df.loc[night, ["ghi", "dni", "dhi"]] = 0.0
    df["local_time"] = (times + timedelta(hours=2)).strftime("%H:%M")
    df["spinup"] = df.index < STEPS[0]
    day = pd.date_range("2026-06-28 00:00", "2026-06-28 23:59", freq="5min", tz="UTC")
    altmax = float(pvlib.solarposition.get_solarposition(day, LAT, LON)["apparent_elevation"].max())
    df.attrs["altmax"] = altmax
    df.attrs["source"] = "ERA5 via Open-Meteo archive (models=era5), " + ERA5_URL.format(lat=LAT, lon=LON)
    return df


def pilot_layers(domain=DOMAIN):
    """Mosaic T01 1 km tiles (npz, row 0 = north) and crop to `domain`."""
    w, s, e, n = domain
    e0s = range((w // TILE) * TILE, e, TILE)
    n0s = range((s // TILE) * TILE, n, TILE)
    top = max(n0s) + TILE
    left = min(e0s)
    H, W = top - min(n0s), max(e0s) + TILE - left
    names = ("dtm", "dsm", "building_mask", "water_mask", "canopy_height", "crown_base_height",
             "crown_gap_fraction", "surface_type")
    out = {}
    for e0 in e0s:
        for n0 in n0s:
            z = np.load(io.BytesIO(get_bytes(f"T01/tiles/{e0}_{n0}.npz")))
            r0, c0 = top - (n0 + TILE), e0 - left
            for k in names:
                if k not in out:
                    out[k] = np.zeros((H, W), z[k].dtype)
                out[k][r0:r0 + TILE, c0:c0 + TILE] = z[k]
    rows = slice(top - n, top - s)
    cols = slice(w - left, e - left)
    return {k: v[rows, cols] for k, v in out.items()}


def core_slice(domain=DOMAIN, core=CORE):
    w, s, e, n = domain
    cw, cs, ce, cn = core
    return slice(n - cn, n - cs), slice(cw - w, ce - w)


def transform(domain=DOMAIN):
    from rasterio.transform import from_origin
    return from_origin(domain[0], domain[3], 1.0, 1.0)


def write_cog(path, arr, domain, descriptions=None, nodata=np.nan):
    """Write a (bands, H, W) or (H, W) float32 array as a Cloud-Optimised GeoTIFF."""
    import rasterio
    a = arr[None] if arr.ndim == 2 else arr
    prof = dict(driver="COG", dtype=str(a.dtype), count=a.shape[0], height=a.shape[1], width=a.shape[2],
                crs="EPSG:2180", transform=transform(domain), nodata=nodata, compress="DEFLATE",
                predictor=3 if a.dtype.kind == "f" else 2, blocksize=512, BIGTIFF="IF_SAFER")
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(a)
        if descriptions:
            for i, d in enumerate(descriptions, 1):
                dst.set_band_description(i, d)


def dumps(obj):
    return json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


def now():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
