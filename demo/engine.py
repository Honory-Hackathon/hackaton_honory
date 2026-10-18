"""Walk-only pricing and routing on a segment heat table (T08 columns). Fallback for when the T09 router is
not importable, and the like-for-like pricer for foreign lines (ORS) in either case.

- HeatField: utci/g/shade per segment and step, linearly interpolated in time.
- WalkNet: T03 network (component 0), igraph, EPSG:2180; snapping of foreign lines to segments.
- price_line(): sample a line every SAMPLE_M metres, snap each sample to the nearest segment, walk it at
  SPEED m/s from `depart`, add a signal wait at every signalised crossing passed, integrate g(U(t)) dt.
- route(): fastest (length) or coolest (dose, entry time estimated along the search) walk-only path.
"""
import io
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from demo import common

SPEED = 1.0          # m/s, persona (spec §2)
SIGNAL_WAIT_S = 30   # mean wait at a signalised crossing (half of a ~60 s pedestrian red); our choice
SAMPLE_M = 5.0
SNAP_MAX_M = 30.0
CACHE = Path(__file__).resolve().parent / ".cache"


def _gcs_parquet_cached(path):
    CACHE.mkdir(exist_ok=True)
    local = CACHE / path.replace("/", "_")
    if not local.exists():
        from google.cloud import storage
        storage.Client(project="hackaton-honory").bucket(common.BUCKET).blob(path).download_to_filename(local)
    return local


def hhmm_to_s(h):
    parts = list(map(int, h.split(":")))
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)


