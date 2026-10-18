"""Building-only shade per walk segment and stop (T11 baseline input). Runs as Cloud Run Job t11-bldg.

Reads T02 shares (33-band COGs, uint8 /250, 255 = night) and T03 network/stops; writes
  gs://hackaton-honory-heat/T11/bldg_only/segment_shade.parquet
      segment_id, step, b_mean, b_q10, tree_mean, sun_mean   (shares 0-1, NaN = night)
  gs://hackaton-honory-heat/T11/bldg_only/stop_shade.parquet
      stop_id, step, b_max, b_mean, tree_mean, n_px

B = f_bldg + f_both is the solid (building + terrain) shade, i.e. what a tool without trees sees.
tree_mean = f_tree (shade from trees only), sun_mean = f_sun.
"""
import io
import os
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import features, windows
from google.cloud import storage

BUCKET = "hackaton-honory-heat"
OUT = os.environ.get("OUT_PREFIX", "T11/bldg_only")
TMP = os.environ.get("TMP", "/tmp")
SCALE = 250.0


def dl(cli, path):
    local = os.path.join(TMP, path.replace("/", "_"))
    if not os.path.exists(local):
        t = time.time()
        cli.bucket(BUCKET).blob(path).download_to_filename(local)
        print(f"downloaded {path} in {time.time() - t:.0f}s", flush=True)
    return local


def up(cli, df, name):
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    cli.bucket(BUCKET).blob(f"{OUT}/{name}").upload_from_string(buf.getvalue())
    print(f"uploaded {OUT}/{name} rows={len(df)}", flush=True)


