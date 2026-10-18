"""T06 Cloud Run entry point.

Task index 0 (or MODE=pilot): SOLWEIG on the pilot (default surfaces, then with T01 land cover),
shade zones from SOLWEIG's own shadow functions, analysis bundle -> gs://hackaton-honory-heat/T06/.
Task index >= 1 (or MODE=bench): runtime benchmark on a square crop from BENCH.
MODE=district: runtime of the whole shared T01 grid (8 x 9 km) in one call, summary grids only.
MODE=tile: one 1 km core of the district (TILES = comma list "e0_n0", picked by task index), halo 500 m,
or 1000 m when a building over 100 m stands in the 1 km ring -> Tmrt/shadow/UTCI COGs for the core.
MODE=mosaic: tile cores -> district COGs (33 bands) in T06/district/.
MODE=analyze: comparison only, from the uploaded bundle.
"""

import io
import json
import logging
import os
import platform
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

BENCH = [  # task index 1.. -> config
    dict(size=256, threads=8),
    dict(size=512, threads=8),
    dict(size=1024, threads=8),
    dict(size=1448, threads=8),
    dict(size=512, threads=4),
    dict(size=512, threads=2),
    dict(size=512, threads=1),
]

TASK = int(os.environ.get("TASK_BASE", "0")) + int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
MODE = os.environ.get("MODE") or ("pilot" if TASK == 0 else "bench")
CFG = BENCH[TASK - 1] if MODE == "bench" else dict(threads=int(os.environ.get("THREADS", os.cpu_count())))
os.environ["RAYON_NUM_THREADS"] = str(CFG["threads"])

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import common as C  # noqa: E402

LOG = []


class _Capture(logging.Handler):
    def emit(self, record):
        LOG.append((round(time.time() - T0, 2), record.getMessage()))


T0 = time.time()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger().addHandler(_Capture())


def write_inputs(layers, domain, d, land_cover):
    d.mkdir(parents=True, exist_ok=True)
    cdsm = np.where(layers["canopy_height"] > 0, layers["canopy_height"], 0).astype(np.float32)
    C.write_cog(d / "dsm.tif", layers["dsm"].astype(np.float32), domain)
    C.write_cog(d / "dem.tif", layers["dtm"].astype(np.float32), domain)
    C.write_cog(d / "cdsm.tif", cdsm, domain)
    paths = dict(dsm=d / "dsm.tif", dem=d / "dem.tif", cdsm=d / "cdsm.tif")
    if land_cover:
        lc = np.ones(layers["surface_type"].shape, np.uint8)
        for k, v in C.T01_TO_UMEP_LC.items():
            lc[layers["surface_type"] == k] = v
        C.write_cog(d / "lc.tif", lc, domain, nodata=0)
        paths["land_cover"] = d / "lc.tif"
    return {k: str(v) for k, v in paths.items()}


def weather_list(df):
    import solweig
    out = []
    for ts, r in df.iterrows():
        out.append(solweig.Weather(
            datetime=ts.to_pydatetime(), ta=float(r.ta), rh=float(r.rh), global_rad=float(r.ghi),
            ws=float(r.ws10), pressure=float(r.pressure), timestep_minutes=30.0,
            measured_direct_rad=float(r.dni), measured_diffuse_rad=float(r.dhi),
            precomputed_sun_altitude=float(r.sun_altitude), precomputed_sun_azimuth=float(r.sun_azimuth),
            precomputed_altmax=df.attrs["altmax"]))
    return out


def run_solweig(layers, domain, work, df, land_cover=False, outputs=("tmrt", "shadow"), cache=None, svf_dir=None, steps=True):
    """Prepare (walls + SVF, or SVF from `svf_dir`) and run all steps. Returns (surface, out_dir, timings)."""
    import solweig
    solweig.disable_gpu()
    paths = write_inputs(layers, domain, work / "in", land_cover)
    t = time.time()
    n_log = len(LOG)
    surface = solweig.SurfaceData.prepare(working_dir=str(cache or work / "cache"), svf_dir=svf_dir, **paths)
    if svf_dir:  # loaded into memory; /tmp on Cloud Run is RAM
        shutil.rmtree(svf_dir, ignore_errors=True)
    if not steps:
        return surface, None, dict(prepare_s=round(time.time() - t, 1), prepare_log=LOG[n_log:])
    t_prep = time.time() - t
    prep_log = LOG[n_log:]
    stamps = []
    out_dir = work / "out"
    t = time.time()
    solweig.calculate(surface, weather=weather_list(df), location=solweig.Location(latitude=C.LAT, longitude=C.LON, utc_offset=0),
                      output_dir=str(out_dir), use_anisotropic_sky=False, outputs=list(outputs) if outputs else None,
                      progress_callback=lambda i, n: stamps.append((i, n, time.time())))
    t_calc = time.time() - t
    tim = dict(prepare_s=round(t_prep, 1), calculate_s=round(t_calc, 1), n_steps=len(df),
               per_step_s=round(t_calc / len(df), 3), prepare_log=prep_log,
               step_stamps=[(i, n, round(s - t, 2)) for i, n, s in stamps])
    return surface, out_dir, tim


