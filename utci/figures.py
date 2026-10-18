"""T07 report figures and tables from the job's checks (small: checks.json + 8 m previews).

Usage: python figures.py <checks.json> <previews_8m.npz> <out_dir>
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

EXT = (634000, 642000, 483000, 492000)


def utci_cmap():
    from matplotlib.colors import BoundaryNorm, ListedColormap
    bounds = [9, 26, 32, 38, 46, 60]
    cols = ["#9ecae1", "#fee391", "#fe9929", "#d7301f", "#67000d"]
    return ListedColormap(cols), BoundaryNorm(bounds, 5)


def crop(a, f=8):
    """Crop 8 m preview to the tile-core bbox E 635-641 km, N 484-491 km."""
    r0, r1 = (492000 - 491000) // f, (492000 - 484000) // f
    c0, c1 = (635000 - 634000) // f, (641000 - 634000) // f
    return a[r0:r1, c0:c1], (635000, 641000, 484000, 491000)


def agg(a, f):
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.nanmean(a[:h, :w].reshape(h // f, f, w // f, f), axis=(1, 3))


def maps(p, out, labels=("1300", "1800", "2000")):
    cmap, norm = utci_cmap()
    fig, ax = plt.subplots(1, len(labels), figsize=(4.2 * len(labels), 5), constrained_layout=True)
    for a, lab in zip(ax, labels):
        arr, ext = crop(p[f"utci_{lab}"])
        im = a.imshow(arr, extent=ext, cmap=cmap, norm=norm, interpolation="nearest")
        a.imshow(np.where(np.isfinite(crop(p["aoi"])[0]) & (crop(p["aoi"])[0] > 0), np.nan, 0.0), extent=ext,
                 cmap="Greys", alpha=0.08, vmin=0, vmax=1)
        a.set_title(f"UTCI {lab[:2]}:{lab[2:]} CEST, walkable px (8 m mean)", fontsize=9)
        a.set_xticks([]), a.set_yticks([])
    cb = fig.colorbar(im, ax=ax, shrink=0.7, label="UTCI °C (categories <26, 26–32, 32–38, 38–46, >46)")
    cb.set_ticks([9, 26, 32, 38, 46, 60])
    fig.savefig(out / "utci_maps.png", dpi=80)
    plt.close(fig)


def far_map(p, out, labels=("1900", "2000", "2030")):
    fig, ax = plt.subplots(1, len(labels), figsize=(4.2 * len(labels), 5), constrained_layout=True)
    for a, lab in zip(ax, labels):
        k = f"far_{lab}"
        if k not in p:
            continue
        arr, ext = crop(agg(p[k], 5), 40)
        a.imshow(np.isfinite(crop(p["walk"])[0]) & (crop(p["walk"])[0] > 0), extent=ext, cmap="Greys", vmin=0, vmax=4)
        im = a.imshow(np.where(arr > 0, arr, np.nan), extent=ext, cmap="magma_r", vmin=0, vmax=0.5, interpolation="nearest")
        a.set_title(f"{lab[:2]}:{lab[2:]}: T02 building shade, SOLWEIG sun", fontsize=9)
        a.set_xticks([]), a.set_yticks([])
    fig.colorbar(im, ax=ax, shrink=0.7, label="share of walkable px in 40 m cell")
    fig.savefig(out / "far_casters_map.png", dpi=80)
    plt.close(fig)


def timeseries(c, out):
    s = c["sanity"]
    lab = [r["label"] for r in s]
    x = np.arange(len(lab))
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8), constrained_layout=True)
    ax[0].plot(x, [r["ta"] for r in s], "k--", label="Ta (T05)")
    for k, col in (("sunlit", "#d7301f"), ("tree_shade", "#41ab5d"), ("bldg_shade", "#4a6fa5")):
        ax[0].plot(x, [r.get(f"utci_{k}") for r in s], color=col, label=f"UTCI {k.replace('_', ' ')}")
    ax[0].plot(x, [r["utci"]["mean"] for r in s], color="grey", lw=2, alpha=0.6, label="UTCI all walkable")
    ax[0].set_xticks(x[::4], lab[::4])
    ax[0].set_ylabel("°C")
    ax[0].legend(fontsize=7)
    ax[0].set_title("Walkable pixels: UTCI by SOLWEIG shade class", fontsize=9)
    far = c["far_casters"]
    fx = [lab.index(r["label"]) for r in far]
    ax[1].plot(fx, [100 * r["share_t02_shade_solweig_sun"] for r in far], color="#7a0177", label="T02 bldg shade, SOLWEIG sun")
    ax[1].plot(fx, [100 * r["share_solweig_shade_t02_no_bldg"] for r in far], color="#4a6fa5", label="SOLWEIG full shade, T02 none")
    ax[1].set_xticks(x[::4], lab[::4])
    ax[1].set_ylabel("% of walkable pixels")
    ax[1].legend(fontsize=7)
    ax[1].set_title("Shade disagreement SOLWEIG vs T02 (building shade)", fontsize=9)
    fig.savefig(out / "timeseries.png", dpi=80)
    plt.close(fig)


def seams_fig(c, out):
    s = c["seams"]
    lab = c["labels"]
    x = np.arange(len(lab))
    fig, ax = plt.subplots(figsize=(6, 3.4), constrained_layout=True)
    a = np.array([r["tmrt_mae_across"] for r in s])
    b = np.array([r["tmrt_mae_inside"] for r in s])
    ax.plot(x, a.mean(0), color="#d7301f", label="across tile edge")
    ax.plot(x, b.mean(0), color="grey", label="one pixel inside")
    ax.plot(x, a.max(0), color="#d7301f", ls=":", label="across, worst edge")
    ax.set_xticks(x[::4], lab[::4])
    ax.set_ylabel("mean |ΔTmrt| between neighbours, K")
    ax.legend(fontsize=7)
    ax.set_title(f"Tile seams ({len(s)} shared edges)", fontsize=9)
    fig.savefig(out / "seams.png", dpi=80)
    plt.close(fig)


def coverage_fig(p, out):
    fig, ax = plt.subplots(figsize=(5, 5.5), constrained_layout=True)
    g = p["coverage_gap"]
    ax.imshow(np.where(np.isfinite(g), 0.25, np.nan), extent=EXT, cmap="Greys", vmin=0, vmax=1)
    ax.imshow(np.where(g > 0, 1.0, np.nan), extent=EXT, cmap="Reds", vmin=0, vmax=1, interpolation="nearest")
    ax.contour(np.flipud(p["aoi"]), levels=[0.5], extent=EXT, colors="k", linewidths=0.8, origin="lower")
    for e in range(635000, 641001, 1000):
        ax.axvline(e, color="b", lw=0.2)
    for n in range(484000, 491001, 1000):
        ax.axhline(n, color="b", lw=0.2)
    ax.set_title("Walkable px outside SOLWEIG tiles (red)", fontsize=9)
    ax.set_xticks([]), ax.set_yticks([])
    fig.savefig(out / "coverage.png", dpi=80)
    plt.close(fig)


def main(cj, npz, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    c = json.load(open(cj))
    p = dict(np.load(npz))
    maps(p, out)
    far_map(p, out)
    timeseries(c, out)
    if c["seams"]:
        seams_fig(c, out)
    coverage_fig(p, out)


if __name__ == "__main__":
    main(*sys.argv[1:])
