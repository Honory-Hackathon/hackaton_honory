"""T08: UTCI field -> heat per walk segment and per stop waiting spot, 33 time steps.

Cloud Run entry point. Environment:
  SOURCE   'auto' (default): T07 UTCI if gs://.../T07/_SUCCESS exists, else the T06 mosaic, else the T06 tiles
           't07' | 'mosaic' | 'tiles' | 'pilot': force one source
  OUT      'stub' (default) -> gs://.../T08/stub/ + warsaw_heat.t08_*_stub
           'final'          -> gs://.../T08/       + warsaw_heat.t08_segment_heat, t08_stop_heat, then T08/_SUCCESS
  WEIGHTS  dose weights 'thr:w,...' (default '26:1,32:1,38:2' = spec §7 g)
  SUCCESS  '1' (default) writes T08/_SUCCESS after a final run
  LOCAL    optional local dir with segments.parquet, segment_pixels.parquet, stop_areas.parquet (testing)

Rules (see reports/T08/README.md):
  segment  utci_mean / utci_p90 / g over the segment's footprint pixels (g = pixel mean of g(U), exact for any weights
           via the exceedance columns exc26..exc46). shade_share = pixel mean of (1 - SOLWEIG shadow); 1 at night.
  indoor   constant UTCI 26 °C (g = 0), shade_share 1.
  covered  capped at the local shade UTCI (median of shaded walkable pixels within 30 m) if SOLWEIG left it sunlit.
  missing  segments without valid pixels take the mean of their graph neighbours (iterated), then the step median.
  stop     waiting spot = coolest 10 % of the stop area's walkable pixels (mean of them, >= 3 px);
           OSM shelter=yes: capped at the local shade UTCI (shelter roofs are not in the LiDAR DSM).
"""

import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows
from rasterio import features
from rasterio.transform import from_origin
from shapely import wkb

BUCKET = "hackaton-honory-heat"
PROJECT = "hackaton-honory"
DATASET = "warsaw_heat"
GRID_X0, GRID_Y1, NCOLS, NROWS = 634000, 492000, 8000, 9000
GRID_T = from_origin(GRID_X0, GRID_Y1, 1, 1)
NSTEP = 33
INDOOR_UTCI = 26.0
LOCAL_R = 30  # m, neighbourhood for shelter / covered local-shade UTCI
SHADE_THR = 0.5  # SOLWEIG shadow (1 = sunlit, 0 = shade, 0.03 = under canopy) below this counts as shade
EXC = (26, 32, 38, 46)
TMP = Path(os.environ.get("TMPDIR", "/tmp")) / "t08"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def gcs():
    from google.cloud import storage
    return storage.Client(project=PROJECT).bucket(BUCKET)


def blob_exists(name):
    return gcs().blob(name).exists()


def parse_weights(s):
    out = []
    for part in s.split(","):
        t, w = part.split(":")
        out.append((float(t), float(w)))
    return out


def g_of(u, weights):
    g = np.zeros_like(u)
    for t, w in weights:
        g += w * np.maximum(0.0, u - t)
    return g


# ---------------------------------------------------------------- sources

def list_sources(source):
    """Return (kind, [(utci_path, shadow_path)]) of GCS object names on the shared grid."""
    b = gcs()
    names = [x.name for x in b.client.list_blobs(BUCKET, prefix="T06/district/")]
    tiles = sorted({n.split("/")[3] for n in names if n.startswith("T06/district/tiles/") and n.endswith("timing.json")})
    tile_pairs = [(f"T06/district/tiles/{t}/utci.tif", f"T06/district/tiles/{t}/shadow.tif") for t in tiles]
    mosaic_ok = "T06/district/utci_20260628.tif" in names and "T06/district/shadow_20260628.tif" in names
    t07 = [x.name for x in b.client.list_blobs(BUCKET, prefix="T07/")]
    t07_utci = sorted(n for n in t07 if n.endswith(".tif") and "utci" in n.lower().split("/")[-1]
                      and n.count("/") == 1)
    if source == "auto":
        source = "t07" if ("T07/_SUCCESS" in t07 and t07_utci) else ("mosaic" if mosaic_ok else "tiles")
    shadow = [("T06/district/shadow_20260628.tif",)] if mosaic_ok else [(p[1],) for p in tile_pairs]
    if source == "t07":
        # prefer an int16 / float utci COG named like utci*.tif at the top of T07/
        pick = [n for n in t07_utci if "20260628" in n] or t07_utci
        return "t07", [pick[0]], [s[0] for s in shadow]
    if source == "mosaic":
        return "mosaic", ["T06/district/utci_20260628.tif"], ["T06/district/shadow_20260628.tif"]
    if source == "pilot":
        return "pilot", ["T06/cog/utci_solweig.tif"], ["T06/cog/shadow_solweig.tif"]
    return "tiles", [p[0] for p in tile_pairs], [p[1] for p in tile_pairs]