def zones(surface, df):
    """Shade zone per pixel and step from SOLWEIG's shadow function: 0 sun, 1 bldg, 2 tree, 3 both, 255 night."""
    return zones_from_arrays(np.asarray(surface.dsm, np.float32), np.asarray(surface.dem, np.float32),
                             np.asarray(surface.cdsm, np.float32), np.asarray(surface.tdsm, np.float32), df)


def zones_from_layers(layers, df, trunk_ratio=0.25, min_object_height=1.0):
    """Same as zones() from raw T01 layers, rebuilding SOLWEIG's absolute CDSM/TDSM (trunk = 25 % of crown height)."""
    dem = layers["dtm"].astype(np.float32)
    dsm = layers["dsm"].astype(np.float32)
    dsm = np.where(dsm - dem < min_object_height, dem, dsm)
    h = layers["canopy_height"].astype(np.float32)
    veg = h >= min_object_height
    cdsm = np.where(veg, dem + h, np.nan).astype(np.float32)
    tdsm = np.where(veg, dem + trunk_ratio * h, np.nan).astype(np.float32)
    return zones_from_arrays(dsm, dem, cdsm, tdsm, df)


def zones_from_arrays(dsm, dem, cdsm, tdsm, df):
    """SOLWEIG's veg_sh is cleared where buildings already shade, so vegetation shadow on its own comes from a
    second pass over terrain + canopy only; `both` = building shadow and vegetation shadow."""
    from solweig.rustalgos import shadowing as sh
    bush = np.zeros_like(dsm)
    amax = float(np.nanmax(dsm) - np.nanmin(dsm))
    z = np.full((len(df),) + dsm.shape, 255, np.uint8)
    t = time.time()
    for i, (_, r) in enumerate(df.iterrows()):
        if r.sun_altitude <= 0:
            continue
        res = sh.calculate_shadows_wall_ht_25(float(r.sun_azimuth), float(r.sun_altitude), 1.0, amax, dsm, cdsm, tdsm, bush,
                                              None, None, None, None, None, None)
        b = np.asarray(res.bldg_sh) < 0.5
        res_v = sh.calculate_shadows_wall_ht_25(float(r.sun_azimuth), float(r.sun_altitude), 1.0, amax, dem, cdsm, tdsm, bush,
                                                None, None, None, None, None, None)
        v = (np.asarray(res_v.veg_sh) < 0.5) | (np.asarray(res.veg_sh) < 0.5)
        z[i] = b.astype(np.uint8) + 2 * v.astype(np.uint8)
    return z, round(time.time() - t, 1)


def read_stack(out_dir, name, df):
    import rasterio
    arrs = []
    for ts in df.index:
        f = list(Path(out_dir).rglob(f"{name}_{ts.strftime('%Y%m%d_%H%M')}.tif"))
        assert len(f) == 1, (name, ts, f)
        with rasterio.open(f[0]) as src:
            arrs.append(src.read(1).astype(np.float32))
    return np.stack(arrs)


def env_info():
    return dict(cpu_count=os.cpu_count(), threads=CFG["threads"], python=platform.python_version(),
                machine=platform.machine(), maxrss_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 2),
                cloud_run_execution=os.environ.get("CLOUD_RUN_EXECUTION"), task=TASK)


