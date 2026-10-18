"""Building-only-shade heat table (T08 segment/stop columns) and the T02-zone stand-in field.

Input: gs://hackaton-honory-heat/T11/bldg_only/{segment,stop}_shade.parquet from the t11-bldg job
(B = f_bldg + f_both, tree = f_tree, sun = f_sun, per segment/stop and step).

Each segment's UTCI is a mixture of per-step zone UTCIs:
  true-ish  U = sun*U_sun + tree*U_tree + B*U_bldg      (stand-in field, only until T08 lands)
  bldg-only U = (sun + tree)*U_sun + B*U_bldg            (what a tool without trees believes)

Zone UTCIs per step come from one of two calibrations:
  pilot : SOLWEIG pilot (T06) mean Tmrt per T02 zone and step, with T05 forcing -> UTCI (pythermalcomfort)
  t08   : (or t08_stub) per-step least-squares fit of the final T08 utci_mean on (sun, tree, B) shares across segments,
          so the building-only baseline lives on the same field it is priced on.

Outputs (parquet, local + gs://hackaton-honory-heat/T11/):
  segment_heat_bldg_only.parquet, stop_heat_bldg_only.parquet       (routing cost of the baseline)
  segment_heat_standin.parquet,  stop_heat_standin.parquet          (pilot calibration only)
  zone_utci_<calib>.csv
Usage: python -m demo.bldg_only --calib pilot|t08|t08_stub   (t08 = final table, t08_stub = stub table)
The unsuffixed segment/stop_heat_bldg_only.parquet is always the last calibration run; suffixed copies are kept.
"""
import argparse
import io

import numpy as np
import pandas as pd

from demo import common

T0 = pd.Timestamp("2026-06-28T04:00:00Z")
INDOOR_UTCI = 26.0  # indoor/underground segments: comfortable, no outdoor dose (same intent as AC ride)


def _gcs_parquet(path):
    from google.cloud import storage
    b = storage.Client(project="hackaton-honory").bucket(common.BUCKET).blob(path)
    return pd.read_parquet(io.BytesIO(b.download_as_bytes()))


def forcing():
    from google.cloud import bigquery
    q = ("select step, ta_c, rh_pct, wind_10m_utci_ms from `hackaton-honory.warsaw_heat.t05_forcing_20260628` "
         "where step between 0 and 32 order by step")
    return bigquery.Client(project="hackaton-honory").query(q).to_dataframe()


def utci(ta, tr, v, rh):
    from pythermalcomfort.models import utci as _utci
    r = _utci(tdb=np.asarray(ta, float), tr=np.asarray(tr, float), v=np.maximum(0.5, np.asarray(v, float)),
              rh=np.asarray(rh, float), limit_inputs=False)
    return np.asarray(getattr(r, "utci", r), dtype=float)


def zone_utci_pilot():
    from google.cloud import storage
    raw = storage.Client(project="hackaton-honory").bucket(common.BUCKET).blob(
        "T06/results/by_zone_time.csv").download_as_bytes()
    z = pd.read_csv(io.BytesIO(raw))
    z = z[z.model == z.model.iloc[0]]  # sol_mean (SOLWEIG Tmrt) is the same in every model's rows
    z["step"] = ((pd.to_datetime(z.local_time, format="%H:%M").dt.hour * 60
                  + pd.to_datetime(z.local_time, format="%H:%M").dt.minute) - 360) // 30
    tm = z.pivot(index="step", columns="zone", values="sol_mean")
    for c in ("bldg", "tree", "both"):  # night: only the sun zone exists -> same everywhere
        tm[c] = tm[c].fillna(tm["sun"])
    f = forcing().set_index("step")
    out = pd.DataFrame(index=f.index)
    for zone, col in (("sun", "sun"), ("tree", "tree"), ("bldg", "bldg")):
        out[f"U_{zone}"] = utci(f.ta_c, tm[col].reindex(f.index), f.wind_10m_utci_ms, f.rh_pct)
    out["source"] = "T06 pilot SOLWEIG Tmrt per T02 zone + T05 forcing"
    return out.reset_index()


def zone_utci_t08(seg_shade, t08):
    m = t08.merge(seg_shade, on=["segment_id", "step"])
    rows = []
    for s, d in m.groupby("step"):
        d = d.dropna(subset=["utci_mean", "b_mean", "tree_mean", "sun_mean"])
        if len(d) < 50 or d[["tree_mean", "sun_mean", "b_mean"]].sum().min() < 1:  # night: one value
            u = float(t08.loc[t08.step == s, "utci_mean"].median())
            rows.append({"step": s, "U_sun": u, "U_tree": u, "U_bldg": u})
            continue
        X = d[["sun_mean", "tree_mean", "b_mean"]].to_numpy()
        coef, *_ = np.linalg.lstsq(X, d.utci_mean.to_numpy(), rcond=None)
        rows.append({"step": s, "U_sun": coef[0], "U_tree": coef[1], "U_bldg": coef[2]})
    out = pd.DataFrame(rows)
    out["source"] = "per-step least squares of T08 utci_mean on T02 (sun, tree, building) shares"
    return out


def _labels(step):
    ts = T0 + pd.to_timedelta(step * 30, unit="min")
    lab = (pd.Timestamp("2026-06-28 06:00") + pd.to_timedelta(step * 30, unit="min")).dt.strftime("%H:%M")
    return ts, lab