def download(names):
    TMP.mkdir(parents=True, exist_ok=True)
    b = gcs()
    out = []
    for n in names:
        p = TMP / n.replace("/", "__")
        if not p.exists():
            b.blob(n).download_to_filename(str(p))
        out.append(p)
    return out


class Field:
    """Band-by-band reader that pastes one or more COGs onto the shared grid."""

    def __init__(self, paths):
        self.srcs = []
        for p in paths:
            s = rasterio.open(p)
            c0 = int(round((s.transform.c - GRID_X0) / s.transform.a))
            r0 = int(round((GRID_Y1 - s.transform.f) / -s.transform.e))
            scale = float(s.scales[0]) if s.scales and s.scales[0] not in (None, 0) else 1.0
            self.srcs.append((s, r0, c0, scale))
            assert s.count == NSTEP, (p, s.count)

    def sample(self, needed, block=500):
        """values (NSTEP, len(needed)) at sorted flat grid indices `needed`; all bands read per row block."""
        out = np.full((NSTEP, len(needed)), np.nan, np.float32)
        rows, cols = needed // NCOLS, needed % NCOLS
        for s, r0, c0, scale in self.srcs:
            h, w = s.shape
            for br in range(0, h, block):
                gr0, gr1 = r0 + br, r0 + min(br + block, h)
                lo, hi = np.searchsorted(needed, [gr0 * NCOLS, gr1 * NCOLS])
                if hi <= lo:
                    continue
                rr, cc = rows[lo:hi], cols[lo:hi]
                inside = (cc >= c0) & (cc < c0 + w)
                if not inside.any():
                    continue
                win = rasterio.windows.Window(0, br, w, min(block, h - br))
                d = s.read(window=win, masked=True).astype(np.float32).filled(np.nan)
                if s.dtypes[0].startswith("int"):
                    d *= (scale if scale != 1.0 else 0.01)  # T07 hand-over: int16 in 0.01 °C
                elif scale != 1.0:
                    d *= scale
                k = np.nonzero(inside)[0]
                out[:, lo + k] = d[:, rr[k] - gr0, cc[k] - c0]
        return out


# ---------------------------------------------------------------- inputs

def load_inputs():
    local = os.environ.get("LOCAL")
    if local:
        seg = pd.read_parquet(f"{local}/segments.parquet")
        pix = pd.read_parquet(f"{local}/segment_pixels.parquet")
        stops = pd.read_parquet(f"{local}/stop_areas.parquet")
        walk_path = f"{local}/walkable.tif"
    else:
        p = download(["T03/network/segments.parquet", "T03/network/segment_pixels.parquet",
                      "T03/stops/stop_areas.parquet", "T03/walkable.tif"])
        seg, pix, stops = (pd.read_parquet(x) for x in p[:3])
        walk_path = p[3]
    with rasterio.open(walk_path) as s:
        assert s.transform == GRID_T and s.shape == (NROWS, NCOLS)
        walk = s.read(1) > 0
    return seg, pix, stops, walk


def forcing():
    from google.cloud import bigquery
    q = f"select step, ts_utc, local_label, ta_c, sun_elev_deg from `{PROJECT}.{DATASET}.t05_forcing_20260628` order by step"
    f = bigquery.Client(project=PROJECT).query(q).to_dataframe()
    f["label_local"] = f.local_label.str.slice(0, 5)
    assert len(f) == NSTEP
    return f