def pilot():
    import solweig
    work = Path(tempfile.mkdtemp())
    df = C.forcing()
    C.put_bytes("forcing/forcing_2026-06-28.csv", df.to_csv().encode())
    t = time.time()
    layers = C.pilot_layers()
    t_layers = time.time() - t
    steps = ~df["spinup"].values
    rs, cs = C.core_slice()
    tims = {"solweig_version": solweig.__version__, "layers_s": round(t_layers, 1), "domain": C.DOMAIN, "core": C.CORE,
            "domain_mpx": layers["dsm"].size / 1e6, "max_height_m": float(np.nanmax(layers["dsm"] - layers["dtm"]))}

    surface, out_dir, tims["default"] = run_solweig(layers, C.DOMAIN, work / "default", df)
    z, tims["zones_s"] = zones(surface, df)
    tmrt = read_stack(out_dir, "tmrt", df)
    shadow = read_stack(out_dir, "shadow", df)
    svf = {k: np.asarray(getattr(surface.svf, k), np.float32)[rs, cs] for k in ("svf", "svf_veg", "svfbuveg")}
    del surface
    _, out_lc, tims["landcover"] = run_solweig(layers, C.DOMAIN, work / "lc", df, land_cover=True, outputs=("tmrt",),
                                               cache=work / "default" / "cache")
    tmrt_lc = read_stack(out_lc, "tmrt", df)
    tims["env"] = env_info()
    C.put_bytes("timings/pilot.json", C.dumps(tims).encode())

    bundle = dict(pmwc=C.pmwc_core(), tmrt=tmrt[steps][:, rs, cs], tmrt_lc=tmrt_lc[steps][:, rs, cs], shadow=shadow[steps][:, rs, cs],
                  zone=z[steps][:, rs, cs], **svf,
                  **{k: layers[k][rs, cs] for k in ("building_mask", "water_mask", "surface_type", "canopy_height",
                                                    "dsm", "dtm", "crown_base_height")})
    buf = io.BytesIO()
    np.savez_compressed(buf, **bundle)
    C.put_bytes("work/bundle.npz", buf.getvalue())
    C.put_bytes("work/forcing.json", df[steps].reset_index().to_json(orient="records", date_format="iso").encode())
    print(json.dumps({k: v for k, v in tims.items() if k not in ("default", "landcover")}, default=str))
    import analyze
    analyze.main(bundle, df[steps])


