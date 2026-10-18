"""T11 demo: comparison table per trip and dose-weight sensitivity. One command for every heat source:

    python -m demo.run_demo --heat final      # T08 tables (integration run)
    python -m demo.run_demo --heat stub       # t08_*_stub tables
    python -m demo.run_demo --heat standin    # T11's T02-zone stand-in field (pilot SOLWEIG zone UTCI)

Candidates per trip, all priced on the SAME field (the --heat one) with demo.engine's pricer:
  ours_*          T09 router options (coolest, balanced, fastest, walk_only) when `router` imports
  bldg_only       router's coolest option when it routes on building-only shade costs
                  (T11/segment_heat_bldg_only.parquet), re-priced on the real field
  walk_coolest    min-dose walk on the real field (demo.engine), the walking-route counterpart of ORS
  walk_bldg_only  min-dose walk on building-only shade costs: what trees add
  walk_fastest    shortest-time walk
  ors_shaded      ORS Shaded route (HeiGIT, slot 172_<h> for the departure, factor 0.8), cached
  ors_plain       ORS unweighted route (factor 0)
  fastest_now     fastest walk leaving at the earliest time (no time choice, no transit)
walk_* and ors_* depart at ours_coolest's departure (walk_coolest's best departure without the router), so
route choice is isolated from departure choice. Outputs: reports/T11/{comparison.csv, weights.csv, results_<heat>.json, map_<trip>.png}
and gs://hackaton-honory-heat/T11/results/<heat>/.
"""
import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from demo import common, engine, ors_shaded

REPO = Path(__file__).resolve().parent.parent


# ---------------- heat sources ----------------
def _bq(q):
    from google.cloud import bigquery
    return bigquery.Client(project="hackaton-honory").query(q).to_dataframe()


def _gcs_parquet(path):
    from google.cloud import storage
    b = storage.Client(project="hackaton-honory").bucket(common.BUCKET).blob(path)
    return pd.read_parquet(io.BytesIO(b.download_as_bytes()))


def load_heat(kind):
    if kind in ("final", "stub"):
        suf = "" if kind == "final" else "_stub"
        seg = _bq(f"select segment_id, step, utci_mean, utci_p90, g, shade_share "
                  f"from `hackaton-honory.warsaw_heat.t08_segment_heat{suf}`")
        stop = _bq(f"select cast(stop_id as string) stop_id, step, utci, g "
                   f"from `hackaton-honory.warsaw_heat.t08_stop_heat{suf}`")
    elif kind == "standin":
        seg = _gcs_parquet("T11/segment_heat_standin.parquet")
        stop = _gcs_parquet("T11/stop_heat_standin.parquet")
    else:
        raise ValueError(kind)
    stop["stop_id"] = stop.stop_id.astype(str)
    return seg, stop


def load_bldg_only():
    seg = _gcs_parquet("T11/segment_heat_bldg_only.parquet")
    stop = _gcs_parquet("T11/stop_heat_bldg_only.parquet")
    stop["stop_id"] = stop.stop_id.astype(str)
    return seg, stop


def try_router():
    if os.environ.get("T11_NO_ROUTER"):
        return None
    sys.path.insert(0, os.environ.get("ROUTER_PATH", str(REPO)))  # ROUTER_PATH: another checkout's root
    try:
        import router  # noqa
        if hasattr(router, "plan"):
            return router
    except Exception as e:
        print(f"router not importable ({e}); using the walk-only fallback")
    return None


# ---------------- candidates as legs ----------------
def walk_leg(coords, start_s):
    return {"mode": "walk", "coords": coords, "start_s": start_s}


def legs_from_router_option(opt):
    legs = []
    for L in opt["legs"]:
        s = engine.hhmm_to_s(L["start"])
        e = engine.hhmm_to_s(L["end"])
        if L["mode"] == "walk":
            legs.append({"mode": "walk", "coords": L["geometry"]["coordinates"], "start_s": s, "end_s": e})
        elif L["mode"] == "wait":
            legs.append({"mode": "wait", "stop_id": str(L.get("from_stop") or L.get("stop_id") or L.get("to_stop")),
                         "start_s": s, "end_s": e})
        else:
            g = L.get("geometry") or {}
            legs.append({"mode": "ride", "start_s": s, "end_s": e, "line": L.get("line"),
                         "coords": g.get("coordinates") if g.get("type") == "LineString" else None})
    return legs


