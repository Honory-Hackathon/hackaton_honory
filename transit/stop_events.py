"""T04: recorded tram and bus operations -> stop events with an AC flag per run.

usage: python -m transit.stop_events --date 2026-10-17 [--start 06:00] [--end 22:00]
                                     [--out DIR] [--cache] [--no-upload]

Method (details in transit/README.md):
  1. Scheduled stop calls of every tram/bus block (line + brigade) with a trip calling at an AOI stop
     inside the window; trip shapes densified in EPSG:2180.
  2. Fresh, de-duplicated position reports near the AOI.
  3. Per vehicle and block label, a Viterbi pass assigns each report to one trip of the block (trips
     only advance in time) or to none, scoring distance to the trip's shape, heading against the
     shape, and lateness against the timetable at the projected position.
  4. Per run (trip + vehicle), each AOI stop gets arrival/departure from the reports within a few
     metres of it ("observed"), else by interpolating along the shape between the bracketing
     reports ("interpolated").
  5. AC from the fleet register of the actual vehicle; unknown vehicles are not boardable.

Outputs in OUT (default t04_out/DATE): stop_events.parquet, runs.parquet, scheduled.parquet,
report_groups.parquet, diagnostics.json; uploaded to BigQuery warsaw_heat.{stop_events,runs}
(partition = service_date) and gs://hackaton-honory-heat/T04/DATE/.
"""

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyproj
import shapely
import shapely.geometry
from google.cloud import bigquery, storage
from scipy.spatial import cKDTree

PROJECT = "hackaton-honory"
DATASET = "warsaw_heat"
BUCKET = "hackaton-honory-heat"
AOI_BLOB = "T01/meta/aoi_halo_grid.geojson"
SQL = Path(__file__).parent / "sql"
TZ = ZoneInfo("Europe/Warsaw")

STOP_BUFFER_M = 30  # stops this close to the district boundary count as AOI stops
PING_BUFFER_M = 1500  # reports kept within this distance of the AOI
MAX_REPORT_AGE_S = 120
SHAPE_STEP_M = 5
MAX_SHAPE_DIST_M = 60  # a report farther than this from a trip's shape cannot belong to it
TRIP_PRE_S, TRIP_POST_S = 30 * 60, 60 * 60  # report-time window around a trip's scheduled span
NULL_COST = 3.0  # per report left unassigned
SWITCH_COST, SKIP_COST = 1.0, 0.5  # moving on to a later trip / per trip skipped on the way
HEADING_COST = 4.0  # for driving against the shape direction
MIN_MOVE_M = 15  # displacement needed for a heading
FROZEN_SPREAD_M = 50
STOP_RADIUS_M = 30  # report this close (along the shape) to a stop = observed at the stop
MAX_SHAPE_OFFSET_OBS_M = 40
MAX_GAP_S = 300  # longest report gap bridged by interpolation
BACKTRACK_M = 40  # reports this far behind the run's progress are dropped as GPS noise

to_2180 = pyproj.Transformer.from_crs(4326, 2180, always_xy=True)
to_4326 = pyproj.Transformer.from_crs(2180, 4326, always_xy=True)


def query(client, name, params):
    types = {bool: "BOOL", int: "INT64", float: "FLOAT64", str: "STRING", date: "DATE"}
    qp = []
    for k, v in params.items():
        if isinstance(v, list):
            qp.append(bigquery.ArrayQueryParameter(k, "STRING", v))
        elif isinstance(v, datetime):
            qp.append(bigquery.ScalarQueryParameter(k, "TIMESTAMP", v))
        else:
            qp.append(bigquery.ScalarQueryParameter(k, types[type(v)], v))
    sql = (SQL / f"{name}.sql").read_text()
    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=qp))
    return job.result().to_dataframe(create_bqstorage_client=True)


