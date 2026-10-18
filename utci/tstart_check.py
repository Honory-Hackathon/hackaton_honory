"""Ground-temperature sign check for solweig 0.1.0b96 without a land-cover grid.

Synthetic 160 x 160 m flat scene with one 20 m block (land cover: block = building), T05 forcing, three runs:
  A  no land cover (what the district run does)
  B  land cover all 0 (cobblestone), Tstart = -3.41 (UMEP land-cover path), albedo 0.15 as A
  C  same as B with Tstart = +3.41 (UMEP no-land-cover path, Solweig_run.py)
A == B shows which sign the default applies; C - A is the size of the effect.
Usage: python tstart_check.py forcing_20260628.csv out.json
"""

import json
import sys
import tempfile
from datetime import datetime

import numpy as np
import pandas as pd
import rasterio

import solweig
from solweig.loaders import load_params

LAT, LON = 52.2195, 21.02


def write(p, a, x0=637000.0, y0=486160.0):
    with rasterio.open(p, "w", driver="GTiff", width=a.shape[1], height=a.shape[0], count=1, dtype="float32",
                       crs="EPSG:2180", transform=rasterio.transform.from_origin(x0, y0, 1, 1)) as d:
        d.write(a.astype(np.float32), 1)


def main(csv, out):
    solweig.disable_gpu()
    f = pd.read_csv(csv)
    f["t"] = pd.to_datetime(f["ts_utc"]).dt.tz_localize(None)
    altmax = 61.3
    n = 160
    dem = np.full((n, n), 100.0)
    dsm = dem.copy()
    dsm[60:100, 60:100] += 20.0
    work = tempfile.mkdtemp()
    write(f"{work}/dem.tif", dem)
    write(f"{work}/dsm.tif", dsm)
    lc = np.zeros((n, n))
    lc[60:100, 60:100] = 2  # block = building (UMEP id 2), ground = cobblestone (0)
    write(f"{work}/lc.tif", lc)
    weather = [solweig.Weather(datetime=r.t.to_pydatetime(), ta=float(r.ta_c), rh=float(r.rh_pct),
                               global_rad=float(r.ghi_wm2), ws=float(r.wind_10m_ms), pressure=1010.0,
                               timestep_minutes=30.0, measured_direct_rad=float(r.dni_wm2),
                               measured_diffuse_rad=float(r.dhi_wm2), precomputed_sun_altitude=float(r.sun_elev_deg),
                               precomputed_sun_azimuth=float(r.sun_azim_deg), precomputed_altmax=altmax)
               for r in f.itertuples()]
    res = {}
    for name, lc, tstart in (("A_default", False, None), ("B_lc_m3.41", True, -3.41), ("C_lc_p3.41", True, 3.41)):
        kw = dict(dsm=f"{work}/dsm.tif", dem=f"{work}/dem.tif")
        mat = None
        if lc:
            kw["land_cover"] = f"{work}/lc.tif"
            mat = load_params()
            mat.Albedo.Effective.Value.Cobble_stone_2014a = 0.15
            mat.Tstart.Value.Cobble_stone_2014a = tstart
        surf = solweig.SurfaceData.prepare(working_dir=f"{work}/c_{name}", **kw)
        od = f"{work}/o_{name}"
        solweig.calculate(surf, weather=weather, location=solweig.Location(latitude=LAT, longitude=LON, utc_offset=0),
                          output_dir=od, use_anisotropic_sky=False, outputs=["tmrt", "shadow"], materials=mat)
        import glob
        files = sorted(glob.glob(f"{od}/tmrt/*.tif"))
        sh = sorted(glob.glob(f"{od}/shadow/*.tif"))
        tm = np.stack([rasterio.open(p).read(1) for p in files])
        sd = np.stack([rasterio.open(p).read(1) for p in sh])
        res[name] = (tm, sd)
    tA, sA = res["A_default"]
    out_d = dict(files=len(tA), steps=[])
    # open sunlit ground pixel far from block, and a shaded ground pixel north of block
    sun_px = (20, 20)
    for i, r in enumerate(f.itertuples()):
        row = dict(local=r.local_label, ta=r.ta_c)
        for k, (tm, sd) in res.items():
            row[f"{k}_tmrt_open"] = round(float(tm[i][sun_px]), 2)
            row[f"{k}_shadow_open"] = round(float(sd[i][sun_px]), 2)
        out_d["steps"].append(row)
    tB = res["B_lc_m3.41"][0]
    tC = res["C_lc_p3.41"][0]
    out_d["max_abs_A_minus_B"] = float(np.nanmax(np.abs(tA - tB)))
    away = np.ones(tA.shape[1:], bool)
    away[50:110, 50:110] = False  # block 60:100 plus 10 px
    out_d["max_abs_A_minus_B_away_from_block"] = float(np.nanmax(np.abs(tA - tB)[:, away]))
    out_d["max_C_minus_A_away_from_block"] = float(np.nanmax((tC - tA)[:, away]))
    from pythermalcomfort.models import utci as utci_fn
    ws = np.maximum(f["wind_10m_ms"].to_numpy(), 0.5)
    ua = utci_fn(tdb=f["ta_c"].to_numpy(), tr=tA[:, sun_px[0], sun_px[1]], v=ws, rh=f["rh_pct"].to_numpy(), limit_inputs=False).utci
    uc = utci_fn(tdb=f["ta_c"].to_numpy(), tr=tC[:, sun_px[0], sun_px[1]], v=ws, rh=f["rh_pct"].to_numpy(), limit_inputs=False).utci
    out_d["utci_C_minus_A_open_px"] = [round(float(x), 2) for x in (uc - ua)]
    dCA = tC - tA
    out_d["C_minus_A_open_px"] = [round(float(x), 2) for x in dCA[:, sun_px[0], sun_px[1]]]
    out_d["C_minus_A_max"] = float(np.nanmax(dCA))
    json.dump(out_d, open(out, "w"), indent=1)
    print(json.dumps({k: v for k, v in out_d.items() if k != "steps"}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
