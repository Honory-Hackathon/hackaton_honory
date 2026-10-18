"""T07: UTCI and Tmrt field on walkable pixels from the T06 district SOLWEIG tiles.

Cloud Run Job entry point. Reads every finished tile core (T06/district/tiles/{e0}_{n0}/{tmrt,utci,shadow}.tif),
keeps only the walkable pixels (T03 walkable.tif > 0) in memory as vectors, writes 33-band COGs on the shared
8000 x 9000 grid (NaN / 255 outside the mask) and computes the checks of the brief:
coverage, tile seams, far casters vs T02 building shade, pilot consistency, sanity, previews.

Env:
  OUT_PREFIX    output prefix in the bucket (default T07; a test run uses T07/test)
  REQUIRE_TILES refuse to run with fewer finished tiles (default 0 = take what exists)
  SKIP_COG      1 = checks only
  TILES         optional comma list to restrict the tiles (tests)
"""

import io
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import rasterio.shutil
from google.cloud import storage
from rasterio.features import rasterize
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

BUCKET = "hackaton-honory-heat"
OUT = os.environ.get("OUT_PREFIX", "T07").strip("/")
GRID = (634000, 483000, 642000, 492000)  # W S E N, shared 1 m grid
W, H = GRID[2] - GRID[0], GRID[3] - GRID[1]
TILE = 1000
NB = 33
PILOT = (637300, 485100, 638500, 485900)
TMP = Path(tempfile.mkdtemp())
T0 = time.time()

_bucket = None


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def bucket():
    global _bucket
    if _bucket is None:
        _bucket = storage.Client().bucket(BUCKET)
    return _bucket


def get_bytes(path):
    return bucket().blob(path).download_as_bytes()


def get_file(path):
    p = TMP / path.replace("/", "_")
    bucket().blob(path).download_to_filename(str(p))
    return p


def put_file(path, local, ctype=None):
    bucket().blob(f"{OUT}/{path}").upload_from_filename(str(local), content_type=ctype)


def put_json(path, obj):
    data = json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    bucket().blob(f"{OUT}/{path}").upload_from_string(data, content_type="application/json")


def rc(e, n):
    """Grid row/col of the upper-left corner of a cell whose lower-left is (e, n) ... for 1 km tile (e0, n0)."""
    return GRID[3] - (n + TILE), e - GRID[0]


def read_tile(name, var):
    with MemoryFile(get_bytes(f"T06/district/tiles/{name}/{var}.tif")) as mf, mf.open() as src:
        assert src.count == NB and src.width == TILE and src.height == TILE, (name, var, src.shape)
        e0, n0 = (int(v) for v in name.split("_"))
        assert abs(src.bounds.left - e0) < 1e-6 and abs(src.bounds.top - (n0 + TILE)) < 1e-6
        return src.read(), src.descriptions


def stats(v):
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    p = np.percentile(v, [1, 50, 99])
    return dict(n=int(v.size), mean=round(float(v.mean()), 3), min=round(float(v.min()), 3), p1=round(float(p[0]), 3),
                p50=round(float(p[1]), 3), p99=round(float(p[2]), 3), max=round(float(v.max()), 3))


