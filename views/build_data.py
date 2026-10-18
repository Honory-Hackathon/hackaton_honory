"""Build the route-heat data files for the heat-views pages (T13).

Reads walk segments (T03) and per-segment / per-stop heat (T08) from BigQuery
and writes compact files the static pages load:

  routes.bin        float32 vertices, uint32 path starts, uint8 UTCI and shade per step
  routes_meta.json  layout of routes.bin, heat source, step labels, summary per step
  stops.json        stops with per-step UTCI

Heat source (--heat):
  auto   final tables if gs://hackaton-honory-heat/T08/_SUCCESS exists, else stub
  final  warsaw_heat.t08_segment_heat / t08_stop_heat
  stub   warsaw_heat.t08_segment_heat_stub / t08_stop_heat_stub

Usage: python views/build_data.py --out views/site/data [--heat auto]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import numpy as np
from google.cloud import bigquery, storage

PROJECT = "hackaton-honory"
DS = f"{PROJECT}.warsaw_heat"
BUCKET = "hackaton-honory-heat"
N_STEPS = 33
UTCI_NULL = 255  # utci byte: value = 10 + b / 4  (10 .. 73.5 °C)


def choose_source(heat: str) -> str:
    if heat != "auto":
        return heat
    blob = storage.Client(project=PROJECT).bucket(BUCKET).blob("T08/_SUCCESS")
    return "final" if blob.exists() else "stub"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--heat", default="auto", choices=["auto", "final", "stub"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    src = choose_source(a.heat)
    suffix = "" if src == "final" else "_stub"
    seg_t, stop_t = f"{DS}.t08_segment_heat{suffix}", f"{DS}.t08_stop_heat{suffix}"
    bq = bigquery.Client(project=PROJECT)
    print("heat source:", src, seg_t)

    q = f"""
    WITH h AS (
      SELECT segment_id,
             ARRAY_AGG(utci_mean ORDER BY step) AS utci,
             ARRAY_AGG(shade_share ORDER BY step) AS shade,
             ARRAY_AGG(g ORDER BY step) AS g,
             COUNT(*) AS n
      FROM `{seg_t}` GROUP BY segment_id)
    SELECT s.segment_id, s.length_m, ST_ASGEOJSON(s.geometry) AS geom, h.utci, h.shade, h.g, h.n
    FROM `{DS}.t03_walk_segments` s JOIN h USING (segment_id)
    ORDER BY s.segment_id"""
    rows = list(bq.query(q).result(page_size=20000))
    print("segments:", len(rows))
    verts, starts, utci, shade, length = [], [], [], [], []
    nv = 0
    bad = 0
    for r in rows:
        if r["n"] != N_STEPS:
            bad += 1
            continue
        coords = json.loads(r["geom"])["coordinates"]
        starts.append(nv)
        for lon, lat in coords:
            verts.extend((lon, lat))
        nv += len(coords)
        utci.append([UTCI_NULL if v is None else int(round((min(max(v, 10.0), 73.5) - 10.0) * 4)) for v in r["utci"]])
        shade.append([0 if v is None else int(round(max(0.0, min(1.0, v)) * 250)) for v in r["shade"]])
        length.append(r["length_m"] or 0.0)
    n = len(starts)
    v32 = np.asarray(verts, dtype=np.float32)
    s32 = np.asarray(starts, dtype=np.uint32)
    u8 = np.asarray(utci, dtype=np.uint8).T.copy()   # step-major: [step, segment]
    sh8 = np.asarray(shade, dtype=np.uint8).T.copy()
    parts = [("vertices", v32), ("starts", s32), ("utci", u8), ("shade", sh8)]
    layout, off = {}, 0
    with open(os.path.join(a.out, "routes.bin"), "wb") as f:
        for name, arr in parts:
            pad = (-off) % 4
            f.write(b"\0" * pad)
            off += pad
            b = arr.tobytes()
            f.write(b)
            layout[name] = {"offset": off, "length": int(arr.size), "dtype": str(arr.dtype)}
            off += len(b)

    # length-weighted summary per step (outdoor and indoor alike)
    L = np.asarray(length)
    uv = u8.astype(float) / 4 + 10
    uv[u8 == UTCI_NULL] = np.nan
    cats = [26, 32, 38, 46]
    summary = []
    for k in range(N_STEPS):
        x = uv[k]
        ok = ~np.isnan(x)
        w = L[ok]
        bins = np.digitize(x[ok], cats)
        share = [float(w[bins == c].sum() / w.sum()) for c in range(5)]
        summary.append({"utci_lw_mean": round(float((x[ok] * w).sum() / w.sum()), 2),
                        "length_share_by_category": [round(s, 4) for s in share],
                        "shade_lw_mean": round(float((sh8[k][ok] / 250 * w).sum() / w.sum()), 3)})
    km = float(L.sum() / 1000)

    labels = [(dt.datetime(2026, 6, 28, 6, 0) + dt.timedelta(minutes=30 * k)).strftime("%H:%M") for k in range(N_STEPS)]
    meta = {
        "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "heat_source": src,
        "segment_table": seg_t,
        "stop_table": stop_t,
        "geometry_table": f"{DS}.t03_walk_segments",
        "n_segments": n, "n_vertices": int(nv), "skipped_incomplete": bad,
        "network_km": round(km, 1),
        "steps": N_STEPS, "labels_local": labels,
        "layout": layout,
        "utci_decode": "utci = 10 + byte / 4 (°C); 255 = no value",
        "shade_decode": "shade_share = byte / 250",
        "summary_by_step": summary,
        "categories": cats,
    }
    with open(os.path.join(a.out, "routes_meta.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False)

    q2 = f"""
    WITH h AS (SELECT stop_id, ARRAY_AGG(utci ORDER BY step) AS utci, COUNT(*) AS n
               FROM `{stop_t}` GROUP BY stop_id)
    SELECT a.stop_id, a.stop_name, a.mode, a.shelter,
           ST_X(ST_CENTROID(a.geometry)) AS lon, ST_Y(ST_CENTROID(a.geometry)) AS lat, h.utci
    FROM `{DS}.t03_stop_areas` a JOIN h USING (stop_id)"""
    stops = []
    for r in bq.query(q2).result():
        stops.append({"id": r["stop_id"], "name": r["stop_name"], "mode": r["mode"], "shelter": r["shelter"],
                      "lon": round(r["lon"], 6), "lat": round(r["lat"], 6),
                      "utci": [None if v is None else round(v, 1) for v in r["utci"]]})
    with open(os.path.join(a.out, "stops.json"), "w") as f:
        json.dump({"heat_source": src, "stops": stops}, f, ensure_ascii=False)
    print(f"wrote {n} segments, {nv} vertices, {len(stops)} stops, {off/1e6:.1f} MB bin; source={src}")


if __name__ == "__main__":
    main()