def epoch_s(ts):
    return ((ts - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy()


def load_aoi():
    blob = storage.Client(project=PROJECT).bucket(BUCKET).blob(AOI_BLOB)
    fc = json.loads(blob.download_as_text())
    feat = next(f for f in fc["features"] if f["properties"]["name"] == "aoi")
    aoi = shapely.geometry.shape(feat["geometry"])
    return shapely.transform(aoi, lambda c: np.c_[to_4326.transform(c[:, 0], c[:, 1])]).wkt


@dataclass
class Shape:
    xy: np.ndarray  # densified points, EPSG:2180
    s: np.ndarray  # metres along the shape
    tangent: np.ndarray  # unit direction per point
    tree: cKDTree
    gtfs_scale: float  # metres along the 2180 line per GTFS metre


def build_shapes(df):
    shapes = {}
    for sid, g in df.groupby("shape_id", sort=False):
        x, y = to_2180.transform(g.shape_pt_lon.to_numpy(), g.shape_pt_lat.to_numpy())
        line = shapely.segmentize(shapely.LineString(np.c_[x, y]), SHAPE_STEP_M)
        xy = shapely.get_coordinates(line)
        seg = np.hypot(*np.diff(xy, axis=0).T)
        s = np.r_[0, np.cumsum(seg)]
        i = np.arange(len(xy))
        d = xy[np.minimum(i + 2, len(xy) - 1)] - xy[np.maximum(i - 2, 0)]
        tangent = d / np.maximum(np.hypot(*d.T), 1e-9)[:, None]
        gtfs_len = g.dist_m.max()
        scale = s[-1] / gtfs_len if gtfs_len and gtfs_len > 0 else np.nan
        shapes[sid] = Shape(xy, s, tangent, cKDTree(xy), scale)
    return shapes


@dataclass
class Trip:
    trip_id: str
    shape: Shape
    t_first: float
    t_last: float
    stop_s: np.ndarray
    stop_t: np.ndarray  # scheduled departure, epoch s
    calls: pd.DataFrame  # AOI stop calls of the trip, in stop order (empty outside the window)


def build_trips(sched, shapes):
    blocks = {}
    for tid, g in sched.groupby("trip_id", sort=False):
        g = g.sort_values("stop_sequence")
        sh = shapes.get(g.shape_id.iat[0])
        if sh is None:
            continue
        if g.shape_dist_m.notna().all() and np.isfinite(sh.gtfs_scale):
            stop_s = g.shape_dist_m.to_numpy() * sh.gtfs_scale
        else:
            x, y = to_2180.transform(g.stop_lon.to_numpy(), g.stop_lat.to_numpy())
            stop_s = sh.s[sh.tree.query(np.c_[x, y])[1]]
        stop_s = np.maximum.accumulate(stop_s)
        stop_t = epoch_s(g.sched_departure)
        calls = g.assign(s_m=stop_s)
        calls = calls[calls.in_aoi & calls.serves_window]
        trip = Trip(tid, sh, stop_t.min(), stop_t.max(), stop_s, stop_t, calls)
        blocks.setdefault((g["mode"].iat[0], g.line.iat[0], g.brigade.iat[0]), []).append(trip)
    for trips in blocks.values():
        trips.sort(key=lambda tr: tr.t_first)
    return blocks


def heading(xy, t):
    """Unit motion direction per report from its neighbours; NaN while (nearly) standing."""
    n = len(xy)
    i = np.arange(n)
    a, b = np.maximum(i - 1, 0), np.minimum(i + 1, n - 1)
    d = xy[b] - xy[a]
    norm = np.hypot(*d.T)
    ok = (norm >= MIN_MOVE_M) & (t[b] - t[a] <= 90)
    out = np.full((n, 2), np.nan)
    out[ok] = d[ok] / norm[ok, None]
    return out


def assign(xy, t, trips):
    """Viterbi over reports: state = trip k (assigned), trip k (unassigned, last trip k), or start.

    Returns the trip index per report (-1 = unassigned) plus distance and position along the shape.
    """
    n, K = len(t), len(trips)
    cost = np.full((n, K), np.inf)
    s_m = np.full((n, K), np.nan)
    d_m = np.full((n, K), np.nan)
    hd = heading(xy, t)
    for k, tr in enumerate(trips):
        m = np.flatnonzero((t >= tr.t_first - TRIP_PRE_S) & (t <= tr.t_last + TRIP_POST_S))
        if not len(m):
            continue
        d, j = tr.shape.tree.query(xy[m], distance_upper_bound=MAX_SHAPE_DIST_M)
        ok = np.isfinite(d)
        m, d, j = m[ok], d[ok], j[ok]
        if not len(m):
            continue
        s = tr.shape.s[j]
        delay = t[m] - np.interp(s, tr.stop_s, tr.stop_t)
        c = (d / 25) ** 2 + np.where(delay >= 0, delay / 900, -delay / 450)
        cos = np.einsum("ij,ij->i", hd[m], tr.shape.tangent[j])
        c += np.where(np.isnan(cos), 0, HEADING_COST * (1 - cos) / 2)
        cost[m, k], s_m[m, k], d_m[m, k] = c, s, d

    S = 2 * K + 1  # [assigned 0..K-1, unassigned-after 0..K-1, start]
    k_of = np.r_[np.arange(K), np.arange(K)]
    T = np.full((S, S), np.inf)
    dk = k_of[None, :K] - k_of[:, None]  # (from, to-assigned)
    T[: 2 * K, :K] = np.where(dk == 0, 0, np.where(dk > 0, SWITCH_COST + SKIP_COST * (dk - 1), np.inf))
    T[: 2 * K, K : 2 * K] = np.where(k_of[:, None] == np.arange(K)[None, :], 0, np.inf)
    T[2 * K, :K] = 0
    T[2 * K, 2 * K] = 0

    node = np.c_[cost, np.full((n, K), NULL_COST), np.full(n, NULL_COST)]
    back = np.zeros((n, S), dtype=np.int32)
    dp = np.full(S, np.inf)
    dp[:K] = node[0, :K]
    dp[2 * K] = node[0, 2 * K]
    for i in range(1, n):
        tot = dp[:, None] + T
        back[i] = tot.argmin(0)
        dp = tot[back[i], np.arange(S)] + node[i]
    path = np.empty(n, dtype=np.int32)
    path[-1] = int(dp.argmin())
    for i in range(n - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    k = np.where(path < K, path, -1)
    rows = np.flatnonzero(k >= 0)
    s_out = np.full(n, np.nan)
    d_out = np.full(n, np.nan)
    s_out[rows], d_out[rows] = s_m[rows, k[rows]], d_m[rows, k[rows]]
    return k, s_out, d_out


def run_events(trip, t, s, d):
    """Arrival/departure per AOI stop of one run from its reports (t, s, d sorted by time)."""
    keep = s >= np.maximum.accumulate(s) - BACKTRACK_M
    t, s, d = t[keep], s[keep], d[keep]
    smono = np.maximum.accumulate(s)
    all_s = trip.stop_s
    out = []
    for c in trip.calls.itertuples():
        idx = np.searchsorted(all_s, c.s_m)
        gaps = [abs(c.s_m - all_s[j]) for j in (idx - 1, idx + 1) if 0 <= j < len(all_s)]
        gaps = [g for g in gaps if g > 0]
        r = min([STOP_RADIUS_M] + [g / 2 for g in gaps])
        near = (np.abs(s - c.s_m) <= r) & (d <= MAX_SHAPE_OFFSET_OBS_M)
        if near.any():
            arr, dep = t[near].min(), t[near].max()
            out.append((c, arr, dep, "observed", int(near.sum()), 0.0))
            continue
        before = np.flatnonzero(smono < c.s_m)
        after = np.flatnonzero(smono > c.s_m)
        if not (len(before) and len(after)):
            continue
        i0, i1 = before[-1], after[0]
        gap = t[i1] - t[i0]
        if gap > MAX_GAP_S:
            continue
        ti = t[i0] + gap * (c.s_m - smono[i0]) / (smono[i1] - smono[i0])
        out.append((c, ti, ti, "interpolated", 0, float(gap)))
    last = -np.inf
    rows = []
    for c, arr, dep, method, n_near, gap in out:
        arr = max(arr, last)
        dep = max(dep, arr)
        last = dep
        rows.append(dict(trip_id=trip.trip_id, stop_sequence=c.stop_sequence, stop_id=c.stop_id,
                         arrival=arr, departure=dep, method=method, n_reports_near=n_near,
                         interp_gap_s=gap))
    return rows, int(keep.sum())


def match(pings, blocks):
    """Returns events, runs and one row per (vehicle, line, brigade) report group with its status."""
    events, runs, groups = [], [], []
    keys = ["mode", "vehicle_number", "line", "brigade"]
    for key, g in pings.groupby(keys, sort=False):
        info = dict(zip(keys, key), n_reports=len(g), n_assigned=0,
                    first=g.reported_at.min(), last=g.reported_at.max())
        groups.append(info)
        trips = blocks.get((key[0], key[2], key[3]))
        if trips is None:
            info["status"] = "no_aoi_block"
            continue
        g = g.sort_values("reported_at")
        xy = g[["x", "y"]].to_numpy()
        t = epoch_s(g.reported_at)
        if t[-1] - t[0] > 900 and np.hypot(*(xy - np.median(xy, 0)).T).max() < FROZEN_SPREAD_M:
            info["status"] = "frozen"
            continue
        info["status"] = "matched"
        k, s, d = assign(xy, t, trips)
        info["n_assigned"] = int((k >= 0).sum())
        for kk in np.unique(k[k >= 0]):
            tr = trips[kk]
            m = k == kk
            if tr.calls.empty:
                continue
            rows, n_used = run_events(tr, t[m], s[m], d[m])
            run_id = f"{tr.trip_id}|{key[1]}"
            for r in rows:
                r["run_id"] = run_id
            events.extend(rows)
            runs.append(dict(run_id=run_id, trip_id=tr.trip_id, mode=key[0], vehicle_number=key[1],
                             n_reports=int(m.sum()), n_reports_used=n_used,
                             first_report=t[m].min(), last_report=t[m].max()))
    return pd.DataFrame(events), pd.DataFrame(runs), pd.DataFrame(groups)


def finalise(events, runs, sched, vehicles, service_date):
    ev = events.copy()
    for c in ("arrival", "departure"):
        ev[c] = pd.to_datetime(np.round(ev[c]), unit="s", utc=True)

    # Several vehicles on one trip (label errors, swaps): keep the run with most, then most observed, events.
    score = ev.groupby("run_id").agg(n=("stop_id", "size"),
                                     n_obs=("method", lambda m: (m == "observed").sum()))
    runs = runs.merge(score, left_on="run_id", right_index=True, how="inner")
    runs = runs.sort_values(["trip_id", "n", "n_obs"], ascending=[True, False, False])
    runs["vehicles_on_trip"] = runs.groupby("trip_id").run_id.transform("size")
    runs = runs.drop_duplicates("trip_id")
    ev = ev[ev.run_id.isin(runs.run_id)]

    trip_cols = ["trip_id", "mode", "line", "brigade", "direction_id", "trip_headsign", "variant_code"]
    calls = sched[sched.in_aoi & sched.serves_window]
    trip_info = calls.groupby(trip_cols, as_index=False).agg(
        n_aoi_stops=("stop_id", "size"), sched_first_aoi=("sched_departure", "min"),
        sched_last_aoi=("sched_departure", "max"))

    veh = vehicles.rename(columns={"air_conditioning": "ac"})
    runs = (runs.drop(columns="mode").merge(trip_info, on="trip_id", how="left")
            .merge(veh, on=["mode", "vehicle_number"], how="left"))
    runs["in_register"] = runs.model_id.notna()
    runs["ac_boardable"] = runs.ac.fillna(False).astype(bool)
    runs["n_observed"] = runs.n_obs
    runs["n_interpolated"] = runs.n - runs.n_obs
    runs["n_events"] = runs.n
    runs["share_stops_matched"] = runs.n_events / runs.n_aoi_stops
    for c in ("first_report", "last_report"):
        runs[c] = pd.to_datetime(np.round(runs[c]), unit="s", utc=True)

    stop_cols = ["trip_id", "stop_sequence", "stop_id", "stop_name", "stop_code", "stop_lat", "stop_lon",
                 "in_aoi_strict", "sched_arrival", "sched_departure", "pickup_type", "drop_off_type"]
    ev = ev.merge(calls[stop_cols], on=["trip_id", "stop_sequence", "stop_id"], how="left")
    ev = ev.merge(runs[["run_id", "mode", "line", "brigade", "direction_id", "trip_headsign",
                        "vehicle_number", "ac", "ac_boardable"]], on="run_id")
    ev["delay_s"] = (ev.departure - ev.sched_departure).dt.total_seconds().astype("int64")
    ev["arrival_method"] = ev["departure_method"] = ev.method
    ev = ev.drop(columns="method")
    ev.insert(0, "service_date", service_date)
    ev = ev.sort_values(["run_id", "stop_sequence"])
    ev_cols = ["service_date", "run_id", "trip_id", "mode", "line", "brigade", "direction_id",
               "trip_headsign", "vehicle_number", "ac", "ac_boardable", "stop_sequence", "stop_id",
               "stop_name", "stop_code", "stop_lat", "stop_lon", "in_aoi_strict", "sched_arrival",
               "sched_departure", "arrival", "departure", "arrival_method", "departure_method",
               "delay_s", "n_reports_near", "interp_gap_s", "pickup_type", "drop_off_type"]
    ev = ev[ev_cols].reset_index(drop=True)

    delays = ev.groupby("run_id").delay_s.median().rename("median_delay_s")
    runs = runs.merge(delays, left_on="run_id", right_index=True, how="left")
    first_last = ev.groupby("run_id").agg(first_event=("arrival", "min"), last_event=("departure", "max"))
    runs = runs.merge(first_last, left_on="run_id", right_index=True, how="left")
    runs.insert(0, "service_date", service_date)
    run_cols = ["service_date", "run_id", "trip_id", "mode", "line", "brigade", "direction_id",
                "trip_headsign", "variant_code", "vehicle_number", "in_register", "model_id", "brand",
                "model_type", "production_year", "depot", "ac", "ac_boardable", "sched_first_aoi",
                "sched_last_aoi", "first_event", "last_event", "n_aoi_stops", "n_events",
                "n_observed", "n_interpolated", "share_stops_matched", "median_delay_s",
                "n_reports", "n_reports_used", "first_report", "last_report", "vehicles_on_trip"]
    return ev, runs[run_cols].sort_values("run_id").reset_index(drop=True)


def upload(ev, runs, out, service_date):
    bq = bigquery.Client(project=PROJECT)
    for name, df, desc in [
        ("stop_events", ev, "T04: recorded tram/bus arrival+departure per run and Śródmieście stop, AC of "
                            "the actual vehicle. See hackaton_honory transit/README.md."),
        ("runs", runs, "T04: one row per operated tram/bus trip calling in Śródmieście: vehicle, AC, "
                       "match quality. See hackaton_honory transit/README.md."),
    ]:
        table_id = f"{PROJECT}.{DATASET}.{name}"
        cfg = bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            time_partitioning=bigquery.TimePartitioning(field="service_date"))
        try:
            bq.get_table(table_id)
            dest = f"{table_id}${service_date:%Y%m%d}"
        except Exception:
            dest = table_id
        bq.load_table_from_dataframe(df, dest, job_config=cfg).result()
        table = bq.get_table(table_id)
        table.description = desc
        bq.update_table(table, ["description"])
    bucket = storage.Client(project=PROJECT).bucket(BUCKET)
    for f in ("stop_events.parquet", "runs.parquet", "diagnostics.json"):
        bucket.blob(f"T04/{service_date}/{f}").upload_from_filename(out / f)
    bucket.blob("T04/README.md").upload_from_filename(Path(__file__).parent / "README.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, default=date(2026, 10, 17))
    ap.add_argument("--start", default="06:00")
    ap.add_argument("--end", default="22:00")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--cache", action="store_true", help="reuse BigQuery extracts already in OUT")
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()

    out = a.out or Path("t04_out") / str(a.date)
    out.mkdir(parents=True, exist_ok=True)
    w0 = datetime.combine(a.date, datetime.strptime(a.start, "%H:%M").time(), TZ)
    w1 = datetime.combine(a.date, datetime.strptime(a.end, "%H:%M").time(), TZ)
    tick = time.time()

    raw = {}
    if a.cache and all((out / f"raw_{n}.parquet").exists() for n in ("sched", "pings", "vehicles", "shapes")):
        raw = {n: pd.read_parquet(out / f"raw_{n}.parquet") for n in ("sched", "pings", "vehicles", "shapes")}
    else:
        client = bigquery.Client(project=PROJECT)
        aoi = load_aoi()
        p = dict(service_date=a.date, window_start=w0, window_end=w1, aoi_wkt=aoi)
        raw["sched"] = query(client, "scheduled_stops", p | dict(stop_buffer_m=float(STOP_BUFFER_M)))
        raw["pings"] = query(client, "pings", p | dict(ping_buffer_m=float(PING_BUFFER_M),
                                                       max_report_age_s=MAX_REPORT_AGE_S))
        raw["vehicles"] = query(client, "vehicles", dict(service_date=a.date))
        raw["shapes"] = query(client, "shapes", dict(service_date=a.date,
                                                     shape_ids=sorted(raw["sched"].shape_id.unique())))
        for n, df in raw.items():
            df.to_parquet(out / f"raw_{n}.parquet")
    print(f"extracts: " + ", ".join(f"{n} {len(df):,}" for n, df in raw.items()),
          f"({time.time() - tick:.0f} s)", flush=True)

    sched, pings = raw["sched"], raw["pings"]
    shapes = build_shapes(raw["shapes"])
    blocks = build_trips(sched, shapes)
    x, y = to_2180.transform(pings.lon.to_numpy(), pings.lat.to_numpy())
    pings = pings.assign(x=x, y=y)
    events, runs, groups = match(pings, blocks)
    print(f"matched: {len(runs):,} runs, {len(events):,} events ({time.time() - tick:.0f} s)", flush=True)

    ev, runs = finalise(events, runs, sched, raw["vehicles"], a.date)
    scheduled = sched[sched.in_aoi & sched.serves_window]
    ev.to_parquet(out / "stop_events.parquet")
    runs.to_parquet(out / "runs.parquet")
    scheduled.to_parquet(out / "scheduled.parquet")
    groups.to_parquet(out / "report_groups.parquet")
    diag = dict(
        service_date=str(a.date), window=[w0.isoformat(), w1.isoformat()],
        params={k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))},
        reports=len(pings),
        reports_by_status=groups.groupby("status").n_reports.sum().to_dict(),
        reports_assigned=int(groups.n_assigned.sum()),
        scheduled_trips=int(scheduled.trip_id.nunique()), runs=len(runs), stop_events=len(ev),
        runtime_s=round(time.time() - tick))
    (out / "diagnostics.json").write_text(json.dumps(diag, indent=1, default=str))
    print(json.dumps(diag, indent=1, default=str))
    if not a.no_upload:
        upload(ev, runs, out, a.date)
        print("uploaded")


if __name__ == "__main__":
    main()
