"""Compare engine shade with shadows visible in an orthophoto taken on the LiDAR day.

For each window, the photo time is unknown (mosaic of frames), so it is fitted: for candidate times the
engine's shade (solid, or crowns with transmission < 0.5) is compared with dark ground pixels of the photo
(Otsu threshold on 1 m mean luminance), and the time with the best IoU wins. A pass means the fitted time
falls inside the LiDAR flight window and engine shade is dark in the photo (precision; recall is lowered
by dark asphalt, cars and tram tracks, which Otsu cannot tell from shadow); shadow lengths and heights are then checked
independently of the fit, since only the time (one parameter) is fitted.

usage: python -m shade.validate_ortho DEV_IN ORTHO.tif OUT_DIR DATE HH:MM-HH:MM(UTC) name:E,N[,half] ...
"""

import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from . import engine
from .run import read_layers
from .sun import sun_positions

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def otsu(x):
    hist, edges = np.histogram(x, bins=256)
    p = hist / hist.sum()
    w = np.cumsum(p)
    m = np.cumsum(p * edges[:-1])
    between = (m[-1] * w - m) ** 2 / np.maximum(w * (1 - w), 1e-12)
    return edges[np.argmax(between)]


def main():
    dev_in, ortho, out, day, window, *spots = sys.argv[1:]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    geom, profile = read_layers(dev_in)
    T = profile["transform"]
    building = np.isfinite(rasterio.open(Path(dev_in) / "dsm.tif").read(1))
    t0, t1 = window.split("-")
    times = pd.date_range(f"{day} {t0}", f"{day} {t1}", freq="3min", tz="UTC")
    sp = sun_positions(times)
    results = []
    with rasterio.open(ortho) as src:
        for spot in spots:
            name, xy = spot.split(":")
            e, n, *half = (float(v) for v in xy.split(","))
            half = half[0] if half else 150.0
            # engine runs on the whole dev grid (casters outside the window matter), scored inside the window
            c0, r0 = int((e - half - T.c) / T.a), int((T.f - (n + half)) / -T.e)
            size = int(2 * half)
            sl = np.s_[r0:r0 + size, c0:c0 + size]
            ob = (T.c + c0 * T.a, T.f + (r0 + size) * T.e, T.c + (c0 + size) * T.a, T.f + r0 * T.e)
            rgb = src.read([1, 2, 3], window=from_bounds(*ob, src.transform), out_shape=(3, size, size),
                           resampling=Resampling.average).astype(np.float32)
            lum = rgb.mean(axis=0)
            ground = ~building[sl] & ~(geom.crown_top[sl] > geom.crown_base[sl])
            dark = lum < otsu(lum[ground])
            scores = []
            for _, row in sp.iterrows():
                b, tau = engine.shade_step(geom, row.azimuth, row.elevation)
                pred = (b[sl] > 0) | (tau[sl] < 0.5)
                inter = (pred & dark & ground).sum()
                union = ((pred | dark) & ground).sum()
                scores.append(inter / max(union, 1))
            k = int(np.argmax(scores))
            best = sp.iloc[k]
            b, tau = engine.shade_step(geom, best.azimuth, best.elevation)
            pred = (b[sl] > 0) | (tau[sl] < 0.5)
            res = {"spot": name, "E": e, "N": n, "best_utc": str(best.utc), "azimuth": round(best.azimuth, 1),
                   "elevation": round(best.elevation, 2), "iou": round(scores[k], 3),
                   "accuracy": round(float((pred == dark)[ground].mean()), 3),
                   "precision": round(float((pred & dark & ground).sum() / max((pred & ground).sum(), 1)), 3),
                   "recall": round(float((pred & dark & ground).sum() / max((dark & ground).sum(), 1)), 3),
                   "iou_range": [round(min(scores), 3), round(max(scores), 3)]}
            results.append(res)
            print(json.dumps(res), flush=True)

            fig, ax = plt.subplots(1, 3, figsize=(15, 5.4), dpi=110)
            ax[0].imshow(np.clip(rgb.transpose(1, 2, 0) / 255, 0, 1))
            ax[0].set_title(f"{name}: orthophoto 2023-09-05 (1 m)")
            ax[1].imshow(np.where(ground, dark, np.nan), cmap="gray_r", vmin=0, vmax=1.6)
            ax[1].set_title("dark ground pixels (Otsu)")
            show = np.zeros(pred.shape + (3,))
            show[pred & dark & ground] = (0.1, 0.5, 0.1)    # agree shade
            show[pred & ~dark & ground] = (0.9, 0.2, 0.2)   # engine shade, photo light
            show[~pred & dark & ground] = (0.2, 0.3, 0.9)   # photo dark, engine sun
            show[~ground] = (0.85, 0.85, 0.85)
            show[~pred & ~dark & ground] = (1, 1, 1)
            ax[2].imshow(show)
            ax[2].set_title(f"engine {best.utc:%H:%M}Z IoU {scores[k]:.2f} precision {res['precision']:.2f}\n"
                            "green agree, red engine-only, blue photo-only", fontsize=9)
            for a in ax:
                a.axis("off")
            fig.tight_layout()
            fig.savefig(out / f"ortho_{name}.png")
            plt.close(fig)
    (out / "ortho_validation.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