def price_legs(net, field, legs, weights):
    """Re-price a multi-leg trip on `field`. Walk legs start at their own time if given, else chained."""
    T, DT, U, SH = [], [], [], []
    t = legs[0]["start_s"]
    dist = 0.0
    for L in legs:
        if L["mode"] == "walk":
            t0 = L.get("start_s", t)
            t0 = max(t0, t) if L.get("end_s") is None else t0
            if L.get("edges") is not None:
                seg_idx = np.asarray(L["edges"], int)
                pieces = net.length[seg_idx]
                dist += pieces.sum()
            elif len(L["coords"]) < 2:
                continue
            else:
                line, pieces, seg_idx, _ = net.sample_line(L["coords"])
                dist += line.length
            ts, dts, us, sh = net.price_samples(field, pieces, seg_idx, t0, weights)
            T += list(ts); DT += list(dts); U += list(us); SH += list(sh)
            t = ts[-1] + dts[-1] if len(ts) else t0
        elif L["mode"] == "wait":
            dur = L["end_s"] - L["start_s"]
            n = max(1, int(dur // 60))
            for k in range(n):
                tk = L["start_s"] + k * dur / n
                u = field.stop_utci(L["stop_id"], tk) if (field.stop is not None and L["stop_id"] in field.stop.index) else np.nan
                T.append(tk); DT.append(dur / n); U.append(u); SH.append(np.nan)
            t = L["end_s"]
        else:  # AC ride: U ~ 26, no dose
            T.append(L["start_s"]); DT.append(L["end_s"] - L["start_s"]); U.append(25.99); SH.append(np.nan)
            t = L["end_s"]
    return np.array(T), np.array(DT), np.array(U, float), np.array(SH, float), dist


def metrics(series, weights, depart_s):
    T, DT, U, SH, dist = series
    m = engine.summarize(T, DT, U, SH, weights, depart_s)
    m["distance_m"] = round(dist)
    m["depart"] = engine.s_to_hhmm(depart_s)
    m["arrive"] = engine.s_to_hhmm(T[-1] + DT[-1])
    return m


# ---------------- fallback walk-only planning ----------------
def walk_route(net, field, o, d, dep, mode, weights):
    ep = net.route(field, o, d, dep, mode, weights)
    leg = walk_leg(net.epath_coords(ep, o), dep)
    leg["edges"] = list(map(int, ep))  # our own network path: priced edge by edge, no snapping
    return [leg]


def coolest_walk(net, field, o, d, dep, weights):
    """Dose-min walk; the label-setting search is heuristic with time-dependent costs, so keep the fastest
    path if it prices lower."""
    best = None
    for mode in ("coolest", "fastest"):
        legs = walk_route(net, field, o, d, dep, mode, weights)
        s = price_legs(net, field, legs, weights)
        dose = float((common.g_of_u(np.nan_to_num(s[2], nan=26.0), weights) * s[1]).sum())
        if best is None or dose < best[0] - 1e-6:
            best = (dose, legs)
    return best[1]


def best_departure(net, field, o, d, earliest, latest, weights, step_min=30):
    best = None
    t = earliest
    while t <= latest:
        legs = coolest_walk(net, field, o, d, t, weights)
        s = price_legs(net, field, legs, weights)
        arr = s[0][-1] + s[1][-1]
        if arr <= latest + 60:
            dose = float((common.g_of_u(np.nan_to_num(s[2], nan=26.0), weights) * s[1]).sum())
            if best is None or dose < best[0] - 1e-6:
                best = (dose, t, legs)
        t += step_min * 60
    if best is None:  # window too short: leave at earliest
        legs = coolest_walk(net, field, o, d, earliest, weights)
        best = (None, earliest, legs)
    return best[1], best[2]


def plan_trip(net, field, bo_field, trip, weights, router=None, heat=None, bo_heat=None):
    e = engine.hhmm_to_s(trip["earliest"])
    l = engine.hhmm_to_s(trip["latest"])
    o = net.nearest_node(trip["origin"])
    d = net.nearest_node(trip["destination"])
    cands = {}
    notes = []
    if router is not None:
        res = router.plan(tuple(trip["origin"]), tuple(trip["destination"]), trip["earliest"], trip["latest"],
                          weights=None, segment_heat=heat[0], stop_heat=heat[1])
        for opt in res["options"]:
            cands[f"ours_{opt['kind']}"] = {"legs": legs_from_router_option(opt), "router": opt}
        notes += res.get("notes", [])
        coolest = next((v for k, v in cands.items() if k == "ours_coolest"), next(iter(cands.values())))
        dep = coolest["legs"][0]["start_s"]
        bo = router.plan(tuple(trip["origin"]), tuple(trip["destination"]), engine.s_to_hhmm(dep),
                         trip["latest"], weights=None, segment_heat=bo_heat[0], stop_heat=bo_heat[1])
        bo_opt = next((x for x in bo["options"] if x["kind"] == "coolest"), bo["options"][0])
        cands["bldg_only"] = {"legs": legs_from_router_option(bo_opt), "router_bo": bo_opt}  # multimodal
    else:
        dep, _ = best_departure(net, field, o, d, e, l, weights)
        notes.append("walk-only fallback engine (T09 router not importable): no transit options")
    # walking route choice at the compared departure: ours vs building-only vs ORS vs fastest
    cands["walk_coolest"] = {"legs": coolest_walk(net, field, o, d, dep, weights)}
    cands["walk_bldg_only"] = {"legs": coolest_walk(net, bo_field, o, d, dep, weights)}
    cands["walk_fastest"] = {"legs": walk_route(net, field, o, d, dep, "fastest", weights)}
    hh = engine.s_to_hhmm(dep)
    for name, f in (("ors_shaded", ors_shaded.FACTOR), ("ors_plain", 0.0)):
        col = ors_shaded.column_for(hh) if f else f"{ors_shaded.DOY}_12"
        ans = ors_shaded.get_route(trip, col, f)
        if ans:
            cands[name] = {"legs": [walk_leg(ors_shaded.route_coords(ans), dep)], "ors": ors_shaded.route_summary(ans),
                           "ors_column": col}
            if router is not None:
                try:
                    cands[name]["router"] = router.price_path(ors_shaded.route_coords(ans), hh,
                                                              segment_heat=heat[0], stop_heat=heat[1])
                except Exception as ex:
                    notes.append(f"router.price_path failed for {name}: {ex}")
        else:
            notes.append(f"ORS {name} unavailable")
    cands["fastest_now"] = {"legs": walk_route(net, field, o, d, e, "fastest", weights)}
    return dep, cands, notes


def run(heat_kind, trips_path, out_dir, weights_name="spec"):
    t0 = time.time()
    net = engine.walknet()
    heat = load_heat(heat_kind)
    bo_heat = load_bldg_only()
    field = engine.HeatField(heat[0], net.seg_ids, heat[1])
    bo_field = engine.HeatField(bo_heat[0], net.seg_ids, bo_heat[1])
    router = try_router()
    W = common.WEIGHTS[weights_name]
    tag = heat_kind if router is not None else f"{heat_kind}_walkonly"
    trips = common.load_trips(trips_path)
    rows, wrows, results = [], [], []
    for trip in trips:
        print(f"-- {trip['id']}", flush=True)
        dep, cands, notes = plan_trip(net, field, bo_field, trip, W, router, heat, bo_heat)
        series = {k: price_legs(net, field, v["legs"], W) for k, v in cands.items()}
        for k, s in series.items():
            m = metrics(s, W, cands[k]["legs"][0]["start_s"])
            m.update({"trip": trip["id"], "candidate": k,
                      "rides": sum(1 for L in cands[k]["legs"] if L["mode"] == "ride")})
            if "router" in cands[k]:
                m["router_dose_Ks"] = cands[k]["router"].get("dose_Ks")
            if "ors" in cands[k]:
                m["ors_sun_pct"] = round(cands[k]["ors"]["ors_sun_pct"] or np.nan, 1)
                m["ors_column"] = cands[k]["ors_column"]
            rows.append(m)
        # weight sensitivity: re-score the same candidates; rank by dose
        for wn, w in common.WEIGHTS.items():
            sc = {k: float((common.g_of_u(np.nan_to_num(s[2], nan=26.0), w) * s[1]).sum()) for k, s in series.items()}
            order = sorted(sc, key=sc.get)
            for r, k in enumerate(order, 1):
                wrows.append({"trip": trip["id"], "weights": wn, "candidate": k, "dose_Ks": round(sc[k], 1), "rank": r})
            # and re-plan the coolest route under these weights (fallback engine only)
            if wn != weights_name:
                legs = coolest_walk(net, field, net.nearest_node(trip["origin"]), net.nearest_node(trip["destination"]),
                                    dep, w)
                s = price_legs(net, field, legs, w)
                base = series["walk_coolest"]  # noqa
                wrows.append({"trip": trip["id"], "weights": wn, "candidate": "walk_coolest_replanned",
                              "dose_Ks": round(float((common.g_of_u(np.nan_to_num(s[2], nan=26.0), w) * s[1]).sum()), 1),
                              "rank": None, "distance_m": round(s[4]),
                              "same_route_as_spec": bool(abs(s[4] - base[4]) < 1.0)})
        results.append({"trip": trip, "depart_compared": engine.s_to_hhmm(dep), "notes": notes,
                        "candidates": {k: {"legs": [{kk: vv for kk, vv in L.items()} for L in v["legs"]]}
                                       for k, v in cands.items()}})
        plot_trip(net, trip, cands, [r for r in rows if r["trip"] == trip["id"]], out_dir, tag)
    comp = pd.DataFrame(rows)
    cats = pd.json_normalize(comp.pop("minutes_per_category")).add_prefix("min_")
    comp = pd.concat([comp, cats], axis=1)
    comp = comp.rename(columns={"distance_m": "walk_m"})
    lead = ["trip", "candidate", "depart", "arrive", "duration_min", "walk_m", "dose_Ks", "peak_utci",
            "shade_share", "rides"]
    comp = comp[lead + [c for c in comp.columns if c not in lead]]
    wdf = pd.DataFrame(wrows)
    out_dir.mkdir(parents=True, exist_ok=True)
    comp.to_csv(out_dir / f"comparison_{tag}.csv", index=False)
    wdf.to_csv(out_dir / f"weights_{tag}.csv", index=False)
    with open(out_dir / f"results_{tag}.json", "w") as f:
        json.dump({"heat": heat_kind, "router": bool(router), "weights": weights_name,
                   "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "trips": results}, f)
    for p in (f"comparison_{tag}.csv", f"weights_{tag}.csv", f"results_{tag}.json"):
        common.gcs_upload_file(out_dir / p, f"results/{tag}/{p}")
    print(f"done in {time.time() - t0:.0f}s")
    return comp, wdf


COLORS = {"ours_coolest": "#1a9850", "walk_coolest": "#66bd63", "walk_bldg_only": "#762a83",
          "ors_shaded": "#d73027", "walk_fastest": "#4575b4"}


def plot_trip(net, trip, cands, rows, out_dir, heat_kind):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely import get_coordinates
    xs, ys = [], []
    lines = {}
    for k, v in cands.items():
        if k not in COLORS:
            continue
        for L in v["legs"]:
            if L["mode"] in ("walk", "ride") and L.get("coords") is not None and len(L["coords"]) > 1:
                c = np.asarray(L["coords"])
                x, y = net.to2180.transform(c[:, 0], c[:, 1])
                lines.setdefault(k, []).append((x, y, L["mode"]))
                xs += list(x); ys += list(y)
    if not xs:
        return
    pad = 120
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    fig, ax = plt.subplots(figsize=(6.5, 6.5 * (y1 - y0) / max(x1 - x0, 1)) if (y1 - y0) < 1.6 * (x1 - x0) else (5, 7.5))
    bg = net.seg.cx[x0:x1, y0:y1]
    bg.plot(ax=ax, color="#dddddd", linewidth=0.4)
    byk = {r["candidate"]: r for r in rows}
    for i, (k, segs) in enumerate(lines.items()):
        r = byk.get(k, {})
        lab = f"{k}: {r.get('dose_Ks', 0):.0f} K·s, {r.get('duration_min', 0):.0f} min"
        for j, (x, y, mode) in enumerate(segs):
            ax.plot(x, y, color=COLORS.get(k, f"C{i}"), lw=1.2 if mode == "ride" else (2.2 if "cool" in k else 1.6),
                    alpha=0.85, label=lab if j == 0 else None,
                    ls=":" if mode == "ride" else ("--" if k.startswith("ors") else "-"))
    ox, oy = net.to2180.transform(*trip["origin"])
    dx, dy = net.to2180.transform(*trip["destination"])
    ax.plot([ox], [oy], "ko", ms=6)
    ax.plot([dx], [dy], "k*", ms=10)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    dep = rows[0]["depart"] if rows else ""
    ax.set_title(f"{trip['name']}\ncompared at {dep} ({heat_kind} field; dotted = AC ride)", fontsize=9)
    ax.legend(fontsize=7, loc="best", framealpha=0.85)
    fig.tight_layout()
    fig.savefig(out_dir / f"map_{trip['id']}_{heat_kind}.png", dpi=80)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heat", default=os.environ.get("HEAT_SOURCE", "standin"), choices=["final", "stub", "standin"])
    ap.add_argument("--trips", default=common.TRIPS_PATH)
    ap.add_argument("--out", default=str(common.OUT_DIR))
    a = ap.parse_args()
    comp, wdf = run(a.heat, a.trips, Path(a.out))
    pd.set_option("display.width", 200)
    print(comp[["trip", "candidate", "depart", "duration_min", "walk_m", "dose_Ks", "peak_utci", "shade_share"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