def block_mean(a, f):
    """Mean of non-NaN values over f x f blocks of a (H, W) array."""
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    b = a[:h, :w].reshape(h // f, f, w // f, f)
    with np.errstate(invalid="ignore"):
        return np.nanmean(b, axis=(1, 3)).astype(np.float32) if np.isfinite(b).any() else np.full((h // f, w // f), np.nan, np.float32)


def caster_surface():
    """Ground + buildings over the caster extent: T02 dsm_far, with T01 dsm inside the shared grid."""
    with rasterio.open(get_file("T02/far_casters/dsm_far.tif")) as src:
        surf = src.read(1)
        ext = (src.bounds.left, src.bounds.top)
    with rasterio.open(get_file("T01/output/dsm.tif")) as src:
        r0, c0 = int(ext[1] - GRID[3]), int(GRID[0] - ext[0])
        surf[r0:r0 + H, c0:c0 + W] = src.read(1)
    return surf, ext


def caster_distance(px_rows, px_cols, az, el, surf, ext, dmax=6000):
    """Distance (m) along the sun ray to the first cell of `surf` above the ray, from grid pixels; NaN = none."""
    x0 = GRID[0] + px_cols + 0.5
    y0 = GRID[3] - px_rows - 0.5
    sr = (ext[1] - y0).astype(np.int64)
    sc = (x0 - ext[0]).astype(np.int64)
    z0 = surf[sr, sc] + 0.1
    d = np.arange(2.0, dmax, 1.0)
    sa, ca, te = np.sin(np.radians(az)), np.cos(np.radians(az)), np.tan(np.radians(el))
    out = np.full(px_rows.size, np.nan, np.float32)
    hmax = np.full(px_rows.size, np.nan, np.float32)
    for i0 in range(0, px_rows.size, 256):
        sl = slice(i0, i0 + 256)
        xs = x0[sl, None] + d[None] * sa
        ys = y0[sl, None] + d[None] * ca
        rr = np.floor(ext[1] - ys).astype(np.int64)
        cc = np.floor(xs - ext[0]).astype(np.int64)
        ok = (rr >= 0) & (rr < surf.shape[0]) & (cc >= 0) & (cc < surf.shape[1])
        z = np.where(ok, surf[np.clip(rr, 0, surf.shape[0] - 1), np.clip(cc, 0, surf.shape[1] - 1)], -1e9)
        z = np.where(np.isfinite(z), z, -1e9)
        blk = z > (z0[sl, None] + d[None] * te)
        hit = blk.any(1)
        first = blk.argmax(1)
        out[sl] = np.where(hit, d[first], np.nan)
        hmax[sl] = np.where(hit, z[np.arange(first.size), first] - z0[sl], np.nan)
    return out, hmax


def write_stack(name, vec, idx, dtype, nodata, descs, tags):
    """vec (NB, n) values at flat grid indices idx -> 33-band COG on the shared grid."""
    tmp = TMP / f"{name}.tmp.tif"
    out = TMP / f"{name}.tif"
    pred = 3 if dtype == "float32" else 2
    prof = dict(driver="GTiff", dtype=dtype, count=NB, width=W, height=H, crs="EPSG:2180", nodata=nodata,
                transform=from_origin(GRID[0], GRID[3], 1, 1), tiled=True, blockxsize=512, blockysize=512,
                compress="DEFLATE", predictor=pred, BIGTIFF="YES", interleave="band")
    full = np.empty(W * H, dtype=dtype)
    with rasterio.open(tmp, "w", **prof) as dst:
        for b in range(NB):
            full.fill(nodata)
            full[idx] = vec[b]
            dst.write(full.reshape(H, W), b + 1)
            dst.set_band_description(b + 1, descs[b])
        dst.update_tags(**tags)
    del full
    log(f"{name}: tmp written ({tmp.stat().st_size / 1e6:.0f} MB), COG copy")
    rasterio.shutil.copy(tmp, out, driver="COG", compress="DEFLATE", predictor=pred, BIGTIFF="YES",
                         OVERVIEW_RESAMPLING="AVERAGE" if dtype == "float32" else "NEAREST",
                         NUM_THREADS="ALL_CPUS", blocksize=512)
    tmp.unlink()
    log(f"{name}: COG {out.stat().st_size / 1e6:.0f} MB, uploading")
    put_file(f"{name}.tif", out)
    out.unlink()


def main():
    t_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    names = sorted({b.name.split("/")[-2] for b in bucket().list_blobs(prefix="T06/district/tiles/")
                    if b.name.endswith("timing.json")})
    if os.environ.get("TILES"):
        names = [n for n in names if n in os.environ["TILES"].split(",")]
    need = int(os.environ.get("REQUIRE_TILES", "0"))
    if len(names) < need:
        raise SystemExit(f"only {len(names)} tiles, need {need}")
    plan = json.loads(get_bytes("T06/district/plan.json"))
    log(f"{len(names)} tiles; plan has {len(plan)}; out gs://{BUCKET}/{OUT}/")

    # Walkable mask + AOI on the shared grid
    with rasterio.open(get_file("T03/walkable.tif")) as src:
        walk = src.read(1)
        assert (src.width, src.height) == (W, H)
    gj = json.loads(get_bytes("T01/meta/aoi_halo_grid.geojson"))
    aoi_geom = [f["geometry"] for f in gj["features"] if f["properties"].get("name") == "aoi"]
    aoi = rasterize(aoi_geom, out_shape=(H, W), transform=from_origin(GRID[0], GRID[3], 1, 1), fill=0, default_value=1,
                    dtype="uint8").astype(bool)
    in_tiles = np.zeros((H, W), bool)
    for n in names:
        r, c = rc(*(int(v) for v in n.split("_")))
        in_tiles[r:r + TILE, c:c + TILE] = True
    wmask = walk > 0
    cov = dict(
        tiles_done=len(names), tiles_planned=len(plan), missing_tiles=sorted(set(plan) - set(names)),
        walkable_px=int(wmask.sum()), walkable_in_tiles=int((wmask & in_tiles).sum()),
        walkable_outside_tiles=int((wmask & ~in_tiles).sum()),
        walkable_in_aoi=int((wmask & aoi).sum()), walkable_in_aoi_outside_tiles=int((wmask & aoi & ~in_tiles).sum()),
        walkable_outside_aoi=int((wmask & ~aoi).sum()),
        by_category_outside_tiles={int(k): int(v) for k, v in zip(*np.unique(walk[wmask & ~in_tiles], return_counts=True))},
        aoi_px=int(aoi.sum()), aoi_px_outside_tiles=int((aoi & ~in_tiles).sum()))
    log("coverage", {k: v for k, v in cov.items() if k != "missing_tiles"})
    prev = dict(coverage_gap=block_mean(np.where(wmask & ~in_tiles, 1.0, np.where(wmask, 0.0, np.nan)).astype(np.float32), 8),
                aoi=block_mean(aoi.astype(np.float32), 8), walk=block_mean(wmask.astype(np.float32), 8))
    del aoi

    # Walkable pixel order: per tile, row-major inside the tile
    flat_all = []
    tile_slices = {}
    k = 0
    for n in names:
        r, c = rc(*(int(v) for v in n.split("_")))
        rr, cc = np.nonzero(wmask[r:r + TILE, c:c + TILE])
        f = (rr + r).astype(np.int64) * W + (cc + c)
        flat_all.append(f)
        tile_slices[n] = (slice(k, k + f.size), rr, cc)
        k += f.size
    idx = np.concatenate(flat_all)
    npx = idx.size
    log(f"walkable pixels in tiles: {npx}")

    forcing = pd.read_csv(io.BytesIO(get_bytes("T05/forcing/forcing_20260628.csv")))
    ta = forcing["ta_c"].to_numpy(np.float32)
    assert len(ta) == NB

    with rasterio.open(get_file("T02/f_bldg.tif")) as s2:
        t02_desc = list(s2.descriptions)
        t02_tags = s2.tags()
    task = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    ntask = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    # 3 tasks: 0 = checks + utci, 1 = tmrt, 2 = sun_solweig; 1 task: everything
    outputs = [["utci"], ["tmrt"], ["sun"]][task] if ntask == 3 else ["utci", "tmrt", "sun"]
    do_checks = task == 0
    read_vars = ("tmrt", "utci", "shadow") if do_checks else {"utci": ("utci",), "tmrt": ("tmrt",), "sun": ("shadow",)}[outputs[0]]
    log(f"task {task}/{ntask}: outputs {outputs}, checks {do_checks}")
    vec = {v: np.empty((NB, npx), np.float32) for v in ("tmrt", "utci")}
    vec["sun"] = np.empty((NB, npx), np.uint8)
    edges = {}  # (tile, var) -> dict(side -> (NB, 2, 1000))
    pilot = {v: np.full((NB, PILOT[3] - PILOT[1], PILOT[2] - PILOT[0]), np.nan, np.float32) for v in ("tmrt", "shadow")}
    tile_desc = None
    for n in names:
        e0, n0 = (int(v) for v in n.split("_"))
        sl, rr, cc = tile_slices[n]
        for var in read_vars:
            a, desc = read_tile(n, var)
            if tile_desc is None:
                tile_desc = list(desc)
                for b in range(NB):  # same step order as T02
                    assert tile_desc[b][11:16] == t02_desc[b][11:16], (tile_desc[b], t02_desc[b])
            if var == "shadow":
                vec["sun"][:, sl] = np.clip(np.rint(a[:, rr, cc] * 250), 0, 250).astype(np.uint8)
            else:
                vec[var][:, sl] = a[:, rr, cc]
            if var in ("tmrt", "shadow"):
                edges[(n, var)] = dict(W=a[:, :, :2].transpose(0, 2, 1).copy(), E=a[:, :, -2:][:, :, ::-1].transpose(0, 2, 1).copy(),
                                       N=a[:, :2, :].copy(), S=a[:, -2:, :][:, ::-1, :].copy())
                # pilot window
                x0, x1 = max(e0, PILOT[0]), min(e0 + TILE, PILOT[2])
                y0, y1 = max(n0, PILOT[1]), min(n0 + TILE, PILOT[3])
                if x0 < x1 and y0 < y1:
                    pilot[var][:, PILOT[3] - y1:PILOT[3] - y0, x0 - PILOT[0]:x1 - PILOT[0]] = \
                        a[:, n0 + TILE - y1:n0 + TILE - y0, x0 - e0:x1 - e0]
            del a
        log(f"tile {n} read")

    night = [b for b in range(NB) if "el=-" in t02_desc[b]]
    vec["sun"][night] = 255  # SOLWEIG reports 1 (sun) at night; nodata like T02
    # ---------- checks ----------
    if do_checks:
        sunup = [b for b in range(NB) if b not in night]
        labels = [d[11:16] for d in t02_desc]
        checks = dict(generated_utc=t_start, out=f"gs://{BUCKET}/{OUT}/", coverage=cov, labels=labels, night_bands=night)

        # Sanity: UTCI input range, NaN, sun/shade, evening
        san = []
        for b in range(NB):
            tm, ut, su = vec["tmrt"][b], vec["utci"][b], vec["sun"][b]
            d = tm - ta[b]
            lit, bsh, tre = su >= 245, su == 0, (su > 0) & (su < 125)
            row = dict(step=b, label=labels[b], ta=float(ta[b]), wind_utci=float(forcing["wind_10m_utci_ms"][b]),
                       tmrt_minus_ta=stats(d), n_out_of_range=int(((d < -30) | (d > 70)).sum()),
                       utci_nan=int(np.isnan(ut).sum()), tmrt_nan=int(np.isnan(tm).sum()), utci=stats(ut),
                       share_sunlit=round(float(lit.mean()), 4), share_full_shade=round(float(bsh.mean()), 4),
                       share_tree_shade=round(float(tre.mean()), 4))
            if b in sunup:
                for nm, m in (("sunlit", lit), ("bldg_shade", bsh), ("tree_shade", tre)):
                    row[f"tmrt_{nm}"] = round(float(tm[m].mean()), 2) if m.any() else None
                    row[f"utci_{nm}"] = round(float(ut[m].mean()), 2) if m.any() else None
            san.append(row)
        checks["sanity"] = san
        checks["wind_utci_range"] = [float(forcing["wind_10m_utci_ms"].min()), float(forcing["wind_10m_utci_ms"].max())]
        log("sanity done")

        # Seams: jump across shared tile edges vs jump one pixel inside
        seams = []
        have = set(names)
        for n in names:
            e0, n0 = (int(v) for v in n.split("_"))
            for nb, side, oside in ((f"{e0 + TILE}_{n0}", "E", "W"), (f"{e0}_{n0 - TILE}", "S", "N")):
                if nb not in have:
                    continue
                rec = dict(a=n, b=nb, orientation="vertical" if side == "E" else "horizontal",
                           halo_a=1000 if plan.get(n, 0) > 100 else 500, halo_b=1000 if plan.get(nb, 0) > 100 else 500)
                for var in ("tmrt", "shadow"):
                    ea, eb = edges[(n, var)][side], edges[(nb, var)][oside]  # (NB, 2, 1000): [:,0]=edge line, [:,1]=one inside
                    across = np.abs(ea[:, 0] - eb[:, 0])
                    inside = 0.5 * (np.abs(ea[:, 0] - ea[:, 1]) + np.abs(eb[:, 0] - eb[:, 1]))
                    if var == "tmrt":
                        rec["tmrt_mae_across"] = [round(float(x), 3) for x in np.nanmean(across, 1)]
                        rec["tmrt_mae_inside"] = [round(float(x), 3) for x in np.nanmean(inside, 1)]
                        rec["tmrt_mean_jump"] = [round(float(x), 3) for x in np.nanmean(eb[:, 0] - ea[:, 0], 1)]
                    else:
                        sa, sb = ea[:, 0] >= 0.5, eb[:, 0] >= 0.5
                        rec["shadow_flip_across"] = [round(float(x), 4) for x in (sa != sb).mean(1)]
                        rec["shadow_flip_inside"] = [round(float(x), 4) for x in
                                                     (0.5 * ((sa != (ea[:, 1] >= 0.5)).mean(1) + (sb != (eb[:, 1] >= 0.5)).mean(1)))]
                seams.append(rec)
        checks["seams"] = seams
        del edges
        log(f"seams: {len(seams)} edges")

        # Pilot consistency
        pil = []
        try:
            pt = {}
            for var, fn in (("tmrt", "tmrt_solweig"), ("shadow", "shadow_solweig")):
                with MemoryFile(get_bytes(f"T06/cog/{fn}.tif")) as mf, mf.open() as src:
                    assert tuple(round(x) for x in src.bounds) == PILOT
                    pt[var] = src.read()
            r0, c0 = GRID[3] - PILOT[3], PILOT[0] - GRID[0]
            pw = wmask[r0:r0 + PILOT[3] - PILOT[1], c0:c0 + PILOT[2] - PILOT[0]]
            for b in range(NB):
                d = pilot["tmrt"][b] - pt["tmrt"][b]
                ok = np.isfinite(d)
                sa, sb = pilot["shadow"][b] >= 0.5, pt["shadow"][b] >= 0.5
                pil.append(dict(step=b, label=labels[b], n=int(ok.sum()), tmrt_bias=round(float(np.nanmean(d)), 3),
                                tmrt_mae=round(float(np.nanmean(np.abs(d))), 3),
                                tmrt_p99_abs=round(float(np.nanpercentile(np.abs(d), 99)), 3) if ok.any() else None,
                                tmrt_mae_walk=round(float(np.nanmean(np.abs(d[pw]))), 3),
                                share_abs_gt_1K=round(float((np.abs(d[ok]) > 1).mean()), 4) if ok.any() else None,
                                shadow_agree=round(float((sa == sb)[ok].mean()), 4) if ok.any() else None,
                                shadow_district_sun_pilot_shade=round(float((sa & ~sb)[ok].mean()), 4) if ok.any() else None,
                                shadow_district_shade_pilot_sun=round(float((~sa & sb)[ok].mean()), 4) if ok.any() else None))
            b13 = labels.index("13:00")
            b20 = labels.index("20:00")
            prev["pilot_tmrt_diff_1300"] = (pilot["tmrt"][b13] - pt["tmrt"][b13]).astype(np.float32)
            prev["pilot_tmrt_diff_2000"] = (pilot["tmrt"][b20] - pt["tmrt"][b20]).astype(np.float32)
            del pt
        except Exception as ex:  # noqa: BLE001
            pil = dict(error=repr(ex))
        checks["pilot"] = pil
        del pilot
        log("pilot done")

        # Far casters: T02 building shade B = f_bldg + f_both vs SOLWEIG sunlit
        p_bldg = TMP / "T02_f_bldg.tif"
        p_both = get_file("T02/f_both.tif")
        far = []
        tile_of = np.empty(npx, np.int16)
        for i, n in enumerate(names):
            tile_of[tile_slices[n][0]] = i
        far_maps = {}
        # B = f_bldg + f_both per walkable pixel, read in 512-row strips with all bands (files are pixel-interleaved)
        order = np.argsort(idx, kind="stable")
        sidx = idx[order]
        Bv = np.empty((NB, npx), np.uint8)
        with rasterio.open(p_bldg) as sb_, rasterio.open(p_both) as sf_:
            for r0 in range(0, H, 512):
                r1 = min(H, r0 + 512)
                lo, hi = np.searchsorted(sidx, [r0 * W, r1 * W])
                if lo == hi:
                    continue
                win = rasterio.windows.Window(0, r0, W, r1 - r0)
                fb = sb_.read(window=win).reshape(NB, -1)
                fo = sf_.read(window=win).reshape(NB, -1)
                loc = sidx[lo:hi] - r0 * W
                v = fb[:, loc].astype(np.int16) + fo[:, loc].astype(np.int16)
                Bv[:, order[lo:hi]] = np.where(fb[:, loc] == 255, 255, np.clip(v, 0, 250)).astype(np.uint8)
        del sidx, order
        log("T02 B read")
        for b in range(NB):
            if b in night:
                continue
            B = Bv[b]
            su = vec["sun"][b]
            sol_sun = su >= 125            # SOLWEIG: more than half the direct beam (no building, no tree shadow)
            t02_shade = (B >= 125) & (B != 255)
            miss = t02_shade & sol_sun      # T02 has building shade, SOLWEIG has sun
            extra = ~t02_shade & (su == 0)  # SOLWEIG full (building) shade where T02 has no building shade
            tm, ut = vec["tmrt"][b], vec["utci"][b]
            shade_ref = su == 0
            d_tm = float(tm[sol_sun].mean() - tm[shade_ref].mean()) if shade_ref.any() and sol_sun.any() else None
            d_ut = float(ut[sol_sun].mean() - ut[shade_ref].mean()) if shade_ref.any() and sol_sun.any() else None
            per_tile = np.bincount(tile_of[miss], minlength=len(names))
            far.append(dict(step=b, label=labels[b], el=t02_desc[b].split("el=")[-1], n=npx,
                            t02_bldg_shade=round(float(t02_shade.mean()), 4), solweig_sunlit=round(float(sol_sun.mean()), 4),
                            solweig_full_shade=round(float(shade_ref.mean()), 4),
                            t02_shade_solweig_sun=int(miss.sum()), share_t02_shade_solweig_sun=round(float(miss.mean()), 4),
                            share_of_t02_shade_missed=round(float(miss.sum() / max(1, t02_shade.sum())), 4),
                            solweig_shade_t02_no_bldg=int(extra.sum()), share_solweig_shade_t02_no_bldg=round(float(extra.mean()), 4),
                            tmrt_sun_minus_shade=None if d_tm is None else round(d_tm, 2),
                            utci_sun_minus_shade=None if d_ut is None else round(d_ut, 2),
                            utci_excess_mean_over_walkable=None if d_ut is None else round(d_ut * float(miss.mean()), 3),
                            per_tile={names[i]: int(v) for i, v in enumerate(per_tile) if v}))
            if labels[b] in ("19:00", "19:30", "20:00", "20:30", "13:00"):
                m = np.full((H, W), np.nan, np.float32)
                m.ravel()[idx] = miss.astype(np.float32)
                far_maps[labels[b]] = block_mean(m, 8)
                del m
            log(f"far casters {labels[b]}: {miss.mean():.4f}")
        checks["far_casters"] = far

        # Attribution: for a sample of missed pixels at low sun, how far away is T02's caster?
        try:
            surf, ext = caster_surface()
            rng = np.random.default_rng(7)
            halo_of = np.array([1000 if plan.get(n, 0) > 100 else 500 for n in names])
            attr = []
            for b in range(NB):
                if b in night or labels[b] < "17:00":
                    continue
                B = Bv[b]
                miss = np.nonzero((B >= 125) & (B != 255) & (vec["sun"][b] >= 125))[0]
                if miss.size == 0:
                    continue
                smp = rng.choice(miss, size=min(3000, miss.size), replace=False)
                az = float(t02_desc[b].split("az=")[1].split()[0])
                el = float(t02_desc[b].split("el=")[1])
                rr_, cc_ = np.divmod(idx[smp], W)
                dist, hgt = caster_distance(rr_, cc_, az, el, surf, ext)
                halo = halo_of[tile_of[smp]]
                # distance beyond the tile core edge in the sun direction is what the halo has to cover; use the
                # simpler bound: caster distance vs halo and vs SOLWEIG's max_shadow_distance_m (1000 m default)
                attr.append(dict(step=b, label=labels[b], az=az, el=el, n_missed=int(miss.size), n_sample=int(smp.size),
                                 no_caster_found=round(float(np.isnan(dist).mean()), 4),
                                 dist_p10_p50_p90=[round(float(x), 0) for x in np.nanpercentile(dist, [10, 50, 90])] if np.isfinite(dist).any() else None,
                                 share_le_200m=round(float((dist <= 200).mean()), 4),
                                 share_200_500m=round(float(((dist > 200) & (dist <= 500)).mean()), 4),
                                 share_500_1000m=round(float(((dist > 500) & (dist <= 1000)).mean()), 4),
                                 share_gt_1000m=round(float((dist > 1000).mean()), 4),
                                 share_beyond_halo=round(float((dist > halo).mean()), 4),
                                 caster_height_above_px_p50=round(float(np.nanmedian(hgt)), 1) if np.isfinite(hgt).any() else None))
                log(f"attribution {labels[b]}: {attr[-1]['dist_p10_p50_p90']}")
            checks["far_caster_attribution"] = attr
            del surf
        except Exception as ex:  # noqa: BLE001
            checks["far_caster_attribution"] = dict(error=repr(ex))
            log("attribution failed", repr(ex))
        for k_, v_ in far_maps.items():
            prev[f"far_{k_.replace(':', '')}"] = v_
        p_both.unlink()
        del Bv

        # Previews: 8 m block means of walkable UTCI / Tmrt
        for lab in ("13:00", "18:00", "20:00", "21:00"):
            b = labels.index(lab)
            for var in ("utci", "tmrt"):
                m = np.full(W * H, np.nan, np.float32)
                m[idx] = vec[var][b]
                prev[f"{var}_{lab.replace(':', '')}"] = block_mean(m.reshape(H, W), 8)
                del m
        buf = io.BytesIO()
        np.savez_compressed(buf, **prev)
        bucket().blob(f"{OUT}/checks/previews_8m.npz").upload_from_string(buf.getvalue())
        put_json("checks/checks.json", checks)
        log("checks uploaded")

    # ---------- rasters ----------
    if os.environ.get("SKIP_COG") != "1":
        base = dict(source=f"SOLWEIG 0.1.0b96 district run (T06), {len(names)} tiles, gs://{BUCKET}/T06/district/tiles/",
                    mask="T03 walkable.tif > 0", grid="EPSG:2180 1 m E634000-642000 N483000-492000",
                    bands="33 steps 2026-06-28 06:00-22:00 CEST every 30 min (04:00Z-20:00Z), descriptions as T02",
                    produced=t_start)
        if "utci" in outputs:
            write_stack("utci", vec["utci"], idx, "float32", np.nan, t02_desc,
                        dict(base, units="degC", variable="UTCI (pythermalcomfort, T05 ta_c, rh, max(0.5, wind_10m))"))
        if "tmrt" in outputs:
            write_stack("tmrt", vec["tmrt"], idx, "float32", np.nan, t02_desc,
                        dict(base, units="degC", variable="mean radiant temperature, standing person, isotropic sky"))
        if "sun" in outputs:
            write_stack("sun_solweig", vec["sun"], idx, "uint8", 255, t02_desc,
                        dict(base, units="share of direct beam reaching the pixel x 250 (SOLWEIG shadow: 250 sun, 0 shade, ~8 under trees)",
                             scale_factor="0.004"))
    put_json(f"checks/run_{task}.json", dict(outputs=outputs, started=t_start, finished=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     tiles=names, walkable_pixels_in_tiles=npx, seconds=round(time.time() - T0, 1)))
    log("done")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
