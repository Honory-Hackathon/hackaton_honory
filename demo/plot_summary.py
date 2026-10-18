"""Summary chart: dose per candidate and trip (reports/T11/summary_<tag>.png). Usage: python -m demo.plot_summary --heat stub"""
import argparse

import pandas as pd

from demo import common

SERIES = [("ours_coolest", "Our coolest trip (AC + timing)", "#2a78d6"),
          ("walk_coolest", "Our coolest walk", "#1baf7a"),
          ("walk_bldg_only", "Walk routed on building-only shade", "#eda100"),
          ("ors_shaded", "ORS Shaded walk", "#eb6834"),
          ("walk_fastest", "Fastest walk", "#e87ba4")]
NAMES = {"noon_zbawiciela_uniwersytet": "Zbawiciela → Uniwersytet (noon)",
         "trees_centralny_lazienki": "Centralny → Łazienki (afternoon)",
         "evening_dmowskiego_trzech_krzyzy": "Dmowskiego → Trzech Krzyży (evening)",
         "transit_kopernik_mirowska": "Kopernik → Mirowska (afternoon)",
         "when_zamkowy_zbawiciela": "Zamkowy → Zbawiciela (08–18 window)"}


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ap = argparse.ArgumentParser()
    ap.add_argument("--heat", default="stub")
    a = ap.parse_args()
    c = pd.read_csv(common.OUT_DIR / f"comparison_{a.heat}.csv")
    trips = list(dict.fromkeys(c.trip))
    fig, ax = plt.subplots(figsize=(8, 6.2))
    h = 0.15
    ink, muted = "#2b2b2b", "#6b6b6b"
    for j, (k, lab, col) in enumerate(SERIES):
        for i, t in enumerate(trips):
            r = c[(c.trip == t) & (c.candidate == k)]
            if r.empty:
                continue
            v = r.dose_Ks.iloc[0] / 1000
            y = i + (j - 2) * (h + 0.02)
            ax.barh(y, v, height=h, color=col, label=lab if i == 0 else None, edgecolor="white", linewidth=1)
            ax.text(v + 0.8, y, f"{v:.0f}", va="center", fontsize=7, color=muted)
    ax.set_yticks(range(len(trips)))
    ax.set_yticklabels([NAMES.get(t, t) for t in trips], fontsize=8, color=ink)
    ax.invert_yaxis()
    ax.set_xlabel("Heat dose (thousand K·s, spec §7 weights) — lower is better", fontsize=8, color=ink)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(axis="x", colors=muted, labelsize=7)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color="#eeeeee", lw=0.8)
    ax.set_axisbelow(True)
    ax.legend(fontsize=7, frameon=False, loc="lower right")
    ax.set_title(f"Heat dose per demo trip ({a.heat} field); walks leave at the coolest trip's departure",
                 fontsize=9, color=ink, loc="left")
    fig.tight_layout()
    fig.savefig(common.OUT_DIR / f"summary_{a.heat}.png", dpi=90)


if __name__ == "__main__":
    main()
