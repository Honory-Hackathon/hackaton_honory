"""Headline numbers from reports/T11/comparison_<heat>.csv and weights_<heat>.csv.

Per trip: dose saved by our coolest option against fastest-now, ORS Shaded and building-only routes, with the
extra minutes it costs. Writes reports/T11/headline_<heat>.md (pasted into the README) and .json.
Usage: python -m demo.headline --heat standin|stub|final
"""
import argparse
import json

import pandas as pd

from demo import common


def _cmp(d, ours, base):
    if ours not in d.index or base not in d.index:
        return None, None
    o, b = d.loc[ours], d.loc[base]
    saved = round(100 * (1 - o.dose_Ks / b.dose_Ks), 1) if b.dose_Ks > 0 else None
    return saved, round(o.duration_min - b.duration_min, 1)


PAIRS = [  # (label, ours, baseline)
    ("trip_vs_walk_now", "ours_coolest", "fastest_now"),
    ("trip_vs_ours_fastest", "ours_coolest", "ours_fastest"),
    ("trip_vs_bldg_only", "ours_coolest", "bldg_only"),
    ("walk_vs_walk_fastest", "walk_coolest", "walk_fastest"),
    ("walk_vs_ors_shaded", "walk_coolest", "ors_shaded"),
    ("walk_vs_walk_bldg_only", "walk_coolest", "walk_bldg_only"),
    ("ors_shaded_vs_walk_fastest", "ors_shaded", "walk_fastest"),
]


def summarize(heat):
    comp = pd.read_csv(common.OUT_DIR / f"comparison_{heat}.csv")
    w = pd.read_csv(common.OUT_DIR / f"weights_{heat}.csv")
    out = []
    for trip, d in comp.groupby("trip", sort=False):
        d = d.set_index("candidate")
        row = {"trip": trip}
        for k in ("ours_coolest", "walk_coolest"):
            if k in d.index:
                row[f"{k}_depart"] = d.loc[k, "depart"]
                row[f"{k}_dose"] = d.loc[k, "dose_Ks"]
                row[f"{k}_min"] = d.loc[k, "duration_min"]
        for lab, o, b in PAIRS:
            row[f"{lab}_saved_pct"], row[f"{lab}_extra_min"] = _cmp(d, o, b)
        ww = w[(w.trip == trip) & w["rank"].notna()]
        row["winner_by_weights"] = ww[ww["rank"] == 1].set_index("weights").candidate.to_dict()
        rp = w[(w.trip == trip) & (w.candidate == "walk_coolest_replanned")]
        row["walk_route_changes_with_weights"] = {r.weights: (not bool(r.same_route_as_spec)) for r in rp.itertuples()}
        out.append(row)
    return out


def to_md(rows, heat):
    def f(r, lab):
        s, m = r.get(f"{lab}_saved_pct"), r.get(f"{lab}_extra_min")
        return "–" if s is None or pd.isna(s) else f"{s:+.0f} % ({m:+.0f} min)"
    lines = [f"Field: `{heat}`. Dose saved = 1 − ours / baseline (spec §7 weights); minutes = ours − baseline.", "",
             "| Trip | Coolest trip vs walking now | vs our fastest | vs building-only routing | "
             "Coolest walk vs fastest walk | vs ORS Shaded | vs building-only walk |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['trip']} | {f(r, 'trip_vs_walk_now')} | {f(r, 'trip_vs_ours_fastest')} | "
                     f"{f(r, 'trip_vs_bldg_only')} | {f(r, 'walk_vs_walk_fastest')} | {f(r, 'walk_vs_ors_shaded')} | "
                     f"{f(r, 'walk_vs_walk_bldg_only')} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heat", default="standin")
    a = ap.parse_args()
    rows = summarize(a.heat)
    md = to_md(rows, a.heat)
    (common.OUT_DIR / f"headline_{a.heat}.md").write_text(md + "\n")
    with open(common.OUT_DIR / f"headline_{a.heat}.json", "w") as f:
        json.dump(rows, f, indent=1, default=str)
    common.gcs_upload_file(common.OUT_DIR / f"headline_{a.heat}.json", f"results/{a.heat}/headline_{a.heat}.json")
    print(md)


if __name__ == "__main__":
    main()