def bench():
    size = CFG["size"]
    cx, cy = (C.CORE[0] + C.CORE[2]) // 2, (C.CORE[1] + C.CORE[3]) // 2
    domain = (cx - size // 2, cy - size // 2, cx - size // 2 + size, cy - size // 2 + size)
    work = Path(tempfile.mkdtemp())
    df = C.forcing()
    layers = C.pilot_layers(domain)
    surface, _, tim = run_solweig(layers, domain, work, df, outputs=None)
    _, tim["zones_s"] = zones(surface, df)
    tim.update(size=size, mpx=size * size / 1e6, max_height_m=float(np.nanmax(layers["dsm"] - layers["dtm"])),
               domain=domain, env=env_info())
    name = f"bench_{size}_t{CFG['threads']}"
    C.put_bytes(f"timings/{name}.json", C.dumps(tim).encode())
    print(json.dumps({k: v for k, v in tim.items() if k not in ("prepare_log", "step_stamps")}, default=str))


def district():
    """Whole T01 grid in one SOLWEIG call: the runtime number that matters for a district-wide run."""
    grid = json.loads(C.get_bytes("T01/meta/grid.json"))
    domain = tuple(int(v) for v in grid["bounds"])
    work = Path(tempfile.mkdtemp())
    df = C.forcing()
    t = time.time()
    layers = C.pilot_layers(domain)
    t_layers = time.time() - t
    _, _, tim = run_solweig(layers, domain, work, df, outputs=None)
    tim.update(layers_s=round(t_layers, 1), mpx=layers["dsm"].size / 1e6, domain=domain,
               max_height_m=float(np.nanmax(layers["dsm"] - layers["dtm"])), env=env_info())
    C.put_bytes("timings/district.json", C.dumps(tim).encode())
    print(json.dumps({k: v for k, v in tim.items() if k not in ("prepare_log", "step_stamps")}, default=str))


SVF_ZIP_NAMES = {  # SvfArrays field -> file name in UMEP's svfs.zip
    "svf": "svf", "svf_north": "svfN", "svf_east": "svfE", "svf_south": "svfS", "svf_west": "svfW",
    "svf_veg": "svfveg", "svf_veg_north": "svfNveg", "svf_veg_east": "svfEveg", "svf_veg_south": "svfSveg",
    "svf_veg_west": "svfWveg", "svf_aveg": "svfaveg", "svf_aveg_north": "svfNaveg", "svf_aveg_east": "svfEaveg",
    "svf_aveg_south": "svfSaveg", "svf_aveg_west": "svfWaveg"}


def pad_svf(surface, sdom, domain, out):
    """svfs.zip + shadowmats.npz for `domain` from a surface prepared on the smaller `sdom` (halo filled with 1 / 0).

    A zip without cache metadata is accepted by shape, whereas SOLWEIG's memmap cache is hash-checked against the DSM.
    """
    import zipfile
    import rasterio
    rs, cs = C.core_slice(domain, sdom)
    H, W = domain[3] - domain[1], domain[2] - domain[0]
    out.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", dtype="float32", count=1, height=H, width=W, crs="EPSG:2180", transform=C.transform(domain))
    with zipfile.ZipFile(out / "svfs.zip", "w", zipfile.ZIP_STORED) as zf:
        for field, fname in SVF_ZIP_NAMES.items():
            a = np.ones((H, W), np.float32)
            a[rs, cs] = np.asarray(getattr(surface.svf, field), np.float32)
            tif = out / f"{fname}.tif"
            with rasterio.open(tif, "w", **prof) as dst:
                dst.write(a, 1)
            zf.write(tif, f"{fname}.tif")
            tif.unlink()
    sm = surface.shadow_matrices
    mats = {}
    for key, attr in (("shadowmat", "_shmat_u8"), ("vegshadowmat", "_vegshmat_u8"), ("vbshmat", "_vbshmat_u8")):
        src = np.asarray(getattr(sm, attr))
        a = np.zeros((H, W, src.shape[2]), np.uint8)
        a[rs, cs] = src
        mats[key] = a
    np.savez(out / "shadowmats.npz", patch_count=np.array(sm.patch_count), **mats)
    return str(out)


def tile():
    import analyze
    name = os.environ["TILES"].split(",")[int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))]
    e0, n0 = (int(v) for v in name.split("_"))
    core = (e0, n0, e0 + C.TILE, n0 + C.TILE)
    t0 = time.time()
    ring = (e0 - 1000, n0 - 1000, e0 + C.TILE + 1000, n0 + C.TILE + 1000)
    big = C.pilot_layers(ring)
    hb = np.where(big["building_mask"] > 0, big["dsm"] - big["dtm"], 0)
    rs, cs = C.core_slice(ring, core)
    hb[rs, cs] = 0
    ring_hmax = float(hb.max())
    halo = 1000 if ring_hmax > 100 else 500
    domain = (e0 - halo, n0 - halo, e0 + C.TILE + halo, n0 + C.TILE + halo)
    rd, cd = C.core_slice(ring, domain)
    layers = {k: v[rd, cd] for k, v in big.items()}
    margin = int(os.environ.get("SVF_MARGIN", "0"))
    svf_dir, tim_svf = None, None
    work = Path(tempfile.mkdtemp())
    df = C.forcing()
    steps = ~df["spinup"].values
    if margin and margin < halo:
        # SVF only matters on the core (halo pixels are discarded): compute it on core + margin,
        # pad to the full shadow-caster domain and hand it to SOLWEIG.
        sdom = (e0 - margin, n0 - margin, e0 + C.TILE + margin, n0 + C.TILE + margin)
        rs_, cs_ = C.core_slice(ring, sdom)
        surf, _, tim_svf = run_solweig({k: v[rs_, cs_] for k, v in big.items()}, sdom, work / "svf", df, steps=False)
        svf_dir = pad_svf(surf, sdom, domain, work / "svf_full")
        del surf
        shutil.rmtree(work / "svf", ignore_errors=True)
    del big, hb
    _, out_dir, tim = run_solweig(layers, domain, work, df, outputs=("tmrt", "shadow"), svf_dir=svf_dir)
    if tim_svf:
        tim["svf_pass"] = dict(margin_m=margin, prepare_s=tim_svf["prepare_s"], prepare_log=tim_svf["prepare_log"])
    rs, cs = C.core_slice(domain, core)
    f = df[steps]
    desc = [f"2026-06-28 {t} CEST" for t in f["local_time"]]
    tmrt = read_stack(out_dir, "tmrt", f)[:, rs, cs]
    shadow = read_stack(out_dir, "shadow", f)[:, rs, cs]
    ta, ws, rh = f["ta"].values, f["ws10_utci"].values, f["rh"].values
    utci = analyze.utci_stack(ta, tmrt, ws, rh, np.isfinite(tmrt[0]))
    d = Path(tempfile.mkdtemp())
    for nm, a in (("tmrt", tmrt), ("shadow", shadow), ("utci", utci)):
        C.write_cog(d / f"{nm}.tif", a.astype(np.float32), core, desc)
        C.put_file(f"district/tiles/{name}/{nm}.tif", str(d / f"{nm}.tif"))
    tim.update(tile=name, halo_m=halo, ring_hmax_m=ring_hmax, domain=domain, mpx=layers["dsm"].size / 1e6,
               max_height_m=float(np.nanmax(layers["dsm"] - layers["dtm"])), utci_nan=int(np.isnan(utci).sum()),
               total_s=round(time.time() - t0, 1), env=env_info())
    C.put_bytes(f"district/tiles/{name}/timing.json", C.dumps(tim).encode())
    print(json.dumps({k: v for k, v in tim.items() if k not in ("prepare_log", "step_stamps")}, default=str))


