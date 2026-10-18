"""Production run: shade zone shares for every time step + sky view factor, as COGs.

usage: python -m shade.run IN_DIR OUT_DIR [--far-dsm FAR.tif] [--steps 07:00,13:00] [--receiver-height 0] [--no-svf]
IN_DIR holds dtm.tif, dsm.tif, canopy_height.tif, crown_base.tif, transmission.tif on one grid
(heights in m; NaN or nodata = absent). T01's names crown_base_height.tif and crown_gap_fraction.tif are
accepted for the last two; the LiDAR gap fraction is then used as vertical crown transmission. FAR.tif is the ground + buildings surface over the wider caster
extent, same cell size and cell edges; it only casts shadows into the grid.

Outputs in OUT_DIR:
  f_sun.tif f_bldg.tif f_tree.tif f_both.tif  uint8, one band per time step, value/250 = share,
                                              255 = sun below the horizon; the four sum to 250
  svf.tif                                     float32, band 1 buildings+terrain, band 2 incl. crowns
  timings.json, preview_HHMM.png
"""

import argparse
import json
import os
import resource
import shutil
import time
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
import rasterio.shutil

from . import engine
from .sun import sun_positions

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LAYERS = {  # layer -> accepted file names
    "dtm": ("dtm",),
    "dsm": ("dsm",),
    "canopy_height": ("canopy_height",),
    "crown_base": ("crown_base", "crown_base_height"),
    "transmission": ("transmission", "crown_gap_fraction"),
}
SCALE = 250
NIGHT = 255
COLOURS = {  # preview RGB per share
    "f_sun": (1.00, 0.88, 0.45),
    "f_bldg": (0.35, 0.38, 0.50),
    "f_tree": (0.35, 0.65, 0.35),
    "f_both": (0.12, 0.30, 0.18),
}


def read_far(path, profile):
    with rasterio.open(path) as src:
        assert src.crs == profile["crs"] and src.transform.a == profile["transform"].a, "far DSM: CRS/cell size"
        col = (profile["transform"].c - src.transform.c) / src.transform.a
        row = (profile["transform"].f - src.transform.f) / src.transform.e
        assert col == int(col) and row == int(row) and col >= 0 and row >= 0, "far DSM: cells not aligned"
        assert row + profile["height"] <= src.height and col + profile["width"] <= src.width, "far DSM: too small"
        return src.read(1, masked=True).astype(np.float32).filled(np.nan), (int(row), int(col))


def read_layers(in_dir, far_dsm=None):
    arrays, profile = {}, None
    for name, files in LAYERS.items():
        path = next(p for f in files if (p := Path(in_dir) / f"{f}.tif").exists())
        with rasterio.open(path) as src:
            a = src.read(1, masked=True).astype(np.float32).filled(np.nan)
            if profile is None:
                profile = src.profile
            assert src.transform == profile["transform"] and src.shape == (profile["height"], profile["width"]), name
        arrays[name] = a
    res = profile["transform"].a
    far, offset = read_far(far_dsm, profile) if far_dsm else (None, (0, 0))
    geom = engine.Geometry.from_layers(arrays["dtm"], arrays["dsm"], arrays["canopy_height"],
                                       arrays["crown_base"], arrays["transmission"], res=res,
                                       far_dsm=far, far_offset=offset)
    return geom, profile


def encode(solid_shaded, tau):
    q = np.rint(tau * SCALE).astype(np.uint8)
    b = solid_shaded.astype(bool)
    zero = np.zeros_like(q)
    return {
        "f_sun": np.where(b, zero, q),
        "f_bldg": np.where(b, q, zero),
        "f_tree": np.where(b, zero, SCALE - q),
        "f_both": np.where(b, SCALE - q, zero),
    }