def build_tables(seg_shade, stop_shade, zones, indoor_ids, all_seg_ids, weights=None):
    w = weights or common.WEIGHTS["spec"]
    z = zones.set_index("step")
    out = {}
    s = seg_shade.copy()
    Us, Ut, Ub = (z.loc[s.step, c].to_numpy() for c in ("U_sun", "U_tree", "U_bldg"))
    B = s.b_mean.fillna(0).to_numpy()
    Bq = s.b_q10.fillna(0).to_numpy()
    T = s.tree_mean.fillna(0).to_numpy()
    S = s.sun_mean.fillna(1).to_numpy()
    night = s.b_mean.isna().to_numpy()
    for name in ("bldg_only", "standin"):
        if name == "bldg_only":
            u = (1 - B) * Us + B * Ub
            p90 = (1 - Bq) * Us + Bq * Ub   # pixel UTCI is monotone in B -> p90 at B's p10
            shade = B
        else:
            u = S * Us + T * Ut + B * Ub
            p90 = u + 0.0  # not resolved per pixel in the stand-in
            shade = B + T
        u = np.where(night, Us, u)
        p90 = np.where(night, Us, p90)
        df = pd.DataFrame({"segment_id": s.segment_id.to_numpy(), "step": s.step.astype(int).to_numpy(),
                           "utci_mean": u, "utci_p90": p90, "shade_share": np.where(night, 1.0, shade)})
        # segments without pixels (indoor): constant comfortable value
        missing = np.setdiff1d(all_seg_ids, df.segment_id.unique())
        if len(missing):
            ind = pd.DataFrame({"segment_id": np.repeat(missing, 33), "step": np.tile(np.arange(33), len(missing)),
                                "utci_mean": INDOOR_UTCI, "utci_p90": INDOOR_UTCI, "shade_share": 1.0})
            df = pd.concat([df, ind], ignore_index=True)
        df.loc[df.segment_id.isin(indoor_ids), ["utci_mean", "utci_p90", "shade_share"]] = [INDOOR_UTCI, INDOOR_UTCI, 1.0]
        df["g"] = common.g_of_u(df.utci_mean, w)
        df["ts_utc"], df["label_local"] = _labels(df.step)
        out[f"segment_{name}"] = df[["segment_id", "step", "ts_utc", "label_local", "utci_mean", "utci_p90", "g",
                                     "shade_share"]]

        st = stop_shade.copy()
        zs = z.loc[st.step]
        bmax = st.b_max.fillna(0).to_numpy()
        nightst = st.b_max.isna().to_numpy()
        if name == "bldg_only":
            us = (1 - bmax) * zs.U_sun.to_numpy() + bmax * zs.U_bldg.to_numpy()  # best spot = most building shade
        else:
            # best spot: fully shaded by building or tree if any pixel is; else the area mean mixture
            tr = st.tree_mean.fillna(0).to_numpy()
            us = np.minimum((1 - bmax) * zs.U_sun.to_numpy() + bmax * zs.U_bldg.to_numpy(),
                            (1 - tr) * zs.U_sun.to_numpy() + tr * zs.U_tree.to_numpy())
        us = np.where(nightst, zs.U_sun.to_numpy(), us)
        sd = pd.DataFrame({"stop_id": st.stop_id.astype(str).to_numpy(), "step": st.step.astype(int).to_numpy(),
                           "utci": us})
        sd["g"] = common.g_of_u(sd.utci, w)
        sd["ts_utc"], sd["label_local"] = _labels(sd.step)
        out[f"stop_{name}"] = sd[["stop_id", "step", "ts_utc", "label_local", "utci", "g"]]
    return out


def load_network_ids():
    seg = _gcs_parquet("T03/network/segments.parquet")
    return seg.loc[seg.indoor.fillna(False), "segment_id"].to_numpy(), seg.segment_id.to_numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", choices=["pilot", "t08", "t08_stub"], default="pilot")
    a = ap.parse_args()
    seg_shade = _gcs_parquet("T11/bldg_only/segment_shade.parquet")
    stop_shade = _gcs_parquet("T11/bldg_only/stop_shade.parquet")
    indoor, all_ids = load_network_ids()
    if a.calib == "pilot":
        zones = zone_utci_pilot()
    else:
        from google.cloud import bigquery
        suf = "_stub" if a.calib == "t08_stub" else ""
        t08 = bigquery.Client(project="hackaton-honory").query(
            f"select segment_id, step, utci_mean from `hackaton-honory.warsaw_heat.t08_segment_heat{suf}`").to_dataframe()
        zones = zone_utci_t08(seg_shade, t08)
    data = common.OUT_DIR / "data"
    data.mkdir(parents=True, exist_ok=True)
    zones.to_csv(common.OUT_DIR / f"zone_utci_{a.calib}.csv", index=False, float_format="%.2f")
    tabs = build_tables(seg_shade, stop_shade, zones, indoor, all_ids)
    keep = ["segment_bldg_only", "stop_bldg_only"] + (["segment_standin", "stop_standin"] if a.calib == "pilot" else [])
    for k in keep:
        name = k.replace("segment_", "segment_heat_").replace("stop_", "stop_heat_")
        p = data / f"{name}.parquet"
        tabs[k].to_parquet(p, index=False)
        print(common.gcs_upload_file(p, f"{name}.parquet"), len(tabs[k]))  # current = last calibration run
        if "bldg_only" in k:
            print(common.gcs_upload_file(p, f"{name}_{a.calib}.parquet"))
    print(zones.round(1).to_string())


if __name__ == "__main__":
    main()
