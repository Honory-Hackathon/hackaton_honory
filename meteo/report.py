"""T05 Cloud Run task: report (forcing plots + pmwc map over Śródmieście) → GCS T05/report/."""
import base64
import io
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from google.cloud import storage

BKT = storage.Client().bucket("hackaton-honory-heat")
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK2, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": SURF,
                     "axes.facecolor": SURF, "legend.frameon": False, "axes.titlesize": 10, "axes.titleweight": "bold"})

df = pd.read_parquet(io.BytesIO(BKT.blob("T05/forcing/forcing_20260628.parquet").download_as_bytes()))
checks = json.loads(BKT.blob("T05/forcing/checks.json").download_as_text())
meta = json.loads(BKT.blob("T05/pmwc/meta.json").download_as_text())
geo = json.loads(BKT.blob("T01/meta/aoi_halo_grid.geojson").download_as_text())
aoi = np.array(next(f for f in geo["features"] if f["properties"]["name"] == "aoi")["geometry"]["coordinates"][0])
x = df.ts_utc.dt.tz_convert("Europe/Warsaw").dt.hour + df.ts_utc.dt.tz_convert("Europe/Warsaw").dt.minute / 60


def png(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    BKT.blob(f"T05/report/{fig.get_label()}.png").upload_from_string(b.getvalue(), content_type="image/png")
    return base64.b64encode(b.getvalue()).decode()


fig, ax = plt.subplots(3, 2, figsize=(11, 9.5), label="forcing")
for a in ax.flat:
    a.set_xticks(range(6, 23, 2))
    a.set_xlim(5.7, 22.3)
for a in ax[-1]:
    a.set_xlabel("local time (CEST), 2026-06-28")
a = ax[0, 0]
a.plot(x, df.ta_c, color=S1, lw=2, label="ta_c: Śródmieście (Obserwatorium II offset)")
a.plot(x, df.ta_okecie_c, color=MUTED, lw=2, label="Okęcie (hourly synop)")
a.plot(x, df.ta_filtry_adj_c, color=S2, lw=2, label="Filtry offset (sensitivity)")
for name, col in [("obs2_minus_okecie_at_terms_k", S1), ("filtry_minus_okecie_at_terms_k", S2)]:
    for t, d in checks[name].items():
        h = pd.Timestamp(t).tz_convert("Europe/Warsaw").hour
        a.plot(h, df.ta_okecie_c[x == h].iloc[0] + d, "o", ms=8, color=col, mec=SURF, mew=2)
a.set_title("Air temperature (°C); dots = IMGW 06/12/18 UTC term observations")
a.legend(loc="lower center", fontsize=8)
a = ax[0, 1]
a.plot(x, df.rh_pct, color=S1, lw=2)
a.set_title("Relative humidity (%) at ta_c")
a = ax[1, 0]
a.plot(x, df.wind_10m_ms, color=S1, lw=2, drawstyle="default")
a.axhline(0.5, color=MUTED, lw=1, ls="--")
a.text(22.2, 0.55, "UTCI lower limit 0.5", color=MUTED, ha="right", va="bottom", fontsize=8)
a.set_ylim(0, 4)
a.set_title("Wind speed at 10 m (m/s), Okęcie")
a = ax[1, 1]
for col, c, lab in [("ghi_wm2", S1, "global (GHI)"), ("dni_wm2", S2, "direct normal (DNI)"),
                    ("dhi_wm2", S3, "diffuse (DHI)"), ("ldown_wm2", S4, "incoming longwave (est.)")]:
    a.plot(x, df[col], color=c, lw=2, label=lab)
a.set_title("Radiation (W/m²)")
a.legend(loc="upper right", fontsize=8)
a = ax[2, 0]
a.plot(x, df.sun_elev_deg, color=S1, lw=2)
a.axhline(0, color="#c3c2b7", lw=1)
a.set_title("Sun elevation (°, refracted)")
a = ax[2, 1]
a.plot(x, df.cloud_okta, color=S1, lw=2, label="Okęcie observed (oktas)")
a.set_ylim(0, 8)
a.set_title("Cloud cover, Okęcie (oktas)")
fig.tight_layout()
img_forcing = png(fig)

with rasterio.open(BKT.blob("T05/pmwc/pmwc_10m.tif").open("rb")) as src:
    pmwc, b = src.read(1), src.bounds
with rasterio.open(BKT.blob("T05/pmwc/pmwc_source_10m.tif").open("rb")) as src:
    source = src.read(1)
ext = (b.left, b.right, b.bottom, b.top)
fig, ax = plt.subplots(1, 2, figsize=(11, 6.6), label="pmwc_map", gridspec_kw={"width_ratios": [1.15, 1]})
im = ax[0].imshow(pmwc, extent=ext, cmap="RdBu_r", vmin=-8, vmax=8, interpolation="nearest")
cb = fig.colorbar(im, ax=ax[0], shrink=0.8)
cb.set_label(f"pmwc (K) = LST − Śródmieście median ({meta['reference_lst_k_aoi_median'] - 273.15:.1f} °C)", color=INK2)
ax[0].set_title("pmwc, Landsat 9 2026-06-25 11:25 CEST")
ax[1].imshow(source, extent=ext, cmap=ListedColormap([SURF, "#cde2fb", "#eb6834"]), vmin=0, vmax=2, interpolation="nearest")
ax[1].set_title("Source: blue = 06-25 clear, orange = 06-17 rescaled fill")
for a in ax:
    a.plot(aoi[:, 0], aoi[:, 1], color=INK, lw=1.2)
    a.set_xlim(aoi[:, 0].min() - 300, aoi[:, 0].max() + 300)
    a.set_ylim(aoi[:, 1].min() - 300, aoi[:, 1].max() + 300)
    a.set_aspect("equal")
    a.grid(False)
    a.tick_params(labelsize=7)
    a.set_xlabel("EPSG:2180 easting (m)")
fig.tight_layout()
img_map = png(fig)

tbl = df[[c for c in df if not c.endswith("_src")]].assign(ts_utc=df.ts_utc.dt.strftime("%H:%M Z")).to_html(index=False, border=0)
fit = meta["fill_fit"]
html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>T05 Forcing and pmwc</title><style>
:root{{--bg:#f9f9f7;--fg:#0b0b0b;--fg2:#52514e;--card:#fcfcfb;--line:#e1e0d9}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}} h1{{font-size:24px}} h2{{margin-top:32px;font-size:18px}}
img{{max-width:100%;height:auto;background:var(--card);border:1px solid var(--line)}}
.tw{{overflow-x:auto}} table{{border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}}
td,th{{padding:3px 6px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}} p,li{{color:var(--fg2)}}
code{{font-size:13px}}</style></head><body><main>
<h1>T05 — weather forcing 2026-06-28 and Landsat surface-heat anomaly (pmwc)</h1>
<p>Outputs: <code>gs://hackaton-honory-heat/T05/forcing/</code>, BigQuery <code>hackaton-honory.warsaw_heat.t05_forcing_20260628</code>,
<code>gs://hackaton-honory-heat/T05/pmwc/</code>. Definitions and caveats are in the README next to each output.</p>
<h2>Forcing, 33 steps 06:00–22:00 CEST</h2>
<ul><li><b>ta_c</b> = Okęcie hourly synop + offset to IMGW climate station <b>Warszawa-Obserwatorium II</b> (Śródmieście),
measured at 06/12/18 UTC ({', '.join(f'{v:+.1f}' for v in checks['obs2_minus_okecie_at_terms_k'].values())} K), linear in between.
No public hourly series exists inside Śródmieście for June 2026; this station has 3 observations/day plus Tmax {checks['daily']['WARSZAWA-OBSERWATORIUM II']['tmax']} °C.
It sits in a park setting, hence slightly cooler than Okęcie. Filtry (Ochota, 1 km west) runs +0.9…+2.1 K warmer: column <code>ta_filtry_adj_c</code>.</li>
<li>RH from Okęcie vapour pressure at ta_c. Wind is the Okęcie 10 m value. Radiation is ERA5 (Open-Meteo), interpolated with a clear-sky index; DNI closes GHI = DHI + DNI·sin(elev).</li>
<li>Peak: ta_c {checks['ta_c_max']:.1f} °C, Okęcie {checks['ta_okecie_max']:.1f} °C, Filtry-adjusted {checks['ta_filtry_adj_max']:.1f} °C.</li></ul>
<img src="data:image/png;base64,{img_forcing}" alt="Forcing time series">
<h2>pmwc over Śródmieście</h2>
<p>Surface (not air) temperature anomaly, K, relative to the Śródmieście median LST ({meta['reference_lst_k_aoi_median']:.2f} K). One static scene,
2026-06-25 09:25 UTC (11:25 CEST), 3 days before the heat day. Cloud, cloud shadow and dilated-cloud pixels are masked ({100 * (1 - meta['aoi_clear_fraction_0625']):.0f}% of the district).
The gaps are filled from the fully clear LC08 2026-06-17 scene, rescaled with a linear fit (r = {fit['pearson_r']}, slope {fit['pmwc_0625 = slope * anom_0617 + intercept'][0]},
residual SD {fit['residual_sd_k']} K). District percentiles: 5% {meta['aoi_pmwc_percentiles_k']['5']} K, 95% {meta['aoi_pmwc_percentiles_k']['95']} K.
Landsat thermal data are 100 m native and resampled to 30 m, so street-scale detail is not real; the satellite sees roofs and tree crowns.</p>
<img src="data:image/png;base64,{img_map}" alt="pmwc map">
<h2>Forcing table</h2><div class="tw">{tbl}</div>
</main></body></html>"""
BKT.blob("T05/report/report.html").upload_from_string(html, content_type="text/html")
print("ok")
