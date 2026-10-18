"""Small PNG previews for reports/T12 from the uploaded web layers (runs locally, downloads a few MB)."""
import io
import json
import os
import sys

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from common import gcs  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/T12"


def img(path):
    return Image.open(io.BytesIO(gcs().blob(f"T12/{path}").download_as_bytes())).convert("RGBA")


def js(path):
    return json.loads(gcs().blob(f"T12/{path}").download_as_text())


def main():
    m = js("manifest.json")
    v2 = m["views"]["shadows_3d"]["layers"]
    v3 = m["views"]["temperature_albedo"]["layers"]
    sh = m["views"]["shadows_3d"]["recommended_shadow_layer"]
    # fig1: shadow frames at 07, 13, 19
    if sh in v2:
        fig, ax = plt.subplots(1, 3, figsize=(12, 4.4))
        for a, s in zip(ax, (2, 14, 26)):
            im = img(v2[sh]["frames"][s])
            bg = Image.new("RGBA", im.size, (250, 240, 200, 255))
            a.imshow(Image.alpha_composite(bg, im))
            a.set_title(f"{sh} {m['time']['labels_local'][s]}")
            a.axis("off")
        fig.tight_layout()
        fig.savefig(f"{OUT}/fig1_shadow.png", dpi=70)
    # fig2: utci frames
    if "utci" in v3:
        fig, ax = plt.subplots(1, 3, figsize=(12, 4.4))
        for a, s in zip(ax, (2, 14, 24)):
            a.imshow(img(v3["utci"]["frames"][s]))
            a.set_title(f"UTCI {m['time']['labels_local'][s]}")
            a.axis("off")
        fig.tight_layout()
        fig.savefig(f"{OUT}/fig2_utci.png", dpi=70)
    # fig3: albedo + LST
    if "albedo" in v3:
        fig, ax = plt.subplots(1, 2, figsize=(9, 4.6))
        for a, k in zip(ax, ("albedo", "lst_1125")):
            a.imshow(img(v3[k]["frame_png"]))
            a.set_title(k)
            a.axis("off")
        fig.tight_layout()
        fig.savefig(f"{OUT}/fig3_albedo_lst.png", dpi=70)
    # fig4: series
    if "series" in v3:
        s = js(v3["series"]["path"])
        x = np.arange(len(s["steps"]))
        fig, ax = plt.subplots(1, 2, figsize=(12, 4))
        for nm, d in s["by_surface"].items():
            ax[0].plot(x, [np.nan if v is None else v for v in d["utci_mean"]], label=nm)
        ax[0].plot(x, s["forcing"]["ta_c"], "k--", label="air temp")
        for nm, d in s["by_zone"].items():
            ax[1].plot(x, [np.nan if v is None else v for v in d["utci_mean"]], label=f"UTCI {nm}")
        ax[1].plot(x, s["forcing"]["ta_c"], "k--", label="air temp")
        for a in ax:
            a.set_xticks(x[::4], s["steps"][::4])
            a.set_ylabel("°C")
            a.legend(fontsize=7)
            a.grid(alpha=0.3)
        ax[0].set_title("Mean UTCI by surface type (AOI ground)")
        ax[1].set_title("UTCI in sun vs shade")
        fig.tight_layout()
        fig.savefig(f"{OUT}/fig4_series.png", dpi=70)
    # fig5: cool points map
    fc = js("points/cool_points.geojson")
    fig, ax = plt.subplots(figsize=(7, 7))
    cats = sorted({f["properties"]["category"] for f in fc["features"]})
    for c in cats:
        xy = np.array([f["geometry"]["coordinates"] for f in fc["features"] if f["properties"]["category"] == c])
        ax.scatter(xy[:, 0], xy[:, 1], s=3 if c == "bench" else 8, label=f"{c} ({len(xy)})")
    ax.legend(fontsize=7, markerscale=2)
    ax.set_aspect(1 / np.cos(np.radians(52.23)))
    ax.set_title("Cool and water points (OSM), AOI + 300 m")
    fig.tight_layout()
    fig.savefig(f"{OUT}/fig5_cool_points.png", dpi=70)


if __name__ == "__main__":
    main()
