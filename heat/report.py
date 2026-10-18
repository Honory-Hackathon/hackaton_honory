"""T08 report figures and side-of-street check (runs locally on the small outputs).

python report.py DATA_DIR OUT_DIR
  DATA_DIR: segments.parquet (T03), t08_segment_heat.parquet, t08_stop_heat.parquet, checks.json,
            streets.json (Overpass ways named Marszałkowska / Aleje Ujazdowskie, out geom)
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from pyproj import Transformer
from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.ops import linemerge, unary_union

WALK_KINDS = {"sidewalk", "footway", "street_side", "pedestrian", "path", "shared_street"}
STEPS = {"09:00": 6, "13:00": 14, "18:00": 24}


def streets(path):
    tr = Transformer.from_crs(4326, 2180, always_xy=True)
    d = json.load(open(path))
    out = {}
    for e in d["elements"]:
        xs, ys = tr.transform([p["lon"] for p in e["geometry"]], [p["lat"] for p in e["geometry"]])
        out.setdefault(e["tags"]["name"], []).append(LineString(zip(xs, ys)))
    return {k: unary_union(v) for k, v in out.items()}


def side_table(seg, heat, line, name, maxd=30):
    cand = seg[seg.kind.isin(WALK_KINDS) & ~seg.indoor & ~seg.crossing.fillna(False)].copy()
    geoms = cand.geometry.map(wkb.loads)
    cand = cand[geoms.map(lambda g: g.distance(line) < maxd).values]
    geoms = cand.geometry.map(wkb.loads)
    rows = []
    for sid, g, L in zip(cand.segment_id, geoms, cand.length_m):
        c = g.interpolate(0.5, normalized=True)
        s = line.project(c)
        p = line.interpolate(s)
        p2 = line.interpolate(min(s + 5, line.length))
        p1 = line.interpolate(max(s - 5, 0))
        tx, ty = p2.x - p1.x, p2.y - p1.y
        tn = np.hypot(tx, ty)
        if tn == 0 or c.distance(p) < 2:
            continue
        a, b = g.coords[0], g.coords[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        dn = np.hypot(dx, dy)
        if dn == 0 or abs(dx * tx + dy * ty) / (dn * tn) < 0.85:  # roughly parallel (< ~32°)
            continue
        side = "west" if c.x < p.x else "east"
        rows.append((sid, side, L, c.y))
    sides = pd.DataFrame(rows, columns=["segment_id", "side", "length_m", "y"])
    h = heat.merge(sides, on="segment_id")
    agg = (h.assign(wu=h.utci_mean * h.length_m, wg=h.g * h.length_m, ws=h.shade_share * h.length_m)
           .groupby(["step", "label_local", "side"])[["wu", "wg", "ws", "length_m"]].sum())
    agg["utci"] = agg.wu / agg.length_m
    agg["g"] = agg.wg / agg.length_m
    agg["shade"] = agg.ws / agg.length_m
    agg = agg[["utci", "g", "shade", "length_m"]].reset_index()
    agg["street"] = name
    return sides, agg


def seg_lines(seg):
    return [np.asarray(wkb.loads(g).coords)[:, :2] for g in seg.geometry]


def map_fig(seg, heat, bbox, title, out, steps=("13:00", "18:00")):
    x0, y0, x1, y1 = bbox
    fig, axes = plt.subplots(1, len(steps), figsize=(4.2 * len(steps), 4.6), constrained_layout=True)
    b = seg.geometry.map(lambda g: wkb.loads(g).bounds)
    inb = b.map(lambda t: t[2] > x0 and t[0] < x1 and t[3] > y0 and t[1] < y1)
    s = seg[inb.values & ~seg.indoor.values]
    lines = seg_lines(s)
    for ax, lab in zip(np.atleast_1d(axes), steps):
        h = heat[heat.step == STEPS[lab]].set_index("segment_id").loc[s.segment_id]
        lc = LineCollection(lines, array=h.utci_mean.values, cmap="RdYlBu_r", linewidths=1.6)
        lc.set_clim(28, 46)
        ax.add_collection(lc)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{title}, {lab} CEST", fontsize=9)
        ax.set_facecolor("#eeeeee")
    fig.colorbar(lc, ax=axes, shrink=0.7, label="segment UTCI mean (°C)")
    fig.savefig(out, dpi=80)
    plt.close(fig)


def main(data, out):
    data, out = Path(data), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    seg = pd.read_parquet(data / "segments.parquet")
    seg["indoor"] = seg.indoor.fillna(False).astype(bool)
    heat = pd.read_parquet(data / "t08_segment_heat.parquet")
    stops = pd.read_parquet(data / "t08_stop_heat.parquet")
    st = streets(data / "streets.json")
    res = {}

    # sides
    aggs = []
    for name, line in st.items():
        sides, agg = side_table(seg, heat, line, name)
        aggs.append(agg)
        res[name] = {"segments": sides.groupby("side").size().to_dict(),
                     "length_m": sides.groupby("side").length_m.sum().round(0).to_dict()}
    agg = pd.concat(aggs)
    agg.to_csv(out / "sides.csv", index=False, float_format="%.3f")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True, sharey=True)
    for ax, (name, a) in zip(axes, agg.groupby("street")):
        for side, c in [("west", "#1f77b4"), ("east", "#d62728")]:
            s = a[a.side == side].sort_values("step")
            ax.plot(s.step, s.utci, color=c, label=f"{side} side")
        ax.set_xticks(range(0, 33, 4))
        ax.set_xticklabels(a.sort_values("step").drop_duplicates("step").label_local.values[::4], fontsize=8)
        ax.set_title(name)
        ax.grid(alpha=0.3)
        ax.legend()
    axes[0].set_ylabel("length-weighted UTCI (°C)")
    fig.savefig(out / "sides_timeseries.png", dpi=80)
    plt.close(fig)

    # maps: Marszałkowska around Pl. Konstytucji / Zbawiciela, Al. Ujazdowskie at Pl. Trzech Krzyży..Piękna
    map_fig(seg, heat, (637300, 485350, 638000, 486250), "Marszałkowska", out / "map_marszalkowska.png")
    map_fig(seg, heat, (638250, 485300, 638950, 486200), "Aleje Ujazdowskie", out / "map_ujazdowskie.png")
    map_fig(seg, heat, (636000, 485000, 640500, 489500), "Śródmieście", out / "map_overview.png")

    # rules summary
    res["segment_rules"] = heat.groupby("rule").size().to_dict()
    res["stop_rules"] = stops.groupby("rule").size().to_dict()
    cov = seg[seg.covered.fillna(False) & ~seg.indoor].segment_id
    hc = heat[heat.segment_id.isin(cov) & heat.step.isin(list(STEPS.values()))]
    hall = heat[heat.segment_id.isin(seg[~seg.indoor].segment_id) & heat.step.isin(list(STEPS.values()))]
    res["covered_vs_all"] = {
        "covered_shade_mean": hc.groupby("label_local").shade_share.mean().round(3).to_dict(),
        "covered_utci_mean": hc.groupby("label_local").utci_mean.mean().round(2).to_dict(),
        "all_outdoor_shade_mean": hall.groupby("label_local").shade_share.mean().round(3).to_dict(),
        "all_outdoor_utci_mean": hall.groupby("label_local").utci_mean.mean().round(2).to_dict(),
    }
    sh = stops[stops.step.isin(list(STEPS.values()))]
    res["stops"] = {
        "utci_mean_by_step": sh.groupby("label_local").utci.mean().round(2).to_dict(),
        "area_mean_by_step": sh.groupby("label_local").utci_area_mean.mean().round(2).to_dict(),
        "shelter_rule_share_by_step": sh.assign(x=sh.rule == "shelter").groupby("label_local").x.mean().round(3).to_dict(),
    }
    night = heat[heat.step >= 30]
    res["night"] = {"null_utci": int(night.utci_mean.isna().sum()),
                    "utci_mean_by_step": night.groupby("label_local").utci_mean.mean().round(2).to_dict()}
    piv = agg[agg.label_local.isin(STEPS)].pivot_table(index=["street", "label_local"], columns="side",
                                                         values=["utci", "g", "shade"])
    print(piv.round(2).to_string())
    res["sides_key_steps"] = {f"{k[0]} {k[1]}": {f"{a}_{b}": round(float(v), 3) for (a, b), v in r.items()}
                              for k, r in piv.iterrows()}
    (out / "summary.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "sides_key_steps"}, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
