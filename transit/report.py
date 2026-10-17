"""T04 report: coverage, AC, timetable deviation and gaps from the outputs of transit.stop_events.

usage: python -m transit.report OUT_DIR [--report-dir reports/T04]
"""

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TZ = "Europe/Warsaw"
COLOUR = {"tram": "#2a78d6", "bus": "#eb6834"}
GREY, INK, MUTED = "#d6d5cf", "#2b2b29", "#76756f"
MAIN_STOPS = ["Centrum", "Pl. Zbawiciela", "Muzeum Narodowe", "Dw. Centralny", "Pl. Bankowy",
              "Pl. Konstytucji"]

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#ecebe6", "grid.linewidth": 0.6, "axes.axisbelow": True,
    "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "savefig.dpi": 150,
})


def md_table(df, floatfmt="{:.0f}"):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    rows = ["| " + " | ".join(map(str, r)) + " |" for r in df.itertuples(index=False)]
    return "\n".join([head, sep, *rows])


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "–"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("--report-dir", type=Path, default=Path("reports/T04"))
    a = ap.parse_args()
    rd = a.report_dir
    rd.mkdir(parents=True, exist_ok=True)

    ev = pd.read_parquet(a.out / "stop_events.parquet")
    runs = pd.read_parquet(a.out / "runs.parquet")
    sch = pd.read_parquet(a.out / "scheduled.parquet")
    groups = pd.read_parquet(a.out / "report_groups.parquet")
    pings = pd.read_parquet(a.out / "raw_pings.parquet")
    diag = json.loads((a.out / "diagnostics.json").read_text())
    w0, w1 = (pd.Timestamp(t) for t in diag["window"])

    sch = sch[(sch.sched_departure >= w0) & (sch.sched_departure < w1)].copy()
    sch["hour"] = sch.sched_departure.dt.tz_convert(TZ).dt.hour
    ev["hour"] = ev.departure.dt.tz_convert(TZ).dt.hour
    key = ["trip_id", "stop_sequence"]
    sch["recorded"] = pd.MultiIndex.from_frame(sch[key]).isin(pd.MultiIndex.from_frame(ev[key]))
    sch["operated"] = sch.trip_id.isin(runs.trip_id)

    # --- coverage --------------------------------------------------------------------------------
    trips = sch.groupby(["trip_id", "mode", "line"], as_index=False).operated.first()
    cov = []
    for mode in ("tram", "bus"):
        t, c = trips[trips["mode"] == mode], sch[sch["mode"] == mode]
        r = runs[runs["mode"] == mode]
        cov.append(dict(mode=mode, scheduled_trips=len(t), operated_trips=int(t.operated.sum()),
                        trips_share=pct(t.operated.sum(), len(t)), scheduled_stop_calls=len(c),
                        recorded_stop_calls=int(c.recorded.sum()), calls_share=pct(c.recorded.sum(), len(c)),
                        calls_share_on_operated=pct(c[c.operated].recorded.sum(), c.operated.sum()),
                        runs=len(r)))
    cov = pd.DataFrame(cov)

    hourly = sch.groupby(["mode", "hour"]).agg(scheduled=("recorded", "size"), recorded=("recorded", "sum"))
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2), sharey=True)
    for ax, mode in zip(axes, ("tram", "bus")):
        h = hourly.loc[mode]
        ax.bar(h.index, h.scheduled, width=0.8, color=GREY, label="scheduled")
        ax.bar(h.index, h.recorded, width=0.56, color=COLOUR[mode], label="recorded")
        ax.set_title(f"{mode}: AOI stop calls per hour", loc="left", color=INK)
        ax.set_xlabel("scheduled departure hour (local)")
        ax.set_xticks(range(6, 22, 2))
        ax.legend(frameon=False, loc="lower center")
    axes[0].set_ylabel("stop calls")
    fig.tight_layout()
    fig.savefig(rd / "coverage_hourly.png")
    plt.close(fig)

    hourly_tab = hourly.assign(share=lambda d: 100 * d.recorded / d.scheduled).share.unstack(0).round(1)
    hourly_tab = hourly_tab.reset_index().rename(columns={"hour": "hour", "tram": "tram %", "bus": "bus %"})

    main = ev[ev.stop_name.isin(MAIN_STOPS) & ev.hour.between(6, 21)]
    main_tab = main.groupby(["stop_name", "hour"]).size().unstack(fill_value=0)
    main_tab = main_tab.reindex([s for s in MAIN_STOPS if s in main_tab.index])
    main_tab.columns = [f"{h:02d}" for h in main_tab.columns]
    main_tab = main_tab.reset_index().rename(columns={"stop_name": "stop"})

    unop = (trips[~trips.operated].groupby(["mode", "line"]).size().rename("not operated")
            .reset_index().sort_values("not operated", ascending=False))
    per_line_trips = trips.groupby(["mode", "line"]).size().rename("scheduled").reset_index()
    unop = unop.merge(per_line_trips, on=["mode", "line"])

    # --- AC ----------------------------------------------------------------------------------------
    runs["ac_state"] = np.where(runs.ac.isna(), "unknown", np.where(runs.ac.fillna(False), "AC", "no AC"))
    ac_runs = runs.groupby(["mode", "ac_state"]).size().unstack(fill_value=0)
    ac_runs["AC share"] = (100 * ac_runs["AC"] / ac_runs.sum(axis=1)).round(1)
    ac_runs = ac_runs.reset_index()
    ev_ac = ev.groupby("mode").ac_boardable.mean().mul(100).round(1)
    line_ac = runs.groupby(["mode", "line"]).agg(runs=("run_id", "size"), ac=("ac_boardable", "mean"))
    line_ac = line_ac[line_ac.ac < 0.95].assign(ac=lambda d: (100 * d.ac).round(0)).reset_index()
    line_ac = line_ac.sort_values("ac").rename(columns={"ac": "AC runs %"})
    non_ac = (runs[runs.ac == False].groupby(["mode", "brand", "model_type"], dropna=False)  # noqa: E712
              .agg(runs=("run_id", "size"), vehicles=("vehicle_number", "nunique")).reset_index()
              .sort_values("runs", ascending=False))
    unknown = runs[runs.ac.isna()]
    unknown_tab = (unknown.assign(why=np.where(unknown.in_register, "model without AC info", "not in register"))
                   .groupby(["mode", "why"]).agg(runs=("run_id", "size"),
                                                 vehicles=("vehicle_number", "nunique")).reset_index())

    # --- deviation from timetable ------------------------------------------------------------------
    q = lambda p: (lambda s: s.quantile(p))  # noqa: E731
    dl = ev.groupby(["mode", "line"]).delay_s.agg(
        events="size", p05=q(.05), p10=q(.10), median="median", p90=q(.90), p95=q(.95),
        early_60s=lambda s: 100 * (s < -60).mean()).reset_index()
    dl.to_csv(rd / "line_delays.csv", index=False, float_format="%.0f")
    overall = ev.groupby("mode").delay_s.describe(percentiles=[.05, .1, .5, .9, .95]).round(0)
    overall = overall[["count", "5%", "10%", "50%", "90%", "95%"]].reset_index()
    early = ev.groupby("mode").delay_s.apply(lambda s: 100 * (s < -60).mean()).round(1)

    fig, axes = plt.subplots(1, 2, figsize=(9, 11), gridspec_kw=dict(width_ratios=[1, 1]))
    for ax, mode in zip(axes, ("tram", "bus")):
        d = dl[dl["mode"] == mode].sort_values("median").reset_index(drop=True)
        if mode == "bus":
            d = d[d.events >= 100].reset_index(drop=True)
        y = np.arange(len(d))
        ax.hlines(y, d.p10 / 60, d.p90 / 60, color=COLOUR[mode], alpha=0.45, linewidth=3)
        ax.plot(d["median"] / 60, y, "o", color=COLOUR[mode], markersize=4)
        ax.axvline(0, color=MUTED, linewidth=0.8)
        ax.set_yticks(y, d.line, fontsize=7 if mode == "bus" else 8)
        ax.set_ylim(-1, len(y))
        ax.set_xlabel("departure delay at Śródmieście stops (min)")
        sub = "" if mode == "tram" else " (lines with ≥100 events)"
        ax.set_title(f"{mode}: median and p10–p90{sub}", loc="left", color=INK, fontsize=9)
        ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(rd / "line_delays.png")
    plt.close(fig)

    # --- map of stops ------------------------------------------------------------------------------
    st = ev[ev.ac_boardable].groupby(["stop_id", "stop_name", "stop_lat", "stop_lon"]).size().rename("n")
    st = st.reset_index()
    fig, ax = plt.subplots(figsize=(6.5, 7))
    lon_scale = np.cos(np.radians(52.23))
    all_stops = sch.drop_duplicates("stop_id")
    ax.scatter(all_stops.stop_lon * lon_scale, all_stops.stop_lat, s=6, color=GREY, label="AOI stop", zorder=1)
    ax.scatter(st.stop_lon * lon_scale, st.stop_lat, s=st.n / 4, color=COLOUR["tram"], alpha=0.55,
               edgecolor="#fcfcfb", linewidth=0.5, zorder=2)
    for name in MAIN_STOPS:
        s = st[st.stop_name == name]
        if len(s):
            ax.annotate(name, (s.stop_lon.mean() * lon_scale, s.stop_lat.mean()), fontsize=7, color=INK,
                        xytext=(6, 6), textcoords="offset points")
    for n in (100, 300, 600):
        ax.scatter([], [], s=n / 4, color=COLOUR["tram"], alpha=0.55, label=f"{n} AC departures")
    ax.legend(frameon=False, loc="lower left", fontsize=7)
    ax.set_aspect("equal")
    ax.set_xticks([]), ax.set_yticks([])
    ax.grid(False)
    ax.spines[["left", "bottom"]].set_visible(False)
    ax.set_title("Recorded AC-boardable departures per stop pole, 06–22", loc="left", color=INK)
    fig.tight_layout()
    fig.savefig(rd / "stops_ac_departures.png")
    plt.close(fig)

    # --- one run: raw reports vs derived events ----------------------------------------------------
    cand = runs[(runs["mode"] == "tram") & (runs.n_events >= 8) & (runs.n_interpolated >= 1)]
    cand = cand[cand.run_id.isin(ev[ev.stop_name == "Centrum"].run_id)]
    if len(cand):
        r = cand.iloc[len(cand) // 2]
        e = ev[ev.run_id == r.run_id].sort_values("stop_sequence")
        p = pings[(pings.vehicle_number == r.vehicle_number) & (pings["mode"] == r["mode"])
                  & (pings.reported_at >= r.first_report) & (pings.reported_at <= r.last_report)]
        fig, ax = plt.subplots(figsize=(6.5, 6.5))
        ax.plot(p.lon * lon_scale, p.lat, ".", color=GREY, markersize=4, label="position reports (≈10 s)")
        for m, mk in (("observed", "o"), ("interpolated", "D")):
            x = e[e.arrival_method == m]
            ax.plot(x.stop_lon * lon_scale, x.stop_lat, mk, color=COLOUR["tram"], markersize=6,
                    markerfacecolor="#fcfcfb" if m == "interpolated" else COLOUR["tram"], label=f"stop, {m}")
        for s in e.itertuples():
            ax.annotate(f"{s.stop_name} {s.departure.tz_convert(TZ):%H:%M:%S} ({s.delay_s:+d} s)",
                        (s.stop_lon * lon_scale, s.stop_lat), fontsize=6.5, color=INK,
                        xytext=(6, -2), textcoords="offset points")
        ax.set_aspect("equal")
        ax.set_xticks([]), ax.set_yticks([])
        ax.grid(False)
        ax.spines[["left", "bottom"]].set_visible(False)
        ax.legend(frameon=False, fontsize=7, loc="best")
        ax.set_title(f"Run {r.run_id}: line {r.line}, vehicle {r.vehicle_number}, AC={r.ac}",
                     loc="left", color=INK, fontsize=8)
        fig.tight_layout()
        fig.savefig(rd / "run_example.png")
        plt.close(fig)

    # --- feed gaps: minutes with < 60% of the usual number of fresh reports -------------------------
    per_min = pings.groupby(pings.reported_at.dt.floor("min")).size()
    per_min = per_min[(per_min.index >= w0) & (per_min.index < w1 + pd.Timedelta(minutes=30))]
    low = per_min < 0.6 * per_min.median()
    run_id = (low != low.shift()).cumsum()
    feed_gaps = [(g.index[0].tz_convert(TZ), g.index[-1].tz_convert(TZ), 100 * g.mean() / per_min.median())
                 for _, g in per_min[low].groupby(run_id[low]) if len(g) >= 3]

    # --- quality -----------------------------------------------------------------------------------
    meth = ev.groupby("mode").arrival_method.value_counts(normalize=True).mul(100).round(1).unstack()
    gap = ev[ev.arrival_method == "interpolated"].interp_gap_s.describe(percentiles=[.5, .9]).round(0)
    partial = (runs.share_stops_matched < 0.8).sum()
    dwell = (ev.departure - ev.arrival).dt.total_seconds()
    seq = ev.sort_values(["run_id", "stop_sequence"])
    nonmono = int((seq.groupby("run_id").arrival.diff().dt.total_seconds() < 0).sum()
                  + (dwell < 0).sum())
    status = groups.groupby("status").n_reports.sum()
    aoi_lines = set(zip(sch["mode"], sch.line))
    nb = groups[groups.status == "no_aoi_block"]
    nb_extra = nb[[(m, l) in aoi_lines for m, l in zip(nb["mode"], nb.line)]]
    nb_extra_tab = (nb_extra.groupby(["mode", "line", "brigade"]).n_reports.sum().reset_index()
                    .sort_values("n_reports", ascending=False).head(12)
                    .replace({"brigade": {"": "(blank)"}}))
    matched = groups[groups.status == "matched"]

    lines = []
    w = lines.append
    w("# T04 report: recorded tram and bus stop events with AC\n")
    w(f"Service day **{diag['service_date']}** (Saturday), window **{w0.tz_convert(TZ):%H:%M}–"
      f"{w1.tz_convert(TZ):%H:%M} local**, Śródmieście (TERYT 146510_8, stops within "
      f"{diag['params']['STOP_BUFFER_M']} m of the boundary included). Generated by "
      "`python -m transit.report`; method in [`transit/README.md`](../../transit/README.md).\n")
    w("## What was produced\n")
    w("| Output | Where | Rows |\n|---|---|---|")
    w(f"| Stop events | BigQuery `hackaton-honory.warsaw_heat.stop_events` (partition `service_date`), "
      f"`gs://hackaton-honory-heat/T04/{diag['service_date']}/stop_events.parquet` | {len(ev):,} |")
    w(f"| Runs | `hackaton-honory.warsaw_heat.runs`, `gs://hackaton-honory-heat/T04/{diag['service_date']}/runs.parquet` "
      f"| {len(runs):,} |")
    w("| Per-line timetable deviation | [`line_delays.csv`](line_delays.csv) | "
      f"{len(dl)} |")
    w("| Code | `transit/` (`stop_events.py`, `report.py`, `sql/`) | |\n")
    w("Router use (T09): board only rows with `ac_boardable = TRUE`; `departure` is the recorded time the "
      "vehicle left the stop pole, `arrival` the recorded time it got there. Join `stop_id` to T03's stop "
      "waiting areas.\n")

    w("## Coverage against the scheduled Saturday service\n")
    w("Scheduled = GTFS (mkuran.pl feed of 2026-10-16) tram/bus trips with at least one Śródmieście stop "
      "departure in the window. Recorded = a stop event exists for that trip and stop.\n")
    w(md_table(cov.rename(columns={"calls_share_on_operated": "calls share on operated trips"})))
    w("\n![coverage per hour](coverage_hourly.png)\n")
    w("Share of scheduled stop calls recorded, by hour (the 21:00 dip is the feed outage listed under "
      "Known gaps):\n")
    w(md_table(hourly_tab, "{:.1f}"))
    w("\nRecorded departures at main stops (all poles, both modes) per local hour — every hour 06–21 is "
      "covered (Muzeum Narodowe is the stop at rondo de Gaulle'a):\n")
    w(md_table(main_tab))
    w("")

    w("## Air conditioning\n")
    w("AC comes from the fleet register (`ztm_vehicles` → `ztm_vehicle_models.air_conditioning`, snapshot "
      "closest to the service day) of the vehicle that actually drove the run, joined on "
      "`(mode, vehicle_number)`. Unknown AC → `ac_boardable = FALSE`.\n")
    w(md_table(ac_runs, "{:.1f}"))
    w(f"\nShare of recorded departures that are AC-boardable: tram {ev_ac.get('tram', 0)}%, bus "
      f"{ev_ac.get('bus', 0)}%.\n")
    w("Lines with fewer than 95% AC runs:\n")
    w(md_table(line_ac))
    w("\nVehicle types behind the non-AC runs:\n")
    w(md_table(non_ac))
    if len(unknown_tab):
        w("\nRuns with unknown AC (treated as not boardable):\n")
        w(md_table(unknown_tab))
    w("\n![AC departures per stop](stops_ac_departures.png)\n")

    w("## Deviation from the timetable\n")
    w("Delay = recorded departure − scheduled departure, at Śródmieście stops. Negative = early.\n")
    w(md_table(overall.rename(columns={"count": "events"})))
    w(f"\nDepartures more than 60 s early: tram {early.get('tram', 0)}%, bus {early.get('bus', 0)}%. "
      "Because the router uses recorded departures, the timetable deviation matters only as a guide for "
      "the walk-timing safety margin: per-line p05/p10 (early side) and p90/p95 (late side) are in "
      "[`line_delays.csv`](line_delays.csv).\n")
    w("![delay per line](line_delays.png)\n")

    w("## Matching quality\n")
    w(f"- Position reports used: {diag['reports']:,} fresh (report age ≤ "
      f"{diag['params']['MAX_REPORT_AGE_S']} s), de-duplicated across the two APIs and repeated polls, within "
      f"{diag['params']['PING_BUFFER_M']} m of the district. Vehicles report about every 10 s.")
    w(f"- {status.get('matched', 0):,} reports belong to a (line, brigade) block that serves Śródmieście; "
      f"{matched.n_assigned.sum():,} ({pct(matched.n_assigned.sum(), matched.n_reports.sum())}) of them "
      "were assigned to a trip of that block.")
    w(f"- Event time source: tram {meth.loc['tram', 'observed']}% observed / "
      f"{meth.loc['tram', 'interpolated']}% interpolated; bus {meth.loc['bus', 'observed']}% / "
      f"{meth.loc['bus', 'interpolated']}%. Interpolation gap median {gap['50%']:.0f} s, p90 {gap['90%']:.0f} s "
      f"(max allowed {diag['params']['MAX_GAP_S']} s).")
    w(f"- Dwell (departure − arrival): median {dwell.median():.0f} s, p90 {dwell.quantile(.9):.0f} s. "
      "Long dwells are layovers at terminal stops inside the district.")
    w(f"- Runs with < 80% of their Śródmieście stops matched: {partial} of {len(runs):,} "
      f"({pct(partial, len(runs))}).")
    w(f"- Time-monotonicity violations in the output: {nonmono} (arrival ≥ previous departure and "
      "departure ≥ arrival are enforced).")
    w(f"- Trips claimed by two vehicles (best kept): {(runs.vehicles_on_trip > 1).sum()}.\n")

    w("## Known gaps\n")
    w(f"- **Trips not operated / not recorded:** {int((~trips.operated).sum())} scheduled trips have no run. "
      "The one checked by hand is a real gap (tram 10 brigade 8: its vehicle stops reporting near the "
      "district after 10:47); the others were not checked one by one. Lines with most missing trips:\n")
    w(md_table(unop.head(15)))
    w("\n- **Operations outside the timetable blocks** are not in the output: reports on lines that serve the "
      "district "
      "under a brigade whose Saturday GTFS block has no Śródmieście trip (extra or replacement brigades, "
      "blank brigade, or blocks that only run near the district):\n")
    w(md_table(nb_extra_tab) if len(nb_extra_tab) else "  none")
    if feed_gaps:
        w("\n- **Feed outages** (fresh reports below 60% of the usual rate for ≥ 3 min; the collector kept "
          "polling, the city API served stale positions). Stops passed during them are interpolated if the "
          f"gap is ≤ {diag['params']['MAX_GAP_S']} s, otherwise missing:")
        for a_, b_, share in feed_gaps:
            w(f"  - {a_:%H:%M}–{b_:%H:%M} local, {share:.0f}% of the usual report rate")
    w("\n- **Runs more than 60 min late** (or more than 30 min early) cannot be matched to their trip.")
    w("- **Arrival vs departure resolution** is limited by the 10 s report interval: a single report at a "
      "stop gives arrival = departure. Interpolated stops always have arrival = departure.")
    w("- **Shapes passing the same point twice** (terminal loops) project reports to the nearest pass; "
      "the monotonic filter drops the inconsistent ones.")
    w("- **Metro and rail are out of scope**; stops are GTFS poles (`stop_id`), not stop groups.")
    w("- **Vehicle register gaps:** see the unknown-AC table above.\n")

    w("## Reproduce\n")
    w("```bash\npip install -r transit/requirements.txt\n"
      f"python -m transit.stop_events --date {diag['service_date']} --start {w0.tz_convert(TZ):%H:%M} "
      f"--end {w1.tz_convert(TZ):%H:%M} --out t04_out/{diag['service_date']}\n"
      f"python -m transit.report t04_out/{diag['service_date']}\n```\n")
    w(f"Runtime {diag['runtime_s']} s on a laptop (BigQuery does the heavy filtering). Parameters: "
      + ", ".join(f"`{k}={v}`" for k, v in diag["params"].items()) + ".")
    (rd / "README.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {rd}")


if __name__ == "__main__":
    main()