def mosaic():
    import rasterio
    import rasterio.shutil
    from rasterio.io import MemoryFile
    from rasterio.windows import from_bounds
    names = sorted({b.name.split("/")[-2] for b in C.bucket().list_blobs(prefix=f"{C.PREFIX}/district/tiles/")
                    if b.name.endswith("timing.json")})
    xy = [tuple(int(v) for v in n.split("_")) for n in names]
    bounds = (min(e for e, _ in xy), min(n for _, n in xy), max(e for e, _ in xy) + C.TILE, max(n for _, n in xy) + C.TILE)
    d = Path(tempfile.mkdtemp())
    timings = [json.loads(C.get_bytes(f"{C.PREFIX}/district/tiles/{n}/timing.json")) for n in names]
    for var in ("tmrt", "shadow", "utci"):
        tmp = d / f"{var}.tmp.tif"
        prof = None
        for i, n in enumerate(names):
            with MemoryFile(C.get_bytes(f"{C.PREFIX}/district/tiles/{n}/{var}.tif")) as mf, mf.open() as src:
                a, desc = src.read(), src.descriptions
            if prof is None:
                prof = dict(driver="GTiff", dtype="float32", count=a.shape[0], crs="EPSG:2180", nodata=np.nan,
                            width=bounds[2] - bounds[0], height=bounds[3] - bounds[1], transform=C.transform(bounds),
                            tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE", predictor=3, BIGTIFF="YES")
                dst = rasterio.open(tmp, "w", **prof)
                for b, t in enumerate(desc, 1):
                    dst.set_band_description(b, t)
            e0, n0 = xy[i]
            dst.write(a, window=from_bounds(e0, n0, e0 + C.TILE, n0 + C.TILE, transform=dst.transform))
        dst.close()
        rasterio.shutil.copy(tmp, d / f"{var}.tif", driver="COG", compress="DEFLATE", predictor=3, BIGTIFF="YES")
        tmp.unlink()
        C.put_file(f"district/{var}_20260628.tif", str(d / f"{var}.tif"))
        (d / f"{var}.tif").unlink()
        print(f"{C.now()} {var} done", flush=True)
    C.put_bytes("district/tiles_summary.json", C.dumps(
        [{k: t[k] for k in ("tile", "halo_m", "ring_hmax_m", "mpx", "max_height_m", "prepare_s", "calculate_s", "total_s", "utci_nan")}
         | {"maxrss_gb": t["env"]["maxrss_gb"], "cpu": t["env"]["threads"]} for t in timings]).encode())


if __name__ == "__main__":
    print(f"{C.now()} mode={MODE} task={TASK} cfg={CFG}", flush=True)
    if MODE == "pilot":
        pilot()
    elif MODE == "bench":
        bench()
    elif MODE == "mosaic":
        mosaic()
    elif MODE == "tile":
        tile()
    elif MODE == "district":
        district()
    elif MODE == "analyze":
        import analyze
        z = np.load(io.BytesIO(C.get_bytes(f"{C.PREFIX}/work/bundle.npz")))
        b = {k: z[k] for k in z.files}
        f = C.forcing()
        f = f[~f["spinup"]]
        if os.environ.get("REZONE"):
            zz, zt = zones_from_layers(C.pilot_layers(), f)
            rs, cs = C.core_slice()
            b["zone"] = zz[:, rs, cs]
            print(f"rezoned in {zt} s", flush=True)
            buf = io.BytesIO()
            np.savez_compressed(buf, **b)
            C.put_bytes("work/bundle.npz", buf.getvalue())
        analyze.main(b, f)
    else:
        sys.exit(f"unknown MODE {MODE}")