def main():
    cli = storage.Client()
    rasters = {k: dl(cli, f"T02/{k}.tif") for k in ("f_bldg", "f_both", "f_tree", "f_sun")}
    pix = pd.read_parquet(dl(cli, "T03/network/segment_pixels.parquet"))
    stops = gpd.read_parquet(dl(cli, "T03/stops/stop_areas.parquet"))
    print("pixels", len(pix), "stops", len(stops), flush=True)

    with rasterio.open(rasters["f_bldg"]) as r:
        transform, shape, nb = r.transform, r.shape, r.count
    rows = pix["row"].to_numpy()
    cols = pix["col"].to_numpy()
    seg = pix["segment_id"].to_numpy()
    seg_codes, seg_uniq = pd.factorize(seg, sort=True)

    # stop area pixels (rasterised on the shared grid, centre-in-polygon)
    stop_px = []
    for sid, geom in zip(stops["stop_id"], stops.geometry):
        minx, miny, maxx, maxy = geom.bounds
        c0 = int(np.floor(minx - transform.c)) - 1
        r0 = int(np.floor(transform.f - maxy)) - 1
        win = windows.Window(c0, r0, int(np.ceil(maxx - minx)) + 3, int(np.ceil(maxy - miny)) + 3)
        wt = windows.transform(win, transform)
        m = features.geometry_mask([geom], out_shape=(int(win.height), int(win.width)), transform=wt, invert=True)
        rr, cc = np.nonzero(m)
        if len(rr) == 0:  # tiny polygon: take the centroid cell
            c = geom.centroid
            r0, c0 = rasterio.transform.rowcol(transform, c.x, c.y)
            rr, cc = np.array([r0 - int(win.row_off)]), np.array([c0 - int(win.col_off)])
        stop_px.append(pd.DataFrame({"stop_id": sid, "row": rr + int(win.row_off), "col": cc + int(win.col_off)}))
    stop_px = pd.concat(stop_px, ignore_index=True)
    stop_px = stop_px[(stop_px.row >= 0) & (stop_px.row < shape[0]) & (stop_px.col >= 0) & (stop_px.col < shape[1])]
    s_codes, s_uniq = pd.factorize(stop_px["stop_id"], sort=True)
    srows, scols = stop_px["row"].to_numpy(), stop_px["col"].to_numpy()
    print("stop pixels", len(stop_px), flush=True)

    handles = {k: rasterio.open(p) for k, p in rasters.items()}
    # The COGs are pixel-interleaved: read all 33 bands per 512-row strip once, gather the needed pixels.
    vals = {k: np.empty((nb, len(rows)), np.uint8) for k in handles}
    svals = {k: np.empty((nb, len(srows)), np.uint8) for k in handles}
    H = shape[0]
    for r0 in range(0, H, 512):
        t = time.time()
        sel = np.nonzero((rows >= r0) & (rows < r0 + 512))[0]
        ssel = np.nonzero((srows >= r0) & (srows < r0 + 512))[0]
        if len(sel) == 0 and len(ssel) == 0:
            continue
        cc = np.concatenate([cols[sel], scols[ssel]])
        c0, c1 = int(cc.min()), int(cc.max())
        win = windows.Window(c0, r0, c1 - c0 + 1, min(512, H - r0))
        for k, h in handles.items():
            a = h.read(window=win)
            vals[k][:, sel] = a[:, rows[sel] - r0, cols[sel] - c0]
            svals[k][:, ssel] = a[:, srows[ssel] - r0, scols[ssel] - c0]
        print(f"strip {r0}: {len(sel)} px, {time.time() - t:.1f}s", flush=True)

    seg_out, stop_out = [], []
    nseg = len(seg_uniq)
    cnt = np.bincount(seg_codes, minlength=nseg)
    for b in range(nb):
        t = time.time()
        def shares(v):
            night = v["f_bldg"][b] == 255
            B = np.minimum((v["f_bldg"][b].astype(np.float32) + v["f_both"][b]) / SCALE, 1.0)
            T = v["f_tree"][b].astype(np.float32) / SCALE
            S = v["f_sun"][b].astype(np.float32) / SCALE
            for x in (B, T, S):
                x[night] = np.nan
            return B, T, S
        bp, tp, sp = shares(vals)
        with np.errstate(invalid="ignore", divide="ignore"):
            b_mean = np.bincount(seg_codes, weights=bp, minlength=nseg) / cnt
            t_mean = np.bincount(seg_codes, weights=tp, minlength=nseg) / cnt
            s_mean = np.bincount(seg_codes, weights=sp, minlength=nseg) / cnt
        b_q10 = np.full(nseg, np.nan, dtype=np.float32)
        if not np.all(np.isnan(bp)):
            q = pd.Series(bp).groupby(seg_codes).quantile(0.1)
            b_q10[q.index.to_numpy()] = q.to_numpy()
        seg_out.append(pd.DataFrame({"segment_id": seg_uniq, "step": b, "b_mean": b_mean.astype(np.float32),
                                     "b_q10": b_q10, "tree_mean": t_mean.astype(np.float32),
                                     "sun_mean": s_mean.astype(np.float32)}))
        sb, st, _ = shares(svals)
        g = pd.DataFrame({"c": s_codes, "b": sb, "t": st}).groupby("c")
        agg = g.agg(b_max=("b", "max"), b_mean=("b", "mean"), tree_mean=("t", "mean"), n_px=("b", "size"))
        agg["stop_id"] = s_uniq[agg.index.to_numpy()]
        agg["step"] = b
        stop_out.append(agg.reset_index(drop=True))
        print(f"step {b}: {time.time() - t:.1f}s  mean B {np.nanmean(bp) if not np.all(np.isnan(bp)) else float('nan'):.3f}", flush=True)

    up(cli, pd.concat(seg_out, ignore_index=True), "segment_shade.parquet")
    up(cli, pd.concat(stop_out, ignore_index=True)[["stop_id", "step", "b_max", "b_mean", "tree_mean", "n_px"]],
       "stop_shade.parquet")
    cli.bucket(BUCKET).blob(f"{OUT}/_DONE").upload_from_string(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    print("done", flush=True)


if __name__ == "__main__":
    sys.exit(main())