def bbox_pixels(bounds, walk, r=LOCAL_R):
    x0, y0, x1, y1 = bounds
    c0, c1 = max(int(np.floor(x0 - r - GRID_X0)), 0), min(int(np.ceil(x1 + r - GRID_X0)), NCOLS)
    r0, r1 = max(int(np.floor(GRID_Y1 - y1 - r)), 0), min(int(np.ceil(GRID_Y1 - y0 + r)), NROWS)
    rr, cc = np.mgrid[r0:r1, c0:c1]
    m = walk[r0:r1, c0:c1]
    return (rr[m] * NCOLS + cc[m]).astype(np.int64)


def stop_pixels(stops, walk):
    """flat pixel indices per stop: area pixels (walkable if any) and the 30 m neighbourhood."""
    area, hood = [], []
    for g in stops.geometry:
        geom = wkb.loads(g) if isinstance(g, (bytes, bytearray)) else g
        x0, y0, x1, y1 = geom.bounds
        c0, r0 = int(np.floor(x0 - GRID_X0)) - 1, int(np.floor(GRID_Y1 - y1)) - 1
        c1, r1 = int(np.ceil(x1 - GRID_X0)) + 1, int(np.ceil(GRID_Y1 - y0)) + 1
        t = from_origin(GRID_X0 + c0, GRID_Y1 - r0, 1, 1)
        m = features.rasterize([(geom, 1)], out_shape=(r1 - r0, c1 - c0), transform=t, all_touched=True).astype(bool)
        if not m.any():
            m[(r1 - r0) // 2, (c1 - c0) // 2] = True
        rr, cc = np.nonzero(m)
        rr, cc = rr + r0, cc + c0
        ok = (rr >= 0) & (rr < NROWS) & (cc >= 0) & (cc < NCOLS)
        rr, cc = rr[ok], cc[ok]
        w = walk[rr, cc]
        if w.any():
            rr, cc = rr[w], cc[w]
        area.append((rr * NCOLS + cc).astype(np.int64))
        hood.append(bbox_pixels(geom.bounds, walk))
    return area, hood


def local_shade(u, sh, idx):
    """median UTCI of shaded pixels among idx; NaN when none."""
    if len(idx) == 0:
        return np.nan
    uu, ss = u[idx], sh[idx]
    m = (ss < SHADE_THR) & np.isfinite(uu)
    return float(np.median(uu[m])) if m.sum() >= 5 else np.nan


# ---------------------------------------------------------------- main

def run():
    t_start = time.time()
    source = os.environ.get("SOURCE", "auto")
    out_mode = os.environ.get("OUT", "stub")
    weights = parse_weights(os.environ.get("WEIGHTS", "26:1,32:1,38:2"))
    kind, utci_names, shadow_names = list_sources(source)
    log("source", kind, len(utci_names), "utci file(s),", len(shadow_names), "shadow file(s)", utci_names[:2])
    f = forcing()
    seg, pix, stops, walk = load_inputs()
    log("inputs", len(seg), "segments", len(pix), "segment pixels", len(stops), "stops")


    # segment pixel structure, sorted by segment
    seg = seg.reset_index(drop=True)
    sid_to_i = pd.Series(np.arange(len(seg)), index=seg.segment_id.values)
    pix = pix[pix.segment_id.isin(sid_to_i.index)]
    si = sid_to_i.loc[pix.segment_id.values].values
    flat = (pix.row.values.astype(np.int64) * NCOLS + pix.col.values.astype(np.int64))
    order = np.argsort(si, kind="stable")
    si, flat = si[order], flat[order]
    nseg = len(seg)
    starts = np.searchsorted(si, np.arange(nseg))
    counts = np.bincount(si, minlength=nseg)

    covered_i = np.nonzero((seg.covered.fillna(False).values) & ~(seg.indoor.fillna(False).values))[0]
    cov_hood = []
    for i in covered_i:
        fp = seg.footprint.iloc[i]
        geom = wkb.loads(fp) if isinstance(fp, (bytes, bytearray)) else fp
        cov_hood.append(bbox_pixels(geom.bounds, walk))
    st_area, st_hood = stop_pixels(stops, walk)
    shelter = stops.shelter.fillna(False).values.astype(bool)
    log("pixel lists ready; covered", len(covered_i), "sheltered stops", int(shelter.sum()))
    needed = np.unique(np.concatenate([flat] + st_area + st_hood + cov_hood))
    pos = lambda a: np.searchsorted(needed, a)
    flat = pos(flat)
    st_area = [pos(a) for a in st_area]
    st_hood = [pos(a) for a in st_hood]
    cov_hood = [pos(a) for a in cov_hood]
    U = Field(download(utci_names)).sample(needed)
    log("utci sampled at", len(needed), "pixels")
    SH = Field(download(shadow_names)).sample(needed)
    log("shadow sampled")

    S = {k: np.full((NSTEP, nseg), np.nan, np.float32) for k in
         ["utci_mean", "utci_p90", "g", "shade_share"] + [f"exc{t}" for t in EXC]}
    nvalid = np.zeros((NSTEP, nseg), np.int32)
    rule = np.full((NSTEP, nseg), "pixels", object)
    nst = len(stops)
    T = {k: np.full((NSTEP, nst), np.nan, np.float32) for k in
         ["utci", "g", "utci_area_mean", "shade_share"] + [f"exc{t}" for t in EXC]}
    st_rule = np.full((NSTEP, nst), "best_spot", object)
    shade_cap_hits = 0

    for b in range(NSTEP):
        u = U[b]
        sh = SH[b]
        night = f.sun_elev_deg.iloc[b] <= 0
        v = u[flat]
        ok = np.isfinite(v)
        shv = np.where(night, 0.0, sh[flat])
        n = np.bincount(si, weights=ok, minlength=nseg)
        vz = np.where(ok, v, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            S["utci_mean"][b] = np.bincount(si, weights=vz, minlength=nseg) / n
            S["g"][b] = np.bincount(si, weights=np.where(ok, g_of(vz, weights), 0), minlength=nseg) / n
            for t in EXC:
                S[f"exc{t}"][b] = np.bincount(si, weights=np.where(ok, np.maximum(0, vz - t), 0), minlength=nseg) / n
            shok = ok & np.isfinite(shv)
            S["shade_share"][b] = (np.bincount(si, weights=np.where(shok, 1 - np.clip(shv, 0, 1), 0), minlength=nseg)
                                   / np.bincount(si, weights=shok, minlength=nseg))
        nvalid[b] = n
        # p90 per segment: sort values within segment (NaN last), linear interpolation
        o = np.lexsort((np.where(ok, v, np.inf), si))
        vs = np.where(ok, v, np.nan)[o]
        nn = n.astype(np.int64)
        has = nn > 0
        pos = 0.9 * (nn - 1)
        lo = np.floor(pos).astype(np.int64)
        hi = np.minimum(lo + 1, np.maximum(nn - 1, 0))
        fr = pos - lo
        p90 = np.full(nseg, np.nan, np.float32)
        p90[has] = (vs[starts[has] + lo[has]] * (1 - fr[has]) + vs[starts[has] + hi[has]] * fr[has])
        S["utci_p90"][b] = p90

        # covered segments: cap at local shade if SOLWEIG left them sunlit
        if not night:
            for k, i in enumerate(covered_i):
                if nvalid[b, i] > 0 and S["shade_share"][b, i] < 0.5:
                    ls = local_shade(u, sh, cov_hood[k])
                    if np.isfinite(ls) and ls < S["utci_mean"][b, i]:
                        d = S["utci_mean"][b, i] - ls
                        S["utci_mean"][b, i] = ls
                        S["utci_p90"][b, i] = S["utci_p90"][b, i] - d
                        S["shade_share"][b, i] = 1.0
                        rule[b, i] = "covered_shade"
                        shade_cap_hits += 1

        # stops
        for j in range(nst):
            idx = st_area[j]
            uu = u[idx]
            okk = np.isfinite(uu)
            if okk.sum() == 0:
                idx = st_hood[j]
                uu = u[idx]
                okk = np.isfinite(uu)
                st_rule[b, j] = "neighbourhood"
            if okk.sum() == 0:
                st_rule[b, j] = "missing"
                continue
            uu_ok = np.sort(uu[okk])
            k = max(3, int(np.ceil(0.1 * len(uu_ok))))
            best = float(uu_ok[:k].mean())
            shs = sh[idx][okk]
            T["utci_area_mean"][b, j] = float(uu_ok.mean())
            T["shade_share"][b, j] = 1.0 if night else float(np.nanmean(1 - np.clip(shs, 0, 1)))
            if shelter[j] and not night:
                ls = local_shade(u, sh, st_hood[j])
                if np.isfinite(ls) and ls < best:
                    best = ls
                    st_rule[b, j] = "shelter"
            T["utci"][b, j] = best
        log(f"step {b:2d} {f.label_local.iloc[b]} seg valid {int((nvalid[b] > 0).sum())}/{nseg} "
            f"utci_mean med {np.nanmedian(S['utci_mean'][b]):.2f} stops med {np.nanmedian(T['utci'][b]):.2f}")

    # night shade share
    for b in range(NSTEP):
        if f.sun_elev_deg.iloc[b] <= 0:
            S["shade_share"][b] = 1.0

    # indoor rule
    indoor = seg.indoor.fillna(False).values.astype(bool)
    for k in S:
        if k == "shade_share":
            S[k][:, indoor] = 1.0
        elif k.startswith("utci"):
            S[k][:, indoor] = INDOOR_UTCI
        else:
            S[k][:, indoor] = np.nan  # recomputed below from INDOOR_UTCI
    rule[:, indoor] = "indoor"

    # fill outdoor segments without valid pixels from graph neighbours
    miss = ~np.isfinite(S["utci_mean"])
    log("segment-steps without pixels before fill:", int(miss.sum()))
    if miss.any():
        node_seg = pd.concat([pd.DataFrame({"node": seg.u, "i": np.arange(nseg)}),
                              pd.DataFrame({"node": seg.v, "i": np.arange(nseg)})])
        pairs = node_seg.merge(node_seg, on="node")
        pairs = pairs[pairs.i_x != pairs.i_y][["i_x", "i_y"]].drop_duplicates().values
        keys = ["utci_mean", "utci_p90", "shade_share"]
        for b in range(NSTEP):
            for it in range(200):
                m = ~np.isfinite(S["utci_mean"][b])
                if not m.any():
                    break
                src_ok = ~m[pairs[:, 1]] & m[pairs[:, 0]] & ~indoor[pairs[:, 1]]
                if not src_ok.any():
                    break
                pp = pairs[src_ok]
                for k in keys:
                    acc = np.bincount(pp[:, 0], weights=S[k][b][pp[:, 1]], minlength=nseg)
                    cnt = np.bincount(pp[:, 0], minlength=nseg)
                    newv = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
                    S[k][b] = np.where(m & (cnt > 0), newv, S[k][b])
                rule[b][m & np.isfinite(S["utci_mean"][b])] = "neighbour_fill"
            m = ~np.isfinite(S["utci_mean"][b])
            if m.any():
                for k in keys:
                    S[k][b][m] = np.nanmedian(S[k][b][~indoor])
                rule[b][m] = "step_median"
    # g and exceedances from utci_mean where they are not pixel-based
    for b in range(NSTEP):
        m = rule[b] != "pixels"
        um = S["utci_mean"][b][m].astype(np.float64)
        S["g"][b][m] = g_of(um, weights)
        for t in EXC:
            S[f"exc{t}"][b][m] = np.maximum(0, um - t)
    log("covered caps applied:", shade_cap_hits)

    # stops: missing -> access-node segments' mean; g, exceedances
    stop_miss = ~np.isfinite(T["utci"])
    if stop_miss.any():
        node_seg = pd.concat([pd.DataFrame({"node": seg.u, "i": np.arange(nseg)}),
                              pd.DataFrame({"node": seg.v, "i": np.arange(nseg)})])
        for j in np.nonzero(stop_miss.any(0))[0]:
            ii = node_seg[node_seg.node == stops.access_node_id.iloc[j]].i.values
            for b in np.nonzero(stop_miss[:, j])[0]:
                T["utci"][b, j] = np.nanmin(S["utci_mean"][b][ii]) if len(ii) else np.nanmedian(S["utci_mean"][b])
                st_rule[b, j] = "access_segments"
    for b in range(NSTEP):
        if f.sun_elev_deg.iloc[b] <= 0:
            T["shade_share"][b] = 1.0
        u = T["utci"][b].astype(np.float64)
        T["g"][b] = g_of(u, weights)
        for t in EXC:
            T[f"exc{t}"][b] = np.maximum(0, u - t)

    # ---------------------------------------------------------- tables
    ts = pd.to_datetime(f.ts_utc, utc=True)
    rows = []
    for b in range(NSTEP):
        d = pd.DataFrame({"segment_id": seg.segment_id.values.astype(np.int64)})
        d["step"] = np.int64(b)
        d["ts_utc"] = ts.iloc[b]
        d["label_local"] = f.label_local.iloc[b]
        for k in ["utci_mean", "utci_p90", "g", "shade_share"] + [f"exc{t}" for t in EXC]:
            d[k] = S[k][b].astype(np.float64).round(3)
        d["n_pixels"] = nvalid[b].astype(np.int64)
        d["rule"] = rule[b]
        rows.append(d)
    seg_out = pd.concat(rows, ignore_index=True)
    rows = []
    for b in range(NSTEP):
        d = pd.DataFrame({"stop_id": stops.stop_id.astype(str).values})
        d["step"] = np.int64(b)
        d["ts_utc"] = ts.iloc[b]
        d["label_local"] = f.label_local.iloc[b]
        for k in ["utci", "g", "utci_area_mean", "shade_share"] + [f"exc{t}" for t in EXC]:
            d[k] = T[k][b].astype(np.float64).round(3)
        d["shelter"] = shelter
        d["rule"] = st_rule[b]
        rows.append(d)
    stop_out = pd.concat(rows, ignore_index=True)

    # checks
    c0 = seg.component.values == 0
    chk = {
        "source": kind, "utci_files": utci_names, "shadow_files": shadow_names[:3] + (["..."] if len(shadow_names) > 3 else []),
        "weights": weights, "segments": nseg, "stops": nst,
        "seg_rows": len(seg_out), "stop_rows": len(stop_out),
        "seg_null_utci": int(seg_out.utci_mean.isna().sum()), "stop_null_utci": int(stop_out.utci.isna().sum()),
        "c0_outdoor_segments": int((c0 & ~indoor).sum()),
        "c0_outdoor_pixel_based_all_steps": int(((rule == "pixels").all(0) & c0 & ~indoor).sum()),
        "rule_counts_segment_steps": pd.Series(rule.ravel()).value_counts().to_dict(),
        "rule_counts_stop_steps": pd.Series(st_rule.ravel()).value_counts().to_dict(),
        "covered_segments": int(len(covered_i)),
        "runtime_s": round(time.time() - t_start, 1),
    }
    log(json.dumps(chk, default=str))
    write_outputs(seg_out, stop_out, chk, out_mode)


def write_outputs(seg_out, stop_out, chk, out_mode):
    if os.environ.get("DRYRUN"):
        d = Path(os.environ["DRYRUN"])
        d.mkdir(parents=True, exist_ok=True)
        seg_out.to_parquet(d / "t08_segment_heat.parquet", index=False)
        stop_out.to_parquet(d / "t08_stop_heat.parquet", index=False)
        (d / "checks.json").write_text(json.dumps(chk, indent=1, default=str))
        return
    from google.cloud import bigquery
    final = out_mode == "final"
    prefix = "T08" if final else "T08/stub"
    suffix = "" if final else "_stub"
    b = gcs()
    bq = bigquery.Client(project=PROJECT)
    for name, df, cluster in [("segment_heat", seg_out, ["segment_id"]), ("stop_heat", stop_out, ["stop_id"])]:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        obj = f"{prefix}/t08_{name}.parquet"
        b.blob(obj).upload_from_string(buf.getvalue(), content_type="application/octet-stream")
        uri = f"gs://{BUCKET}/{obj}"
        table = f"{PROJECT}.{DATASET}.t08_{name}{suffix}"
        cfg = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET,
                                     write_disposition="WRITE_TRUNCATE", clustering_fields=cluster)
        bq.load_table_from_uri(uri, table, job_config=cfg).result()
        log("wrote", uri, "->", table, bq.get_table(table).num_rows, "rows")
    b.blob(f"{prefix}/checks.json").upload_from_string(json.dumps(chk, indent=1, default=str))
    if final and os.environ.get("SUCCESS", "1") == "1" and chk["source"] in ("t07", "mosaic"):
        b.blob("T08/_SUCCESS").upload_from_string(json.dumps({"source": chk["source"], "utci": chk["utci_files"],
                                                               "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
        log("wrote gs://%s/T08/_SUCCESS" % BUCKET)


if __name__ == "__main__":
    run()
    sys.exit(0)