def preview(enc, path, title, max_px=2500):
    step = max(1, int(np.ceil(max(enc["f_sun"].shape) / max_px)))
    rgb = sum(enc[k][::step, ::step, None].astype(np.float32) / SCALE * np.array(c, np.float32)
              for k, c in COLOURS.items())
    h, w = rgb.shape[:2]
    fig = plt.figure(figsize=(w / 150, h / 150 + 0.4), dpi=150)
    plt.imshow(np.clip(rgb, 0, 1), interpolation="nearest")
    plt.title(title, fontsize=8)
    plt.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def to_cog(src_path, dst_path):
    with rasterio.open(src_path) as src:
        rasterio.shutil.copy(src, dst_path, driver="COG", compress="DEFLATE", predictor=2,
                             bigtiff="IF_SAFER", overview_resampling="average", num_threads="ALL_CPUS")
    os.remove(src_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--far-dsm", help="ground + buildings surface over the wider caster extent")
    ap.add_argument("--steps", help="comma-separated local times, e.g. 07:00,13:00 (default: all 33)")
    ap.add_argument("--receiver-height", type=float, default=0.0)
    ap.add_argument("--no-svf", action="store_true")
    ap.add_argument("--previews", default="07:00,13:00,19:00")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    geom, profile = read_layers(args.in_dir, args.far_dsm)
    timings = {"pixels": int(geom.solid.size), "shape": list(geom.solid.shape),
               "far_shape": None if geom.far_solid is None else list(geom.far_solid.shape),
               "threads": int(os.environ.get("NUMBA_NUM_THREADS", os.cpu_count())),
               "read_s": round(time.perf_counter() - t0, 2), "steps": []}

    sp = sun_positions()
    if args.steps:
        sp = sp[sp.index.strftime("%H:%M").isin(args.steps.split(","))]
    previews = set(args.previews.split(","))

    tmp_profile = dict(driver="GTiff", dtype="uint8", count=len(sp), width=geom.solid.shape[1],
                       height=geom.solid.shape[0], crs=profile["crs"], transform=profile["transform"],
                       nodata=NIGHT, tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE",
                       bigtiff="IF_SAFER", num_threads="ALL_CPUS")
    dsts = {k: rasterio.open(out / f"{k}.tmp.tif", "w", **tmp_profile) for k in engine.SHARE_NAMES}
    for band, (local, row) in enumerate(sp.iterrows(), start=1):
        label = f"{local.isoformat()} ({row.utc.strftime('%H:%MZ')}) az={row.azimuth:.1f} el={row.elevation:.2f}"
        t = time.perf_counter()
        if row.elevation > 0:
            b, tau = engine.shade_step(geom, row.azimuth, row.elevation, args.receiver_height)
            t_solid_tree = time.perf_counter() - t
            enc = encode(b, tau)
        else:
            t_solid_tree = 0.0
            enc = {k: np.full(geom.solid.shape, NIGHT, np.uint8) for k in engine.SHARE_NAMES}
        for k, dst in dsts.items():
            dst.write(enc[k], band)
            dst.set_band_description(band, label)
        hhmm = local.strftime("%H:%M")
        if hhmm in previews and row.elevation > 0:
            preview(enc, out / f"preview_{hhmm.replace(':', '')}.png", label)
        step = {"local": hhmm, "azimuth": round(row.azimuth, 2), "elevation": round(row.elevation, 2),
                "compute_s": round(t_solid_tree, 2), "total_s": round(time.perf_counter() - t, 2),
                "peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 2)}
        timings["steps"].append(step)
        print(json.dumps(step), flush=True)
    for k, dst in dsts.items():
        dst.update_tags(scale_factor=1 / SCALE, units="share of the direct beam", night_value=NIGHT)
        dst.close()
        to_cog(out / f"{k}.tmp.tif", out / f"{k}.tif")

    if not args.no_svf:
        t = time.perf_counter()
        svf_solid, svf_total = engine.sky_view_factor(geom, receiver_height=args.receiver_height)
        p = dict(tmp_profile, dtype="float32", count=2, nodata=None)
        with rasterio.open(out / "svf.tmp.tif", "w", **p) as dst:
            dst.write(svf_solid, 1)
            dst.write(svf_total, 2)
            dst.set_band_description(1, "svf buildings+terrain")
            dst.set_band_description(2, "svf buildings+terrain+crowns (transmission)")
        to_cog(out / "svf.tmp.tif", out / "svf.tif")
        timings["svf_s"] = round(time.perf_counter() - t, 2)
    timings["total_s"] = round(time.perf_counter() - t0, 2)
    timings["peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 2)
    (out / "timings.json").write_text(json.dumps(timings, indent=1))
    print(json.dumps({k: v for k, v in timings.items() if k != "steps"}))


if __name__ == "__main__":
    main()
