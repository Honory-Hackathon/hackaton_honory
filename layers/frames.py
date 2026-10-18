"""Per-step web frames (shadow, UTCI, Tmrt), static maps (albedo, LST) and per-surface series.

MODE=frames SOURCE=t02      -> shadow frames from T02 shares (full grid, available early)
MODE=frames SOURCE=solweig  -> shadow/UTCI/Tmrt frames + series from T06 district tiles
MODE=static                 -> albedo (Sentinel-2) and LST (Landsat) frames + static rasters
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from rasterio.transform import from_origin
from rasterio.warp import Resampling

from common import (ALBEDO_STOPS, GE0, GN1, LST_STOPS, STEP_LABELS, STEP_UTC, STEPS, TMRT_STOPS, UTCI_STOPS,
                    WebFrame, aoi_mask, block_mean, encode_codes, exists, fetch, gcs, grid_slices, log, png_gray,
                    png_palette, ramp_lut, upload, upload_bytes, upload_json, web_window, WORK)

OUT = "T12"
SHADOW_RES = 2
THERMAL_RES = 4
STATIC_RES = 10
UTCI_RANGE = (10.0, 54.0)
TMRT_RANGE = (10.0, 80.0)

# surface classes for series (T01 surface_type + canopy)
CLASSES = ["paved", "grass", "shrubs", "water", "under_trees", "other"]


def read_grid_window(path, win, band=1):
    r0, c0 = grid_slices(win)
    with rasterio.open(fetch(path)) as src:
        return src.read(band, window=Window(c0.start, r0.start, c0.stop - c0.start, r0.stop - r0.start))


def forcing():
    return pd.read_csv(fetch("T05/forcing/forcing_20260628.csv"))


# ---------------------------------------------------------------- shadow encoding

def shadow_palette():
    """codes 1..8 building shade levels, 9..16 tree shade levels; alpha grows with shade share."""
    pal = np.zeros((256, 3), np.uint8)
    alpha = np.zeros(256, np.uint8)
    for i in range(8):
        a = int(round(60 + (i + 1) / 8 * 150))
        pal[1 + i] = (30, 35, 60)
        alpha[1 + i] = a
        pal[9 + i] = (20, 70, 40)
        alpha[9 + i] = a
    return pal, alpha


def encode_shadow(bldg, tree):
    """bldg/tree: shade shares 0..1 (solid shade, tree-only shade). Returns uint8 codes."""
    tot = np.nan_to_num(bldg) + np.nan_to_num(tree)
    lvl = np.clip(np.ceil(tot * 8 - 0.5), 0, 8).astype(np.int16)  # 0 = sun
    code = np.where(np.nan_to_num(bldg) >= np.nan_to_num(tree), lvl, lvl + 8)
    code[lvl == 0] = 0
    code[~np.isfinite(bldg)] = 0
    return code.astype(np.uint8)


def png_palette_alpha(codes, pal, alpha):
    import io
    from PIL import Image
    im = Image.fromarray(codes, mode="P")
    im.putpalette(pal.flatten().tolist())
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True, transparency=bytes(alpha.tolist()))
    return buf.getvalue()


def write_shadow_frames(src_name, bldg_stack, tree_stack, wf, sun_up):
    pal, alpha = shadow_palette()
    frames = []
    sizes = []

    def one(i):
        b = wf.warp(bldg_stack[i], SHADOW_RES)
        t = wf.warp(tree_stack[i], SHADOW_RES)
        data = png_palette_alpha(encode_shadow(b, t), pal, alpha)
        p = f"{OUT}/frames/shadow_{src_name}/{i:02d}.png"
        upload_bytes(data, p, "image/png")
        return i, p, len(data)

    with ThreadPoolExecutor(3) as ex:
        for i, p, n in sorted(ex.map(one, range(STEPS))):
            frames.append(p)
            sizes.append(n)
    log(f"shadow_{src_name}: {sum(sizes) / 1e6:.1f} MB over {STEPS} frames")
    return {"frames": [f"frames/shadow_{src_name}/{i:02d}.png" for i in range(STEPS)],
            "bytes_total": int(sum(sizes)), "frame": wf.meta(), "sun_up": sun_up}


def t02_shadow(win):
    """From T02 shares: bldg = f_bldg + f_both, tree = f_tree, at SHADOW_RES."""
    r0, c0 = grid_slices(win)
    w = Window(c0.start, r0.start, c0.stop - c0.start, r0.stop - r0.start)
    paths = {k: fetch(f"T02/{k}.tif") for k in ("f_bldg", "f_both", "f_tree")}
    H, W = (r0.stop - r0.start) // SHADOW_RES, (c0.stop - c0.start) // SHADOW_RES
    bl = np.full((STEPS, H, W), np.nan, np.float16)
    tr = np.full((STEPS, H, W), np.nan, np.float16)
    srcs = {k: rasterio.open(p) for k, p in paths.items()}
    for i in range(STEPS):
        fb = srcs["f_bldg"].read(i + 1, window=w).astype(np.float32)
        if (fb == 255).all():
            bl[i] = 1.0
            tr[i] = 0.0
            continue
        fo = srcs["f_both"].read(i + 1, window=w).astype(np.float32)
        ft = srcs["f_tree"].read(i + 1, window=w).astype(np.float32)
        bl[i] = block_mean((fb + fo) / 250.0, SHADOW_RES)
        tr[i] = block_mean(ft / 250.0, SHADOW_RES)
    return bl, tr


# ---------------------------------------------------------------- SOLWEIG tiles

def list_tiles():
    tiles = {}
    for b in gcs().client.list_blobs("hackaton-honory-heat", prefix="T06/district/tiles/"):
        parts = b.name.split("/")
        if len(parts) == 5 and parts[4] in ("shadow.tif", "tmrt.tif", "utci.tif"):
            tiles.setdefault(parts[3], set()).add(parts[4])
    return {k: v for k, v in tiles.items() if len(v) == 3}


def solweig_run(win, wf_sh, wf_th, statics):
    plan = json.loads(gcs().blob("T06/district/plan.json").download_as_text())
    tiles = list_tiles()
    log(f"tiles complete: {len(tiles)} / {len(plan)}")
    e0, n0, e1, n1 = win
    Wd, Hd = e1 - e0, n1 - n0
    bl = np.full((STEPS, Hd // SHADOW_RES, Wd // SHADOW_RES), np.nan, np.float16)
    tr = np.full_like(bl, np.nan)
    ut = np.full((STEPS, Hd // THERMAL_RES, Wd // THERMAL_RES), np.nan, np.float32)
    tm = np.full_like(ut, np.nan)

    bmask = statics["building"]
    cls = statics["cls"]  # int8 class index (-1 = excluded)
    svf = statics["svf"]
    alb = statics["albedo"]
    fz = forcing()
    sin_el = np.sin(np.radians(np.clip(fz["sun_elev_deg"].values, 0, 90)))
    dni = fz["dni_wm2"].values
    dhi = fz["dhi_wm2"].values
    up = fz["sun_elev_deg"].values > 0

    nC = len(CLASSES) + 1  # + all ground
    acc = {k: np.zeros((STEPS, nC)) for k in ("utci", "tmrt", "sun", "kin", "kup", "n_utci", "n")}
    zone = {k: np.zeros((STEPS, 2)) for k in ("utci", "tmrt", "n")}  # 0 sun, 1 shade (all ground)
    covered = []

    def load(key):
        te, tn = map(int, key.split("_"))
        out = {}
        for v in ("shadow", "tmrt", "utci"):
            with rasterio.open(fetch(f"T06/district/tiles/{key}/{v}.tif")) as s:
                assert s.transform.a == 1 and s.width == 1000, (key, s.transform)
                out[v] = s.read().astype(np.float32)
            os.remove(os.path.join(WORK, f"T06/district/tiles/{key}/{v}.tif".replace("/", "__")))
        return te, tn, out

    with ThreadPoolExecutor(2) as ex:
        for te, tn, d in ex.map(load, sorted(tiles)):
            # tile placement in window (tiles are 1 km cores aligned to km)
            ce0, cn1 = max(te, e0), min(tn + 1000, n1)
            ce1, cn0 = min(te + 1000, e1), max(tn, n0)
            if ce1 <= ce0 or cn1 <= cn0:
                continue
            tr0, tc0 = (tn + 1000) - cn1, ce0 - te
            hh, ww = cn1 - cn0, ce1 - ce0
            wr0, wc0 = n1 - cn1, ce0 - e0
            sh = d["shadow"][:, tr0:tr0 + hh, tc0:tc0 + ww]
            u = d["utci"][:, tr0:tr0 + hh, tc0:tc0 + ww]
            t = d["tmrt"][:, tr0:tr0 + hh, tc0:tc0 + ww]
            bm = bmask[wr0:wr0 + hh, wc0:wc0 + ww]
            # night: SOLWEIG shadow = 1 everywhere -> no shade information
            b = (sh < 0.015).astype(np.float32)
            tt = ((sh >= 0.015) & (sh < 0.5)).astype(np.float32)
            b[~up] = 1.0
            tt[~up] = 0.0
            k = SHADOW_RES
            bl[:, wr0 // k:(wr0 + hh) // k, wc0 // k:(wc0 + ww) // k] = block_mean(b, k)
            tr[:, wr0 // k:(wr0 + hh) // k, wc0 // k:(wc0 + ww) // k] = block_mean(tt, k)
            k = THERMAL_RES
            uu = u.copy()
            uu[:, bm] = np.nan
            t2 = t.copy()
            t2[:, bm] = np.nan
            ut[:, wr0 // k:(wr0 + hh) // k, wc0 // k:(wc0 + ww) // k] = block_mean(uu, k)
            tm[:, wr0 // k:(wr0 + hh) // k, wc0 // k:(wc0 + ww) // k] = block_mean(t2, k)
            # series
            c = cls[wr0:wr0 + hh, wc0:wc0 + ww].ravel()
            sel = c >= 0
            ci = c[sel].astype(np.int64)
            sv = svf[wr0:wr0 + hh, wc0:wc0 + ww].ravel()[sel]
            al = alb[wr0:wr0 + hh, wc0:wc0 + ww].ravel()[sel]
            for i in range(STEPS):
                s_i = sh[i].ravel()[sel]
                u_i = u[i].ravel()[sel]
                t_i = t[i].ravel()[sel]
                ok = np.isfinite(u_i) & np.isfinite(t_i)
                sun_i = s_i if up[i] else np.zeros_like(s_i)
                kin = sun_i * dni[i] * sin_el[i] + dhi[i] * np.nan_to_num(sv, nan=0.5)
                kup = kin * np.nan_to_num(al, nan=0.15)
                for idx, w8 in ((ci, None), (np.full_like(ci, len(CLASSES)), None)):
                    acc["n"][i] += np.bincount(idx, minlength=nC)
                    acc["sun"][i] += np.bincount(idx, weights=sun_i, minlength=nC)
                    acc["kin"][i] += np.bincount(idx, weights=kin, minlength=nC)
                    acc["kup"][i] += np.bincount(idx, weights=kup, minlength=nC)
                    acc["n_utci"][i] += np.bincount(idx[ok], minlength=nC)
                    acc["utci"][i] += np.bincount(idx[ok], weights=u_i[ok], minlength=nC)
                    acc["tmrt"][i] += np.bincount(idx[ok], weights=t_i[ok], minlength=nC)
                if up[i]:
                    z = (sun_i < 0.5).astype(np.int64)[ok]
                    zone["n"][i] += np.bincount(z, minlength=2)
                    zone["utci"][i] += np.bincount(z, weights=u_i[ok], minlength=2)
                    zone["tmrt"][i] += np.bincount(z, weights=t_i[ok], minlength=2)
            covered.append(f"{te}_{tn}")
            log("tile", te, tn)

    sun_up = [bool(x) for x in up]
    lay = {}
    lay["shadow_solweig"] = write_shadow_frames("solweig", bl, tr, wf_sh, sun_up)
    lay["shadow_solweig"]["tiles_covered"] = len(covered)
    lay["shadow_solweig"]["tiles_planned"] = len(plan)
    del bl, tr
    lay["utci"] = write_value_frames("utci", ut, wf_th, UTCI_STOPS, UTCI_RANGE, "°C")
    lay["tmrt"] = write_value_frames("tmrt", tm, wf_th, TMRT_STOPS, TMRT_RANGE, "°C")
    for v in lay.values():
        v["tiles_covered"] = len(covered)
        v["tiles_planned"] = len(plan)

    def mean(a, n):
        with np.errstate(invalid="ignore", divide="ignore"):
            r = np.where(n > 0, a / np.maximum(n, 1), np.nan)
        return r

    names = CLASSES + ["all_ground"]
    series = {"steps": STEP_LABELS, "ts_utc": STEP_UTC,
              "forcing": {k: [None if pd.isna(x) else round(float(x), 2) for x in fz[c].values] for k, c in
                          [("ta_c", "ta_c"), ("rh_pct", "rh_pct"), ("wind_ms", "wind_10m_ms"), ("ghi_wm2", "ghi_wm2"),
                           ("dni_wm2", "dni_wm2"), ("dhi_wm2", "dhi_wm2"), ("sun_elev_deg", "sun_elev_deg"),
                           ("sun_azim_deg", "sun_azim_deg"), ("cloud_okta", "cloud_okta"),
                           ("cloud_era5_pct", "cloud_era5_pct"), ("ldown_wm2", "ldown_wm2")]},
              "by_surface": {}, "by_zone": {}, "static_by_surface": statics["static_by_surface"],
              "tiles_covered": len(covered), "tiles_planned": len(plan)}
    tot_px = acc["n"][0]
    for j, nm in enumerate(names):
        series["by_surface"][nm] = {
            "pixels": int(tot_px[j]),
            "utci_mean": _r(mean(acc["utci"][:, j], acc["n_utci"][:, j])),
            "tmrt_mean": _r(mean(acc["tmrt"][:, j], acc["n_utci"][:, j])),
            "sun_share": _r(mean(acc["sun"][:, j], acc["n"][:, j]), 3),
            "k_in_est_wm2": _r(mean(acc["kin"][:, j], acc["n"][:, j]), 1),
            "k_up_est_wm2": _r(mean(acc["kup"][:, j], acc["n"][:, j]), 1),
        }
    for j, nm in enumerate(["sun", "shade"]):
        series["by_zone"][nm] = {"utci_mean": _r(mean(zone["utci"][:, j], zone["n"][:, j])),
                                 "tmrt_mean": _r(mean(zone["tmrt"][:, j], zone["n"][:, j])),
                                 "pixels": [int(x) for x in zone["n"][:, j]]}
    upload_json(series, f"{OUT}/series/district_series.json")
    lay["series"] = "series/district_series.json"
    return lay


def _r(a, nd=2):
    return [None if not np.isfinite(x) else round(float(x), nd) for x in a]


def write_value_frames(name, stack, wf, stops, rng, unit):
    lut, _ = ramp_lut(stops, *rng)
    sizes = []

    def one(i):
        a = wf.warp(stack[i], THERMAL_RES)
        codes = encode_codes(a, *rng)
        c = png_palette(codes, lut, alpha=215)
        g = png_gray(codes)
        upload_bytes(c, f"{OUT}/frames/{name}/{i:02d}.png", "image/png")
        upload_bytes(g, f"{OUT}/frames/{name}_value/{i:02d}.png", "image/png")
        return len(c) + len(g)

    with ThreadPoolExecutor(3) as ex:
        sizes = list(ex.map(one, range(STEPS)))
    log(f"{name}: {sum(sizes) / 1e6:.1f} MB")
    with np.errstate(invalid="ignore"):
        stats = {"p05": _r(np.nanpercentile(stack.reshape(STEPS, -1), 5, axis=1)),
                 "p50": _r(np.nanpercentile(stack.reshape(STEPS, -1), 50, axis=1)),
                 "p95": _r(np.nanpercentile(stack.reshape(STEPS, -1), 95, axis=1))}
    return {"frames": [f"frames/{name}/{i:02d}.png" for i in range(STEPS)],
            "value_frames": [f"frames/{name}_value/{i:02d}.png" for i in range(STEPS)],
            "value_decode": {"formula": f"value = {rng[0]} + (gray - 0.5) * {(rng[1] - rng[0]) / 255:.6f}",
                             "vmin": rng[0], "vmax": rng[1], "nodata_gray": 0, "unit": unit},
            "legend": [{"value": v, "color": "#%02x%02x%02x" % c} for v, c in stops], "unit": unit,
            "bytes_total": int(sum(sizes)), "frame": wf.meta(), "district_percentiles": stats}


# ---------------------------------------------------------------- statics

S2 = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/34/U/DC/2026/6/S2C_34UDC_20260627_0_L2A"
# Bonafoni & Sekertekin (2020) Sentinel-2 broadband albedo
ALB_W = {"B02": 0.2266, "B03": 0.1236, "B04": 0.1573, "B08": 0.3417, "B11": 0.1170, "B12": 0.0338}


def albedo_10m(win):
    e0, n0, e1, n1 = win
    W, H = (e1 - e0) // STATIC_RES, (n1 - n0) // STATIC_RES
    t = from_origin(e0, n1, STATIC_RES, STATIC_RES)
    acc = np.zeros((H, W), np.float32)
    for b, wgt in ALB_W.items():
        with rasterio.open(f"{S2}/{b}.tif") as src, WarpedVRT(src, crs="EPSG:2180", transform=t, width=W, height=H,
                                                               resampling=Resampling.bilinear) as vrt:
            a = vrt.read(1).astype(np.float32)
        a[a <= 0] = np.nan
        acc += wgt * a / 10000.0
    return acc


def lst_10m(win):
    """Filled Landsat LST (°C) at 11:25 on 2026-06-25: pmwc + district median 38.84 °C."""
    a = read_grid10(fetch("T05/pmwc/pmwc_10m.tif"), win)
    return a + (311.99 - 273.15)


def read_grid10(path, win):
    e0, n0, e1, n1 = win
    with rasterio.open(path) as s:
        c0 = int((e0 - s.bounds.left) / 10)
        r0 = int((s.bounds.top - n1) / 10)
        return s.read(1, window=Window(c0, r0, (e1 - e0) // 10, (n1 - n0) // 10)).astype(np.float32)


def statics_1m(win, alb10):
    """building mask, class index, svf, albedo on the web window at 1 m (+AOI restriction)."""
    log("loading T01 rasters")
    bm = read_grid_window("T01/output/building_mask.tif", win) == 1
    st = read_grid_window("T01/output/surface_type.tif", win)
    ch = read_grid_window("T01/output/canopy_height.tif", win)
    svf = read_grid_window("T02/svf.tif", win, band=1).astype(np.float32)
    aoi = aoi_mask(win)
    cls = np.full(st.shape, -1, np.int8)
    cls[st == 5] = 0
    cls[st == 3] = 1
    cls[st == 4] = 2
    cls[st == 2] = 3
    cls[st == 6] = 5
    cls[(np.nan_to_num(ch) >= 2) & (st != 1) & (st != 2)] = 4
    cls[bm | (st == 1) | ~aoi] = -1
    alb1 = np.repeat(np.repeat(alb10, 10, 0), 10, 1)
    lst1 = None
    return {"building": bm, "cls": cls, "svf": svf, "albedo": alb1, "aoi": aoi}


def _f(x, nd):
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def static_by_surface(st, alb10, lst10):
    cls = st["cls"]
    out = {}
    lst1 = np.repeat(np.repeat(lst10, 10, 0), 10, 1)
    for j, nm in enumerate(CLASSES):
        m = cls == j
        out[nm] = {"albedo_mean": _f(np.nanmean(st["albedo"][m]), 3) if m.any() else None,
                   "lst_1125_mean_c": _f(np.nanmean(lst1[m]), 2) if m.any() else None,
                   "pixels": int(m.sum())}
    m = (cls >= 0)
    out["all_ground"] = {"albedo_mean": _f(np.nanmean(st["albedo"][m]), 3),
                         "lst_1125_mean_c": _f(np.nanmean(lst1[m]), 2), "pixels": int(m.sum())}
    roofs = st["building"] & st["aoi"]
    out["roofs"] = {"albedo_mean": _f(np.nanmean(st["albedo"][roofs]), 3),
                    "lst_1125_mean_c": _f(np.nanmean(lst1[roofs]), 2), "pixels": int(roofs.sum())}
    return out


def write_static(win, alb10, lst10):
    wf = WebFrame(win, STATIC_RES)
    lay = {}
    for name, arr, stops, rng, unit, desc in [
        ("albedo", alb10, ALBEDO_STOPS, (0.0, 0.45), "fraction",
         "Broadband surface albedo, Sentinel-2 L2A 2026-06-27 (S2C_34UDC), Bonafoni & Sekertekin 2020 weights"),
        ("lst_1125", lst10, LST_STOPS, (15.0, 55.0), "°C",
         "Landsat LST 2026-06-25 11:25 CEST (LC09, gaps from LC08 06-17), = T05 pmwc + 38.84 °C district median")]:
        a = wf.warp(arr, STATIC_RES)
        lut, _ = ramp_lut(stops, *rng)
        codes = encode_codes(a, *rng)
        upload_bytes(png_palette(codes, lut, alpha=225), f"{OUT}/frames/{name}.png", "image/png")
        upload_bytes(png_gray(codes), f"{OUT}/frames/{name}_value.png", "image/png")
        # 10 m COG in EPSG:2180 too
        p = os.path.join(WORK, f"{name}_10m.tif")
        e0, n0, e1, n1 = win
        with rasterio.open(p, "w", driver="COG", width=arr.shape[1], height=arr.shape[0], count=1, dtype="float32",
                           crs="EPSG:2180", transform=from_origin(e0, n1, 10, 10), nodata=np.nan,
                           compress="DEFLATE") as d:
            d.write(arr.astype(np.float32), 1)
        upload(p, f"{OUT}/rasters/{name}_10m.tif", "image/tiff")
        lay[name] = {"frame_png": f"frames/{name}.png", "value_png": f"frames/{name}_value.png",
                     "cog_2180": f"rasters/{name}_10m.tif", "description": desc, "unit": unit,
                     "value_decode": {"formula": f"value = {rng[0]} + (gray - 0.5) * {(rng[1] - rng[0]) / 255:.6f}",
                                      "vmin": rng[0], "vmax": rng[1], "nodata_gray": 0},
                     "legend": [{"value": v, "color": "#%02x%02x%02x" % c} for v, c in stops],
                     "frame": wf.meta(),
                     "district_p05_p50_p95": [round(float(x), 3) for x in np.nanpercentile(arr, [5, 50, 95])]}
    return lay


def main():
    src = os.environ.get("SOURCE", "solweig")
    win = web_window()
    log("window", win, "source", src)
    wf_sh = WebFrame(win, SHADOW_RES)
    parts = {}
    if src == "t02":
        fz = forcing()
        up = [bool(x) for x in fz["sun_elev_deg"].values > 0]
        bl, tr = t02_shadow(win)
        parts["shadow_t02"] = write_shadow_frames("t02", bl, tr, wf_sh, up)
    elif src == "static":
        alb10 = albedo_10m(win)
        lst10 = lst_10m(win)
        parts.update(write_static(win, alb10, lst10))
    else:
        alb10 = albedo_10m(win)
        lst10 = lst_10m(win)
        st = statics_1m(win, alb10)
        st["static_by_surface"] = static_by_surface(st, alb10, lst10)
        parts.update(write_static(win, alb10, lst10))
        wf_th = WebFrame(win, THERMAL_RES)
        parts.update(solweig_run(win, wf_sh, wf_th, st))
        try:
            import points
            points.enrich_with_solweig()
        except Exception as e:  # points may not exist yet
            log("points enrich skipped:", repr(e))
    upload_json(parts, f"{OUT}/parts/frames_{src}.json")
    log("done", list(parts))


if __name__ == "__main__":
    main()
