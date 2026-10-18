"""Answer the T03 test pairs at several departure windows; write reports/T09/ tables, sample JSON and previews.

    HEAT_SOURCE=placeholder python -m router.examples --out reports/T09
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import get_router

PLACES = {  # (lon, lat), same points as walk/routes.py
    "Plac Zbawiciela": (21.01864, 52.21971),
    "Krakowskie Przedmieście (Uniwersytet)": (21.01661, 52.24007),
    "Dworzec Centralny": (21.00404, 52.22887),
    "Łazienki main gate (Al. Ujazdowskie)": (21.02645, 52.21636),
    "Rondo Dmowskiego": (21.01173, 52.22983),
    "Plac Trzech Krzyży": (21.02340, 52.22830),
    "Centrum Nauki Kopernik": (21.02870, 52.24190),
    "Hala Mirowska": (20.99860, 52.23880),
}
PAIRS = [
    ("Plac Zbawiciela", "Krakowskie Przedmieście (Uniwersytet)"),
    ("Dworzec Centralny", "Łazienki main gate (Al. Ujazdowskie)"),
    ("Rondo Dmowskiego", "Plac Trzech Krzyży"),
    ("Centrum Nauki Kopernik", "Hala Mirowska"),
]
WINDOWS = [("08:00", "09:00"), ("11:00", "12:00"), ("13:00", "14:00"), ("15:00", "16:00"), ("17:00", "18:00"),
           ("19:30", "20:30"), ("21:00", "22:00"), ("10:00", "18:00")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/T09")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "examples").mkdir(parents=True, exist_ok=True)
    t = time.time()
    r = get_router()
    load_s = time.time() - t
    rows, samples = [], {}
    for o, d in PAIRS:
        for e, l in WINDOWS:
            res = r.plan(PLACES[o], PLACES[d], e, l)
            for op in res["options"]:
                rides = [lg for lg in op["legs"] if lg["mode"] == "ride"]
                rows.append({
                    "from": o, "to": d, "earliest": e, "latest": l, "kind": op["kind"],
                    "depart": op["depart"], "arrive": op["arrive"], "duration_min": op["duration_min"],
                    "dose_Ks": op["dose_Ks"], "peak_utci": op["peak_utci"], "shade_share": op["shade_share"],
                    "walk_min": op["walk_min"], "walk_m": op["walk_m"], "n_rides": op["n_rides"],
                    "lines": " + ".join(lg["line"] for lg in rides),
                    "wait_min": round(sum(lg["duration_min"] for lg in op["legs"] if lg["mode"] == "wait"), 1),
                    "same_as": op.get("same_as"),
                    **{f"min_{k}": v for k, v in op["minutes_per_category"].items()},
                    "runtime_s": res["query"]["runtime_s"], "heat_source": res["query"]["heat_source"],
                })
            if (e, l) in (("13:00", "14:00"), ("10:00", "18:00")):
                samples[f"{o} -> {d} {e}-{l}"] = res
    df = pd.DataFrame(rows)
    df.to_csv(out / "examples.csv", index=False)
    key = "Plac Zbawiciela -> Krakowskie Przedmieście (Uniwersytet) 13:00-14:00"
    (out / "examples" / "plan_sample.json").write_text(json.dumps(samples[key], ensure_ascii=False, indent=1))
    rt = df.drop_duplicates(["from", "to", "earliest", "latest"]).runtime_s
    summary = {"load_s": round(load_s, 2), "queries": int(len(rt)), "runtime_median_s": float(rt.median()),
               "runtime_max_s": float(rt.max()), "heat_source": r.base_heat.source}
    (out / "examples" / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))
    pv = df.pivot_table(index=["from", "earliest", "latest"], columns="kind", values=["dose_Ks", "duration_min"])
    print(pv.round(0).to_string())
    preview(samples, out)


def preview(samples, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"coolest": "#1f77b4", "balanced": "#2ca02c", "fastest": "#d62728", "walk_only": "#7f7f7f"}
    keys = [k for k in samples if k.endswith("13:00-14:00")][:4]
    fig, axes = plt.subplots(2, 2, figsize=(10, 10))
    for ax, k in zip(axes.ravel(), keys):
        res = samples[k]
        for op in res["options"]:
            if op.get("same_as"):
                continue
            c = colors[op["kind"]]
            for lg in op["legs"]:
                g = lg["geometry"]
                if g["type"] == "LineString":
                    xy = np.array(g["coordinates"])
                    ax.plot(xy[:, 0], xy[:, 1], color=c, lw=3 if lg["mode"] == "ride" else 1.5,
                            ls="--" if lg["mode"] == "ride" else "-", alpha=0.8)
                else:
                    ax.plot(*g["coordinates"], "o", color=c, ms=5)
            ax.plot([], [], color=c, label=f"{op['kind']} {op['depart']}-{op['arrive']} "
                                            f"{op['dose_Ks'] / 1000:.1f} kKs")
        ax.set_title(k.replace(" -> ", " →\n"), fontsize=8)
        ax.set_aspect(1 / np.cos(np.radians(52.23)))
        ax.legend(fontsize=7, loc="best")
        ax.tick_params(labelsize=6)
    fig.suptitle("T09 options, window 13:00-14:00 (solid = walk, dashed = AC ride, dot = wait)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "options_1300.png", dpi=80)


if __name__ == "__main__":
    main()