def s_to_hhmm(s, sec=False):
    s = int(round(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}" + (f":{s % 60:02d}" if sec else "")


class HeatField:
    def __init__(self, seg_heat: pd.DataFrame, seg_ids: np.ndarray, stop_heat: pd.DataFrame = None):
        idx = pd.Index(seg_ids)
        self.U = np.full((len(seg_ids), common.N_STEPS), np.nan, np.float32)
        self.SH = np.zeros((len(seg_ids), common.N_STEPS), np.float32)
        pos = idx.get_indexer(seg_heat.segment_id)
        ok = pos >= 0
        st = seg_heat.step.to_numpy().astype(int)
        self.U[pos[ok], st[ok]] = seg_heat.utci_mean.to_numpy()[ok]
        self.SH[pos[ok], st[ok]] = seg_heat.shade_share.fillna(0).to_numpy()[ok]
        # segments missing from the table: comfortable (indoor rule)
        self.U[np.isnan(self.U)] = 26.0
        self.stop = None
        if stop_heat is not None:
            self.stop = stop_heat.pivot_table(index="stop_id", columns="step", values="utci")

    @staticmethod
    def _w(t_s):
        x = np.clip((np.asarray(t_s, float) - 6 * 3600) / 1800.0, 0, common.N_STEPS - 1)
        i0 = np.minimum(np.floor(x).astype(int), common.N_STEPS - 2)
        return i0, x - i0

    def utci(self, seg_idx, t_s):
        i0, f = self._w(t_s)
        return self.U[seg_idx, i0] * (1 - f) + self.U[seg_idx, i0 + 1] * f

    def shade(self, seg_idx, t_s):
        i0, f = self._w(t_s)
        return self.SH[seg_idx, i0] * (1 - f) + self.SH[seg_idx, i0 + 1] * f

    def stop_utci(self, stop_id, t_s):
        row = self.stop.loc[str(stop_id)].to_numpy()
        i0, f = self._w(t_s)
        return row[i0] * (1 - f) + row[i0 + 1] * f


@lru_cache(maxsize=1)
def walknet():
    return WalkNet()


class WalkNet:
    def __init__(self):
        import geopandas as gpd
        import igraph as ig
        from pyproj import Transformer
        from shapely import STRtree
        seg = gpd.read_parquet(_gcs_parquet_cached("T03/network/segments.parquet"))
        seg = seg[seg.component == 0].reset_index(drop=True)
        self.seg = seg
        self.seg_ids = seg.segment_id.to_numpy()
        self.length = seg.length_m.to_numpy(float)
        self.signal = seg.crossing_signals.fillna(False).to_numpy(bool)
        self.geom = seg.geometry.to_numpy()
        nodes = pd.Index(pd.unique(np.concatenate([seg.u.to_numpy(), seg.v.to_numpy()])))
        self.nodes = nodes
        self.g = ig.Graph(n=len(nodes), edges=list(zip(nodes.get_indexer(seg.u), nodes.get_indexer(seg.v))))
        self.tree = STRtree(self.geom)
        self.to2180 = Transformer.from_crs(4326, 2180, always_xy=True)
        self.to4326 = Transformer.from_crs(2180, 4326, always_xy=True)
        nd = gpd.read_parquet(_gcs_parquet_cached("T03/network/nodes.parquet")).set_index("node_id")
        nd = nd.reindex(nodes)
        self.node_xy = np.c_[nd.geometry.x.to_numpy(), nd.geometry.y.to_numpy()]

    def nearest_node(self, lonlat):
        x, y = self.to2180.transform(*lonlat)
        d = np.hypot(self.node_xy[:, 0] - x, self.node_xy[:, 1] - y)
        return int(np.nanargmin(d))

    # ---- pricing of any line ----
    def sample_line(self, coords_lonlat):
        from shapely import LineString, points
        xs, ys = self.to2180.transform(*np.asarray(coords_lonlat, float).T)
        line = LineString(np.c_[xs, ys])
        n = max(2, int(np.ceil(line.length / SAMPLE_M)) + 1)
        dist = np.linspace(0, line.length, n)
        mids = (dist[:-1] + dist[1:]) / 2
        pts = line.interpolate(mids)
        idx, dd = self.tree.query_nearest(pts, return_distance=True, all_matches=False)
        seg_idx = np.full(len(pts), -1)
        seg_idx[idx[0]] = idx[1]
        dsnap = np.full(len(pts), np.inf)
        dsnap[idx[0]] = dd
        seg_idx[dsnap > SNAP_MAX_M] = -1
        return line, np.diff(dist), seg_idx, dsnap

    def price_samples(self, field, pieces_m, seg_idx, depart_s, weights):
        """Walk pieces in order; returns per-piece arrays (t_start, dt, U, shade) incl. signal waits."""
        t = depart_s
        ts, dts, us, sh = [], [], [], []
        seen_signal = set()
        for L, s in zip(pieces_m, seg_idx):
            if s >= 0 and self.signal[s] and s not in seen_signal:
                seen_signal.add(s)
                ts.append(t); dts.append(SIGNAL_WAIT_S)
                us.append(field.utci(s, t)); sh.append(field.shade(s, t))
                t += SIGNAL_WAIT_S
            dt = L / SPEED
            u = field.utci(s, t) if s >= 0 else np.nan
            ts.append(t); dts.append(dt); us.append(u); sh.append(field.shade(s, t) if s >= 0 else 0.0)
            t += dt
        return np.array(ts), np.array(dts), np.array(us, float), np.array(sh, float)

    # ---- routing ----
    def route(self, field, o_node, d_node, depart_s, mode, weights):
        """mode 'fastest' (length + signal waits) or 'coolest' (time-dependent dose label-setting)."""
        import heapq
        if mode == "fastest":
            w = self.length / SPEED + self.signal * SIGNAL_WAIT_S
            epath = self.g.get_shortest_paths(o_node, d_node, weights=w, output="epath")[0]
            return epath
        adj = self.g.get_inclist()
        el = np.array(self.g.get_edgelist())
        best = {o_node: 0.0}
        tarr = {o_node: depart_s}
        prev = {}
        pq = [(0.0, o_node)]
        tt = self.length / SPEED + self.signal * SIGNAL_WAIT_S
        while pq:
            c, v = heapq.heappop(pq)
            if c > best.get(v, np.inf):
                continue
            if v == d_node:
                break
            t = tarr[v]
            for e in adj[v]:
                u = el[e, 1] if el[e, 0] == v else el[e, 0]
                g = float(common.g_of_u(field.utci(e, t), weights))
                nc = c + g * tt[e] + 1e-3 * tt[e]  # tiny time tie-break
                if nc < best.get(u, np.inf):
                    best[u] = nc
                    tarr[u] = t + tt[e]
                    prev[u] = (v, e)
                    heapq.heappush(pq, (nc, u))
        path, v = [], d_node
        while v != o_node:
            v, e = prev[v]
            path.append(e)
        return path[::-1]

    def epath_coords(self, epath, o_node):
        """Ordered lon/lat coordinates of an edge path starting at o_node."""
        from shapely import get_coordinates
        el = self.g.get_edgelist()
        cur = o_node
        xy = []
        for e in epath:
            c = get_coordinates(self.geom[e])
            a, b = el[e]
            if a != cur:  # traverse reversed
                c = c[::-1]
                cur = a
            else:
                cur = b
            # synthetic sides: geometry ends need not touch the nodes; keep order by nearest end
            if xy and np.hypot(*(np.array(xy[-1]) - c[0])) > np.hypot(*(np.array(xy[-1]) - c[-1])):
                c = c[::-1]
            xy.extend(map(tuple, c))
        xy = np.array(xy)
        lon, lat = self.to4326.transform(xy[:, 0], xy[:, 1])
        return np.c_[lon, lat].tolist()


def summarize(ts, dts, us, sh, weights, depart_s):
    """Option metrics from a sampled exposure series (walk/wait pieces only; rides add time, no dose)."""
    ok = ~np.isnan(us)
    g = common.g_of_u(np.where(ok, us, 26.0), weights)
    dose = float((g * dts).sum())
    total = float(ts[-1] + dts[-1] - depart_s) if len(ts) else 0.0
    cats = np.digitize(np.where(ok, us, 0.0), [26, 32, 38, 46])
    mins = {c: float(dts[(cats == i) & ok].sum() / 60) for i, c in enumerate(common.CATEGORIES)}
    return {"dose_Ks": round(dose, 1), "duration_min": round(total / 60, 1),
            "peak_utci": round(float(np.nanmax(us)), 1) if ok.any() else None,
            "minutes_per_category": {k: round(v, 1) for k, v in mins.items()},
            "shade_share": round(float(np.nansum((sh * dts)[ok]) / max(dts[ok & ~np.isnan(sh)].sum(), 1e-9)), 3),
            "unsnapped_share": round(float(dts[~ok].sum() / max(dts.sum(), 1e-9)), 3)}
