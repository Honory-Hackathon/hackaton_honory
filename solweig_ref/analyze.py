"""T06 comparison: SOLWEIG Tmrt/UTCI vs the spec §13 simple model, fitted coefficients, figures, COGs."""

import io
import json
import tempfile
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pythermalcomfort.models import utci as ptc_utci

import common as C
import simple_model as S

ZN = S.ZONES
FIG = Path(tempfile.mkdtemp())


def utci_stack(ta, tmrt, ws, rh, mask):
    """pythermalcomfort UTCI per step on masked pixels; NaN outside validity (not clipped)."""
    out = np.full(tmrt.shape, np.nan, np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for i in range(tmrt.shape[0]):
            tr = tmrt[i][mask].astype(np.float64)
            n = tr.size
            u = ptc_utci(tdb=np.full(n, ta[i]), tr=tr, v=np.full(n, ws[i]), rh=np.full(n, rh[i]), round_output=False).utci
            out[i][mask] = np.asarray(u, np.float32)
    return out


def dose(u):
    return np.maximum(0, u - 26) + np.maximum(0, u - 32) + 2 * np.maximum(0, u - 38)


def err_stats(pred, ref):
    d = (pred - ref).astype(np.float64)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return dict(n=0)
    return dict(n=int(d.size), bias=float(d.mean()), mae=float(np.abs(d).mean()), rmse=float(np.sqrt((d**2).mean())),
                p95_abs=float(np.percentile(np.abs(d), 95)))


def design(zone, swm, sin_a, svf=None, extra=None):
    """Columns f_z*swm, f_z*sin_a, f_z (per zone), optionally f_z*(1-svf), then shared `extra` columns."""
    cols = []
    for z in range(4):
        fz = (zone == z).astype(np.float32)
        cols += [fz * swm, fz * sin_a, fz]
        if svf is not None:
            cols.append(fz * (1 - svf))
    if extra is not None:
        cols.append(np.broadcast_to(extra, zone.shape).astype(np.float32))
    return np.stack(cols, -1)


def fit(zone, swm, sin_a, y, svf=None, extra=None):
    X = design(zone, swm, sin_a, svf, extra)
    beta, *_ = np.linalg.lstsq(X.astype(np.float64), y.astype(np.float64), rcond=None)
    k = 4 if svf is not None else 3
    p = {"tau": beta[0:4 * k:k].tolist(), "a": beta[1:4 * k:k].tolist(), "b": beta[2:4 * k:k].tolist()}
    if svf is not None:
        p["c_svf"] = beta[3:4 * k:k].tolist()
    if extra is not None:
        p["c_pmwc"] = float(beta[-1])
    return p, beta


def predict(beta, zone, swm, sin_a, svf=None, extra=None):
    return design(zone, swm, sin_a, svf, extra) @ beta.astype(np.float32)


def main(b, f):
    f = f.copy()
    T, H, W = b["tmrt"].shape
    ground = (b["building_mask"] == 0) & (b["water_mask"] == 0)
    day = f["sun_altitude"].values > 0
    lt = f["local_time"].tolist()
    ta, rh, ws = f["ta"].values, f["rh"].values, f["ws10_utci"].values  # T05: max(0.5, 10 m wind)
    ws_raw = f["ws10"].values
    pm = b["pmwc"]
    zone = b["zone"].copy()
    zone[zone == 255] = 0  # night: no shadows, all "sun" as the spec function would see it
    sol = b["tmrt"]

    # Model variants (pmwc = 0 on the pilot)
    swm_cs = np.array([S.sw_max(ta[i], f.sun_altitude.iloc[i])[0] for i in range(T)], np.float32)
    swm_era = np.array([S.sw_max(ta[i], f.sun_altitude.iloc[i], f.dni.iloc[i], f.dhi.iloc[i])[0] for i in range(T)], np.float32)
    sin_day = np.where(day, np.sin(np.radians(np.clip(f.sun_altitude.values, 2, 90))), 0).astype(np.float32)
    models = {
        "spec": np.stack([S.simple_mrt(ta[i], f.sun_altitude.iloc[i], zone[i]) for i in range(T)]),
        "spec_night_off": np.stack([S.simple_mrt(ta[i], f.sun_altitude.iloc[i], zone[i], night_off=True) for i in range(T)]),
        "spec_readme_tau": np.stack([S.simple_mrt(ta[i], f.sun_altitude.iloc[i], zone[i], tau=S.README_TAU) for i in range(T)]),
        "spec_era5_rad": np.stack([S.simple_mrt(ta[i], f.sun_altitude.iloc[i], zone[i], dni=f.dni.iloc[i], dhi=f.dhi.iloc[i], night_off=True)
                                   for i in range(T)]),
        "spec_pmwc": np.stack([S.simple_mrt(ta[i], f.sun_altitude.iloc[i], zone[i], pmwc=pm) for i in range(T)]),
    }
    # check the vectorised copy against the verbatim spec function
    zz = {0: (1, 0, 0, 0), 1: (0, 1, 0, 0), 2: (0, 0, 1, 0), 3: (0, 0, 0, 1)}
    for i in (0, 18, 32):
        for zc, fr in zz.items():
            ref = S.compute_inputs_for_utci(ta[i], f.sun_altitude.iloc[i], *(fr[0], fr[1], fr[2], fr[3]))[1]
            got = float(S.simple_mrt(ta[i], f.sun_altitude.iloc[i], np.array([zc], np.uint8))[0])
            assert abs(ref - got) < 1e-3, (i, zc, ref, got)

    # Fits on daytime ground pixels, west half; evaluated on the east half (spatial hold-out) and everywhere
    west = np.zeros((H, W), bool)
    west[:, : W // 2] = True
    rng = np.random.default_rng(6)
    fits = {}
    svf = b["svfbuveg"]
    for name, swm in (("tuned_clearsky", swm_cs), ("tuned_era5", swm_era), ("tuned_era5_svf", swm_era), ("tuned_era5_pmwc", swm_era)):
        ti, ri, ci = np.nonzero(day[:, None, None] & (ground & west)[None])
        pick = rng.choice(ti.size, min(400_000, ti.size), replace=False)
        ti, ri, ci = ti[pick], ri[pick], ci[pick]
        y = sol[ti, ri, ci] - ta[ti]
        sv = svf[ri, ci] if name.endswith("svf") else None
        ex = pm[ri, ci] if name.endswith("pmwc") else None
        p, beta = fit(zone[ti, ri, ci], swm[ti], sin_day[ti], y, sv, ex)
        fits[name] = p
        pred = np.empty_like(sol)
        for i in range(T):
            pred[i] = ta[i] + predict(beta, zone[i], swm[i], sin_day[i], svf if sv is not None else None, pm if ex is not None else None)
        models[name] = pred
    # Express the clear-sky fit in the spec's parametrisation (GVF 0.40 kept, dt_z = (a_z sin + b_z)/0.40)
    p = fits["tuned_clearsky"]
    fits["tuned_clearsky_spec_form"] = {
        "tau (sun, bldg, tree, both)": [round(x, 3) for x in p["tau"]],
        "dt_surf at sin_a=1 with GVF 0.40 (sun, bldg, tree, both)": [round((p["a"][z] + p["b"][z]) / 0.4, 2) for z in range(4)],
        "dt_surf at sin_a=0 with GVF 0.40": [round(p["b"][z] / 0.4, 2) for z in range(4)],
    }

    # Error tables
    masks = {"day": day[:, None, None] & ground[None], "night": (~day)[:, None, None] & ground[None],
             "day_east_holdout": day[:, None, None] & (ground & ~west)[None]}
    stats = {"tmrt": {}, "utci": {}, "dose": {}}
    for m, arr in models.items():
        stats["tmrt"][m] = {k: err_stats(arr[mk], sol[mk]) for k, mk in masks.items()}
    stats["tmrt"]["solweig_landcover"] = {k: err_stats(b["tmrt_lc"][mk], sol[mk]) for k, mk in masks.items()}

    rows = []
    for m in ("spec", "spec_era5_rad", "tuned_era5", "tuned_era5_svf"):
        for i in range(T):
            for z in range(4):
                mk = ground & (zone[i] == z)
                if mk.sum() < 50:
                    continue
                d = models[m][i][mk] - sol[i][mk]
                rows.append(dict(model=m, local_time=lt[i], zone=ZN[z], n=int(mk.sum()), sol_mean=float(sol[i][mk].mean()),
                                 model_mean=float(models[m][i][mk].mean()), bias=float(d.mean()), rmse=float(np.sqrt((d**2).mean()))))
    by_zone_time = pd.DataFrame(rows)

    # UTCI and heat dose (router metric), validity counts
    utci = {"solweig": utci_stack(ta, sol, ws, rh, ground)}
    for m in ("spec", "spec_night_off", "spec_era5_rad", "tuned_clearsky", "tuned_era5", "tuned_era5_svf"):
        utci[m] = utci_stack(ta, models[m], ws, rh, ground)
    utci["solweig_landcover"] = utci_stack(ta, b["tmrt_lc"], ws, rh, ground)
    validity = {}
    for m, u in utci.items():
        src = sol if m == "solweig" else (b["tmrt_lc"] if m == "solweig_landcover" else models[m])
        g = ground[None].repeat(T, 0)
        dT = src - ta[:, None, None]
        validity[m] = dict(nan=int(np.isnan(u[g]).sum()), of=int(g.sum()), tmrt_minus_ta_gt70=int((dT[g] > 70).sum()),
                           tmrt_minus_ta_lt_minus30=int((dT[g] < -30).sum()),
                           steps_raw_wind_outside_0p5_17=int(((ws_raw < 0.5) | (ws_raw > 17)).sum()))
    ref_u, ref_d = utci["solweig"], dose(utci["solweig"])
    for m, u in utci.items():
        stats["utci"][m] = {k: err_stats(u[mk], ref_u[mk]) for k, mk in masks.items()}
        stats["dose"][m] = {k: err_stats(dose(u)[mk], ref_d[mk]) for k, mk in masks.items()}
    # Spatial ranking agreement per step (what a router uses): Spearman across ground pixels
    from scipy.stats import spearmanr
    gi = np.flatnonzero(ground.ravel())
    sub = rng.choice(gi, min(60_000, gi.size), replace=False)
    rank = {m: [float(spearmanr(utci[m][i].ravel()[sub], ref_u[i].ravel()[sub], nan_policy="omit").statistic) if day[i] else None
                for i in range(T)] for m in utci if m != "solweig"}

    # Failure diagnostics: residual of tuned_era5 vs SVF bins and hour; under trees; land cover sensitivity
    res = models["tuned_era5"] - sol
    bins = np.array([0, .3, .45, .6, .75, .9, 1.01])
    svf_rows = []
    for z in range(4):
        for lo, hi in zip(bins[:-1], bins[1:]):
            mk = day[:, None, None] & (ground & (svf >= lo) & (svf < hi))[None] & (zone == z)
            if mk.sum() > 200:
                svf_rows.append(dict(zone=ZN[z], svf_lo=float(lo), svf_hi=float(hi), n=int(mk.sum()),
                                     bias_tuned_era5=float(res[mk].mean()), bias_spec=float((models["spec"] - sol)[mk].mean())))
    st = b["surface_type"]
    lc_rows = []
    for k, nm in ((5, "paved/bare"), (3, "grass"), (4, "shrubs"), (6, "other")):
        mk = ground & (st == k)
        if mk.sum() < 100:
            continue
        for i in range(T):
            lc_rows.append(dict(surface=nm, local_time=lt[i], n=int(mk.sum()), sol=float(sol[i][mk].mean()),
                                sol_lc=float(b["tmrt_lc"][i][mk].mean())))
    lc_df = pd.DataFrame(lc_rows)

    # pmwc diagnostics: does the Landsat anomaly line up with what SOLWEIG resolves and the tuned model misses?
    gm = ground & np.isfinite(pm)
    mid = day & (f.sun_altitude.values > 30)
    pmwc_diag = dict(
        pilot_pmwc_percentiles={q: float(np.nanpercentile(pm[gm], q)) for q in (5, 25, 50, 75, 95)},
        corr_pmwc_vs_resid_tuned_era5_midday=float(np.corrcoef(pm[gm], (sol - models["tuned_era5"])[mid].mean(0)[gm])[0, 1]),
        corr_pmwc_vs_solweig_lc_minus_default_midday=float(np.corrcoef(pm[gm], (b["tmrt_lc"] - sol)[mid].mean(0)[gm])[0, 1]),
        corr_pmwc_vs_svf=float(np.corrcoef(pm[gm], svf[gm])[0, 1]),
        corr_pmwc_vs_midday_sun_share=float(np.corrcoef(pm[gm], (zone[mid] == 0).mean(0)[gm])[0, 1]),
        fitted_c_pmwc_vs_spec_0p35=fits["tuned_era5_pmwc"]["c_pmwc"])

    # Shade-share summary
    share = pd.DataFrame([{**{"local_time": lt[i]}, **{ZN[z]: float((zone[i][ground] == z).mean()) for z in range(4)}} for i in range(T)])
    # SOLWEIG shadow vs our zones (consistency of the zone derivation)
    shade_sol = b["shadow"] < 0.5
    agree = [float(((zone[i] > 0) == shade_sol[i])[ground].mean()) if day[i] else None for i in range(T)]

    metrics = dict(T=T, core=C.CORE, ground_px=int(ground.sum()), fits=fits, stats=stats, utci_validity=validity, pmwc=pmwc_diag,
                   spearman_utci_vs_solweig=rank, zone_vs_solweig_shadow_agreement=agree,
                   local_times=lt, forcing=f.reset_index().astype({"time_utc": str}).to_dict(orient="records"))
    C.put_bytes("results/metrics.json", C.dumps(metrics).encode())
    for nm, d in (("by_zone_time", by_zone_time), ("svf_bins", pd.DataFrame(svf_rows)), ("landcover", lc_df), ("zone_shares", share)):
        C.put_bytes(f"results/{nm}.csv", d.to_csv(index=False).encode())

    figures(b, f, models, utci, zone, ground, svf, by_zone_time, pd.DataFrame(svf_rows), lc_df, share, day, lt)
    cogs(b, models, utci, zone, lt)
    print(json.dumps({"fits": fits, "tmrt_day": {m: v["day"] for m, v in stats["tmrt"].items()},
                      "utci_day": {m: v["day"] for m, v in stats["utci"].items()}, "validity": validity}, indent=1, default=str))


def figures(b, f, models, utci, zone, ground, svf, bzt, svf_df, lc_df, share, day, lt):
    sol = b["tmrt"]
    idx = [lt.index(t) for t in ("07:00", "13:00", "19:00")]
    ext = None
    cmap_z = plt.matplotlib.colors.ListedColormap(["#f2d16b", "#59617f", "#59a659", "#1f4d2e"])

    fig, ax = plt.subplots(3, 4, figsize=(20, 11), constrained_layout=True)
    for r, i in enumerate(idx):
        gz = np.where(ground, zone[i], np.nan)
        ax[r, 0].imshow(gz, cmap=cmap_z, vmin=-.5, vmax=3.5, interpolation="nearest")
        ax[r, 0].set_title(f"{lt[i]} shade zones (sun/bldg/tree/both)")
        for c, (nm, a) in enumerate((("SOLWEIG", sol[i]), ("spec §13", models["spec"][i])), 1):
            im = ax[r, c].imshow(np.where(ground, a, np.nan), cmap="inferno", vmin=20, vmax=75, extent=ext)
            ax[r, c].set_title(f"{lt[i]} Tmrt {nm}")
        plt.colorbar(im, ax=ax[r, 1:3], shrink=.8, label="Tmrt °C")
        im2 = ax[r, 3].imshow(np.where(ground, models["spec"][i] - sol[i], np.nan), cmap="RdBu_r", vmin=-25, vmax=25)
        ax[r, 3].set_title(f"{lt[i]} spec − SOLWEIG")
        plt.colorbar(im2, ax=ax[r, 3], shrink=.8, label="K")
    for a in ax.ravel():
        a.set_xticks([]); a.set_yticks([])
    fig.savefig(FIG / "fig1_maps.png", dpi=90)
    plt.close(fig)

    fig, axs = plt.subplots(1, 4, figsize=(20, 4.5), sharey=True, constrained_layout=True)
    x = np.arange(len(lt))
    for z, a in enumerate(axs):
        g = bzt[(bzt.zone == ZN[z])]
        for m, ls in (("spec", "--"), ("spec_era5_rad", ":"), ("tuned_era5", "-.")):
            gm = g[g.model == m].set_index("local_time").reindex(lt)
            a.plot(x, gm.model_mean, ls, label=m)
        gs = g[g.model == "spec"].set_index("local_time").reindex(lt)
        a.plot(x, gs.sol_mean, "k-", lw=2, label="SOLWEIG")
        a.plot(x, f.ta.values, color="grey", lw=1, label="Ta")
        a.set_title(f"zone: {ZN[z]}")
        a.set_xticks(x[::4], lt[::4])
    axs[0].set_ylabel("mean Tmrt over ground pixels, °C")
    axs[0].legend(fontsize=8)
    fig.savefig(FIG / "fig2_timeseries_by_zone.png", dpi=90)
    plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(20, 3.8), constrained_layout=True)
    for a, m in zip(axs, ("spec", "spec_era5_rad", "tuned_era5")):
        piv = bzt[bzt.model == m].pivot(index="zone", columns="local_time", values="bias").reindex(index=list(ZN), columns=lt)
        im = a.imshow(piv.values, cmap="RdBu_r", vmin=-20, vmax=20, aspect="auto")
        a.set_yticks(range(4), ZN)
        a.set_xticks(range(0, len(lt), 4), lt[::4])
        a.set_title(f"Tmrt bias {m} − SOLWEIG (K)")
    plt.colorbar(im, ax=axs, shrink=.9)
    fig.savefig(FIG / "fig3_bias_zone_time.png", dpi=90)
    plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
    mk = day[:, None, None] & ground[None]
    for a, m in zip(axs, ("spec", "spec_era5_rad", "tuned_era5")):
        a.hexbin(sol[mk], models[m][mk], gridsize=120, bins="log", mincnt=1, extent=(15, 80, 15, 80))
        a.plot([15, 80], [15, 80], "r-", lw=.8)
        a.set_xlabel("SOLWEIG Tmrt °C"); a.set_ylabel(f"{m} Tmrt °C"); a.set_title(m)
    fig.savefig(FIG / "fig4_hexbin.png", dpi=90)
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(14, 4.5), constrained_layout=True)
    for z in range(4):
        g = svf_df[svf_df.zone == ZN[z]]
        mid = (g.svf_lo + g.svf_hi) / 2
        axs[0].plot(mid, g.bias_spec, "o-", label=ZN[z])
        axs[1].plot(mid, g.bias_tuned_era5, "o-", label=ZN[z])
    axs[0].set_title("daytime Tmrt bias vs sky view factor: spec"); axs[1].set_title("… tuned_era5")
    for a in axs:
        a.axhline(0, color="k", lw=.6); a.set_xlabel("SVF (buildings+veg)"); a.set_ylabel("bias K"); a.legend()
    fig.savefig(FIG / "fig5_svf_bias.png", dpi=90)
    plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(18, 5), constrained_layout=True)
    i = lt.index("15:00")
    for a, m in zip(axs, ("solweig", "spec", "tuned_era5")):
        im = a.imshow(utci[m][i], cmap="magma", vmin=30, vmax=50)
        a.set_title(f"UTCI {lt[i]} {m}"); a.set_xticks([]); a.set_yticks([])
    plt.colorbar(im, ax=axs, shrink=.8, label="UTCI °C")
    fig.savefig(FIG / "fig6_utci_1500.png", dpi=90)
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(14, 4.5), constrained_layout=True)
    for nm, g in lc_df.groupby("surface"):
        g = g.set_index("local_time").reindex(lt)
        axs[0].plot(np.arange(len(lt)), g.sol_lc - g.sol, label=nm)
    axs[0].axhline(0, color="k", lw=.6)
    axs[0].set_title("SOLWEIG with T01 land cover − default surfaces (mean Tmrt, K)")
    axs[0].set_xticks(range(0, len(lt), 4), lt[::4]); axs[0].legend()
    sh = share.set_index("local_time")
    axs[1].stackplot(np.arange(len(lt)), *[sh[z] for z in ZN], labels=ZN, colors=["#f2d16b", "#59617f", "#59a659", "#1f4d2e"])
    axs[1].set_xticks(range(0, len(lt), 4), lt[::4]); axs[1].set_title("shade zone shares on pilot ground pixels"); axs[1].legend(loc="lower left")
    fig.savefig(FIG / "fig7_landcover_shares.png", dpi=90)
    plt.close(fig)

    for p in FIG.glob("*.png"):
        C.put_file(f"results/figures/{p.name}", str(p))


def cogs(b, models, utci, zone, lt):
    d = Path(tempfile.mkdtemp())
    desc = [f"2026-06-28 {t} CEST" for t in lt]
    items = {"tmrt_solweig": b["tmrt"], "shadow_solweig": b["shadow"], "tmrt_solweig_landcover": b["tmrt_lc"],
             "tmrt_spec": models["spec"], "tmrt_tuned_era5": models["tuned_era5"],
             "utci_solweig": utci["solweig"], "utci_spec": utci["spec"], "utci_tuned_era5": utci["tuned_era5"]}
    for nm, a in items.items():
        C.write_cog(d / f"{nm}.tif", a.astype(np.float32), C.CORE, desc)
        C.put_file(f"cog/{nm}.tif", str(d / f"{nm}.tif"))
    C.write_cog(d / "shade_zone.tif", b["zone"], C.CORE, desc, nodata=255)
    C.put_file("cog/shade_zone.tif", str(d / "shade_zone.tif"))
    C.write_cog(d / "svf.tif", np.stack([b["svf"], b["svf_veg"], b["svfbuveg"]]), C.CORE, ["svf_buildings", "svf_veg", "svf_buildings_veg"])
    C.put_file("cog/svf.tif", str(d / "svf.tif"))
