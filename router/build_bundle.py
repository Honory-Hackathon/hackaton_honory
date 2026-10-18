"""Build the compact router bundle from T03 (walk network, stops), T04 (stop events) and T05 (forcing).

    python -m router.build_bundle --t03 DIR_OR_GS --t04 DIR_OR_GS --out OUT_DIR [--upload gs://.../T09/bundle/]

Light work (a few seconds): reads the GeoParquet files, keeps the main network component,
reprojects geometries to EPSG:4326 and writes plain Parquet/NPZ that the router loads without geopandas.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

LOCAL_UTC_OFFSET_S = 2 * 3600  # CEST on both 2026-10-17 and 2026-06-28


def _read(src: str, name: str, tmp: Path) -> Path:
    if src.startswith("gs://"):
        dst = tmp / name
        if not dst.exists():
            subprocess.run(["gsutil", "-q", "cp", f"{src.rstrip('/')}/{name}", str(dst)], check=True)
        return dst
    return Path(src) / name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--t03-network", default="gs://hackaton-honory-heat/T03/network")
    ap.add_argument("--t03-stops", default="gs://hackaton-honory-heat/T03/stops")
    ap.add_argument("--t04", default="gs://hackaton-honory-heat/T04/2026-10-17")
    ap.add_argument("--line-delays", default=str(Path(__file__).resolve().parents[1] / "reports/T04/line_delays.csv"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--upload", default=None)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_src"
    tmp.mkdir(exist_ok=True)

    seg = gpd.read_parquet(_read(a.t03_network, "segments.parquet", tmp))
    nodes = gpd.read_parquet(_read(a.t03_network, "nodes.parquet", tmp))
    stops = gpd.read_parquet(_read(a.t03_stops, "stop_areas.parquet", tmp))
    ev = pd.read_parquet(_read(a.t04, "stop_events.parquet", tmp))

    nodes = nodes[nodes.component == 0].reset_index(drop=True)
    nodes4326 = nodes.to_crs(4326)
    nd = pd.DataFrame({
        "node_id": nodes.node_id.values,
        "x": nodes.geometry.x.values, "y": nodes.geometry.y.values,
        "lon": nodes4326.geometry.x.values, "lat": nodes4326.geometry.y.values,
    })
    idx = pd.Series(np.arange(len(nd)), index=nd.node_id)

    seg = seg[seg.component == 0].reset_index(drop=True)
    seg = seg[seg.u.isin(idx.index) & seg.v.isin(idx.index)].reset_index(drop=True)
    g4326 = seg.geometry.to_crs(4326)
    coords, offs, rev = [], [0], []
    ux, uy = nd.x.values[idx[seg.u].values], nd.y.values[idx[seg.u].values]
    for i, (gm, gm84) in enumerate(zip(seg.geometry.values, g4326.values)):
        c = shapely.get_coordinates(gm84)
        c0 = shapely.get_coordinates(gm)
        # orient geometry u -> v
        d_first = np.hypot(c0[0, 0] - ux[i], c0[0, 1] - uy[i])
        d_last = np.hypot(c0[-1, 0] - ux[i], c0[-1, 1] - uy[i])
        if d_last < d_first:
            c = c[::-1]
        coords.append(c)
        offs.append(offs[-1] + len(c))
    coords = np.concatenate(coords).astype(np.float64)
    ed = pd.DataFrame({
        "segment_id": seg.segment_id.values,
        "u": idx[seg.u].values.astype(np.int32), "v": idx[seg.v].values.astype(np.int32),
        "length_m": seg.length_m.values.astype(np.float64),
        "kind": seg.kind.values.astype(str), "highway": seg.highway.astype(str).values,
        "name": seg["name"].astype(object).where(seg["name"].notna(), None).values,
        "crossing": seg.crossing.values, "crossing_signals": seg.crossing_signals.values,
        "steps": seg.steps.values, "covered": seg.covered.values, "indoor": seg.indoor.values,
        "in_aoi": seg.in_aoi.values, "mask_category": seg.mask_category.astype(object).values,
    })
    ed.to_parquet(out / "edges.parquet")
    nd.to_parquet(out / "nodes.parquet")
    np.savez_compressed(out / "edge_geom.npz", coords=coords, offsets=np.asarray(offs, dtype=np.int64))

    st4326 = stops.geometry.centroid.to_crs(4326)
    stc = stops.geometry.centroid
    sd = pd.DataFrame({
        "stop_id": stops.stop_id.astype(str).values, "stop_name": stops.stop_name.values,
        "mode": stops["mode"].values, "in_aoi": stops.in_aoi.values.astype(bool),
        "shelter": stops.shelter.astype(object).values,
        "lon": st4326.x.values, "lat": st4326.y.values, "x": stc.x.values, "y": stc.y.values,
        "access_node_id": stops.access_node_id.values, "access_dist_m": stops.access_dist_m.values,
    })
    sd["access_node"] = sd.access_node_id.map(idx).astype("Int64")
    sd = sd[sd.access_node.notna()].reset_index(drop=True)
    sd["access_node"] = sd.access_node.astype(np.int32)
    sd.to_parquet(out / "stops.parquet")

    def tod(s):
        t = s.dt.tz_convert("UTC")
        sec = t.dt.hour * 3600 + t.dt.minute * 60 + t.dt.second
        return (sec + LOCAL_UTC_OFFSET_S).astype(np.int32)

    e = pd.DataFrame({
        "run_id": ev.run_id.values, "trip_id": ev.trip_id.values, "mode": ev["mode"].values,
        "line": ev.line.astype(str).values, "headsign": ev.trip_headsign.values,
        "ac": ev.ac.astype("boolean").values, "ac_boardable": ev.ac_boardable.astype(bool).values,
        "seq": ev.stop_sequence.values.astype(np.int32), "stop_id": ev.stop_id.astype(str).values,
        "stop_name": ev.stop_name.values, "stop_lon": ev.stop_lon.values, "stop_lat": ev.stop_lat.values,
        "in_aoi_strict": ev.in_aoi_strict.fillna(False).astype(bool).values,
        "arr_s": tod(ev.arrival).values, "dep_s": tod(ev.departure).values,
        "departure_method": ev.departure_method.values,
    })
    e = e.sort_values(["run_id", "seq"]).reset_index(drop=True)
    e.to_parquet(out / "events.parquet")

    ld = pd.read_csv(a.line_delays)
    ld["line"] = ld.line.astype(str)
    ld.to_csv(out / "line_delays.csv", index=False)

    try:
        from google.cloud import bigquery
        f = bigquery.Client(project="hackaton-honory").query(
            "select step, ta_c, ghi_wm2, sun_elev_deg from `hackaton-honory.warsaw_heat.t05_forcing_20260628` order by step"
        ).to_dataframe()
        f.to_csv(out / "forcing.csv", index=False)
    except Exception as exc:  # pragma: no cover
        print("forcing not fetched:", exc)

    meta = {"nodes": len(nd), "edges": len(ed), "stops": len(sd), "events": len(e),
            "sources": {"t03": a.t03_network, "t04": a.t04}}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(meta)
    if a.upload:
        files = [str(out / f) for f in ["edges.parquet", "nodes.parquet", "edge_geom.npz", "stops.parquet",
                                         "events.parquet", "line_delays.csv", "forcing.csv", "meta.json"]]
        subprocess.run(["gsutil", "-q", "-m", "cp", *files, a.upload.rstrip("/") + "/"], check=True)


if __name__ == "__main__":
    main()
