"""Heat-aware router: walking + air-conditioned trams/buses, lowest heat dose, best departure time.

Method (see reports/T09/README.md):
* Every option minimises one scalar cost  C = dose [K s] + lam * duration [s]  over trips inside the
  time window; lam picks the option (coolest ~0, balanced 10 K, fastest 1000 K). Waiting at home is free.
* Walking: time-dependent costs are handled by one Dijkstra per 30-min step (edge weight
  t_e * (g_e(step) + lam)) from the origin and from the destination (network is undirected),
  values interpolated between steps. ~4 ms per Dijkstra.
* Transit: recorded stop events (T04), AC runs only, board/alight only in Śródmieście (in_aoi_strict).
  0 or 1 transfer. A run is scanned once with a running minimum over the boarding "potential"
  Head = access cost - lam * departure, so all (board, alight) pairs are covered in O(events).
* The chosen itinerary of each option is re-priced exactly: each segment at its entry time,
  g interpolated between steps; waiting at the stop's value; riding = UTCI 26 (dose 0).
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import heat as heatmod

log = logging.getLogger("router")

WALK_SPEED = 1.0          # m/s (persona)
STEPS_FACTOR = 2.0        # stairs take twice as long
SIGNAL_WAIT_S = 20.0      # mean wait at a signalised crossing segment
MARGIN_BASE_S = 60.0      # arrive at the stop this long before the recorded departure ...
MARGIN_MAX_S = 240.0      # ... plus the line's early-departure spread (-p05), capped
MARGIN_DEFAULT_S = 120.0  # lines without a delay record
MAX_TRANSFER_WALK_S = 400.0
MAX_TRANSFER_WAIT_S = 20 * 60
NEXT_DEPS_PER_TRANSFER = 12
WALK_DEPART_GRID_S = 300
LAMBDA = {"coolest": 0.3, "balanced": 10.0, "fastest": 1000.0}
CATS = ["<26", "26-32", "32-38", "38-46", ">46"]
CAT_EDGES = [26.0, 32.0, 38.0, 46.0]
DAY_START, DAY_END = 6 * 3600, 22 * 3600
FEED_GAPS = [("21:53", "22:07")]  # T04 known feed outage (local)

INF = np.inf


def hhmm_to_s(s: str) -> int:
    parts = [int(p) for p in str(s).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def s_to_hhmm(t: float, sec: bool = False) -> str:
    t = int(round(t))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if sec else f"{h:02d}:{m:02d}"


def step_pos(t):
    return np.clip((np.asarray(t, dtype=float) - heatmod.STEP0_S) / heatmod.STEP_S, 0, heatmod.N_STEPS - 1)


def interp_steps(arr: np.ndarray, t, idx) -> np.ndarray:
    """arr [33, n]; value of column idx at time t (seconds of day), linear between steps."""
    p = step_pos(t)
    i0 = np.minimum(np.floor(p).astype(int), heatmod.N_STEPS - 2)
    f = p - i0
    return arr[i0, idx] * (1 - f) + arr[i0 + 1, idx] * f


def _bundle_dir() -> Path:
    src = os.environ.get("ROUTER_BUNDLE", "gs://hackaton-honory-heat/T09/bundle")
    if not src.startswith("gs://"):
        return Path(src)
    cache = Path(os.environ.get("ROUTER_CACHE", "/tmp/router_bundle"))
    cache.mkdir(parents=True, exist_ok=True)
    from google.cloud import storage
    b, _, prefix = src[5:].partition("/")
    cl = storage.Client(project="hackaton-honory")
    for blob in cl.list_blobs(b, prefix=prefix.rstrip("/") + "/"):
        name = blob.name.rsplit("/", 1)[-1]
        if name and not (cache / name).exists():
            blob.download_to_filename(str(cache / name))
    return cache


@dataclass
class Tree:
    """Shortest-path tree from one end for one cost family and one step (or static)."""
    dist: np.ndarray
    pred: np.ndarray
    time: np.ndarray
    dose: np.ndarray  # dose along the tree path (at this tree's step; for static trees: [33, N])


@dataclass
class Itin:
    kind: str
    cost: float
    walk_only: bool = False
    depart: float = 0.0
    path_nodes: list = field(default_factory=list)  # walk-only
    e1: int = -1
    e2: int = -1
    e3: int = -1
    e4: int = -1
    tpair: int = -1
    tree_o: tuple = None  # (family, step) of access tree
    tree_d: tuple = None


class Router:
    def __init__(self, bundle: str | Path | None = None, heat_source: str | None = None):
        t0 = time.time()
        d = Path(bundle) if bundle else _bundle_dir()
        self.edges = pd.read_parquet(d / "edges.parquet")
        self.nodes = pd.read_parquet(d / "nodes.parquet")
        self.stops = pd.read_parquet(d / "stops.parquet")
        ev = pd.read_parquet(d / "events.parquet")
        g = np.load(d / "edge_geom.npz")
        self.geom_coords, self.geom_offs = g["coords"], g["offsets"]
        self.forcing = pd.read_csv(d / "forcing.csv") if (d / "forcing.csv").exists() else None
        ld = pd.read_csv(d / "line_delays.csv", dtype={"line": str})
        self.margin_by_line = {
            r.line: float(np.clip(MARGIN_BASE_S + max(0.0, -float(r.p05)), MARGIN_BASE_S, MARGIN_MAX_S))
            for r in ld.itertuples()
        }
        self._build_graph()
        self._build_events(ev)
        self.heat_source = (heat_source or os.environ.get("HEAT_SOURCE", "placeholder")).lower()
        self.base_heat = heatmod.load(self.heat_source, self.edges, self.stops, self.forcing)
        self._heat_cache = {}
        self._build_transfers()
        self.load_s = time.time() - t0
        log.info("router loaded in %.1f s (heat=%s)", self.load_s, self.heat_source)

    # ------------------------------------------------------------------ setup
    def _build_graph(self):
        e = self.edges
        n = len(self.nodes)
        self.N = n
        t = e.length_m.to_numpy(float) / WALK_SPEED
        t = np.where(e.steps.values, t * STEPS_FACTOR, t)
        t = t + np.where(e.crossing_signals.values, SIGNAL_WAIT_S, 0.0)
        self.edge_time = np.maximum(t, 0.05)
        u, v = e.u.to_numpy(np.int64), e.v.to_numpy(np.int64)
        # dedupe parallel edges (keep the quickest)
        a, b = np.minimum(u, v), np.maximum(u, v)
        order = np.lexsort((self.edge_time, b, a))
        key = a[order] * n + b[order]
        first = np.r_[True, key[1:] != key[:-1]]
        keep = order[first & (a[order] != b[order])]
        self.g_edges = keep
        rows = np.r_[u[keep], v[keep]]
        cols = np.r_[v[keep], u[keep]]
        eid = np.r_[keep, keep]
        m = sp.csr_matrix((np.arange(len(eid)) + 1, (rows, cols)), shape=(n, n))
        m.sort_indices()
        self.csr_indptr, self.csr_indices = m.indptr, m.indices
        self.csr_edge = eid[m.data - 1]  # edge index of each csr entry
        self.pair_key = rows * n + cols
        o = np.argsort(self.pair_key)
        self.pair_key_sorted, self.pair_edge_sorted = self.pair_key[o], eid[o]
        # snapping: local metric frame on lon/lat
        self.lat0 = float(self.nodes.lat.mean())
        self.kx = 111320.0 * math.cos(math.radians(self.lat0))
        self.ky = 110540.0
        self.kd = cKDTree(np.c_[self.nodes.lon.values * self.kx, self.nodes.lat.values * self.ky])

    def _edge_between(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        k = np.asarray(a, np.int64) * self.N + np.asarray(b, np.int64)
        i = np.searchsorted(self.pair_key_sorted, k)
        return self.pair_edge_sorted[np.minimum(i, len(self.pair_key_sorted) - 1)]

    def _graph(self, w_edge: np.ndarray):
        return sp.csr_matrix((w_edge[self.csr_edge], self.csr_indices, self.csr_indptr), shape=(self.N, self.N))

    def _build_events(self, ev: pd.DataFrame):
        self.events_all = ev
        sidx = pd.Series(np.arange(len(self.stops)), index=self.stops.stop_id.values)
        ev = ev[ev.ac_boardable & ev.stop_id.isin(sidx.index)].copy()
        ev = ev.sort_values(["run_id", "seq"]).reset_index(drop=True)
        ev["stop"] = sidx[ev.stop_id.values].values
        ev["gid"] = pd.factorize(ev.run_id)[0]
        ev["usable"] = ev.in_aoi_strict.values  # board / alight only inside Śródmieście
        ev["margin"] = ev.line.map(self.margin_by_line).fillna(MARGIN_DEFAULT_S).values
        self.ev = ev
        self.ev_gid = ev.gid.to_numpy(np.int64)
        self.ev_stop = ev.stop.to_numpy(np.int64)
        self.ev_arr = ev.arr_s.to_numpy(float)
        self.ev_dep = ev.dep_s.to_numpy(float)
        self.ev_use = ev.usable.to_numpy(bool)
        self.ev_margin = ev.margin.to_numpy(float)
        self.n_nonac = int((~self.events_all.ac_boardable).sum())
        # boarding index per stop, sorted by departure
        b = np.where(self.ev_use)[0]
        o = np.lexsort((self.ev_dep[b], self.ev_stop[b]))
        self.board_idx = b[o]
        self.board_stop = self.ev_stop[self.board_idx]
        self.board_dep = self.ev_dep[self.board_idx]
        self.stop_board_ptr = np.searchsorted(self.board_stop, np.arange(len(self.stops) + 1))
        self.stop_node = self.stops.access_node.to_numpy(np.int64)
        self.stop_extra_t = self.stops.access_dist_m.to_numpy(float) / WALK_SPEED

    def _build_transfers(self):
        """Stop-to-stop transfer walks (quickest path, <= MAX_TRANSFER_WALK_S) incl. same-stop."""
        G = self._graph(self.edge_time)
        nodes = np.unique(self.stop_node)
        dist, pred = dijkstra(G, indices=nodes, return_predecessors=True, limit=MAX_TRANSFER_WALK_S)
        row_of = {n: i for i, n in enumerate(nodes)}
        rows = []
        S = len(self.stops)
        for a in range(S):
            ra = row_of[self.stop_node[a]]
            for b in range(S):
                t = dist[ra, self.stop_node[b]] + self.stop_extra_t[a] + self.stop_extra_t[b]
                if a == b:
                    t = 0.0
                if np.isfinite(t) and t <= MAX_TRANSFER_WALK_S:
                    rows.append((a, b, t))
        tp = pd.DataFrame(rows, columns=["a", "b", "t"])
        paths, offs = [], [0]
        for r in tp.itertuples():
            if r.a == r.b:
                p = []
            else:
                p = self._path_nodes(pred[row_of[self.stop_node[r.a]]], self.stop_node[r.b])
            paths.append(p)
            offs.append(offs[-1] + max(len(p) - 1, 0))
        self.tr = tp.sort_values(["a"]).reset_index(drop=True) if False else tp
        self.tr_paths = paths
        edges = [self._edge_between(np.array(p[:-1]), np.array(p[1:])) if len(p) > 1 else np.zeros(0, np.int64)
                 for p in paths]
        self.tr_edges = edges
        self.tr_a = tp.a.to_numpy(np.int64)
        self.tr_b = tp.b.to_numpy(np.int64)
        self.tr_t = tp.t.to_numpy(float)
        o = np.argsort(self.tr_a, kind="stable")
        self.tr_order = o
        self.tr_ptr = np.searchsorted(self.tr_a[o], np.arange(S + 1))
        self._tr_dose_cache = {}

    def _tr_dose(self, H) -> np.ndarray:
        """[33, n_pairs] transfer walk dose per step (static path)."""
        k = id(H)
        if k not in self._tr_dose_cache:
            out = np.zeros((heatmod.N_STEPS, len(self.tr_t)))
            for i, e in enumerate(self.tr_edges):
                if len(e):
                    out[:, i] = (H.seg_g[:, e] * self.edge_time[e]).sum(axis=1)
            self._tr_dose_cache = {k: out}
        return self._tr_dose_cache[k]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _path_nodes(pred: np.ndarray, target: int) -> list:
        p = [int(target)]
        while True:
            q = pred[p[-1]]
            if q < 0:
                break
            p.append(int(q))
        return p[::-1]

    def _tree_accumulate_fixed(self, pred, vals):
        # pointer doubling done right: acc_k(n) = sum over 2^k ancestors
        N = self.N
        reach = pred >= 0
        par = np.where(reach, pred, np.arange(N))
        nodes = np.where(reach)[0]
        e = np.zeros(N, np.int64)
        e[nodes] = self._edge_between(par[nodes], nodes)
        acc = np.zeros((vals.shape[0], N))
        acc[:, nodes] = vals[:, e[nodes]]
        anc = par
        while True:
            acc = acc + acc[:, anc] * (anc != np.arange(N))[None, :]
            nxt = anc[anc]
            if np.array_equal(nxt, anc):
                break
            anc = nxt
        return acc

    def heat_for(self, weights=None, segment_heat=None, stop_heat=None):
        w = heatmod.parse_weights(weights)
        key = (w, id(segment_heat) if segment_heat is not None else None,
               id(stop_heat) if stop_heat is not None else None)
        if key in self._heat_cache:
            return self._heat_cache[key]
        H = self.base_heat
        if segment_heat is not None or stop_heat is not None:
            seg = segment_heat if segment_heat is not None else self._frame_from_heat(H, "seg")
            stp = stop_heat if stop_heat is not None else self._frame_from_heat(H, "stop")
            H = heatmod.from_frames(self.edges, self.stops, seg, stp, "override")
        if w != heatmod.DEFAULT_WEIGHTS:
            H = H.reweighted(w)
        if len(self._heat_cache) > 8:
            self._heat_cache.clear()
        self._heat_cache[key] = H
        return H

    def _frame_from_heat(self, H, what):
        S = heatmod.N_STEPS
        if what == "seg":
            n = len(self.edges)
            return pd.DataFrame({"segment_id": np.tile(self.edges.segment_id.values, S),
                                 "step": np.repeat(np.arange(S), n), "utci_mean": H.seg_utci.ravel(),
                                 "utci_p90": H.seg_p90.ravel(), "g": H.seg_g.ravel(),
                                 "shade_share": H.seg_shade.ravel()})
        n = len(self.stops)
        return pd.DataFrame({"stop_id": np.tile(self.stops.stop_id.values, S), "step": np.repeat(np.arange(S), n),
                             "utci": H.stop_utci.ravel(), "g": H.stop_g.ravel()})

    def snap(self, lonlat):
        lon, lat = float(lonlat[0]), float(lonlat[1])
        d, i = self.kd.query([lon * self.kx, lat * self.ky])
        return int(i), float(d)

    # ------------------------------------------------------------------ exact pricing
    def _price_walk(self, nodes: list, start: float, H):
        """Exact time-dependent pricing of a node path starting at `start`."""
        if len(nodes) < 2:
            return dict(t=0.0, dose=0.0, edges=np.zeros(0, np.int64), enter=np.zeros(0), utci=np.zeros(0),
                        shade=np.zeros(0), tt=np.zeros(0))
        a, b = np.array(nodes[:-1]), np.array(nodes[1:])
        e = self._edge_between(a, b)
        tt = self.edge_time[e]
        enter = start + np.r_[0.0, np.cumsum(tt)[:-1]]
        g = interp_steps(H.seg_g, enter, e)
        u = interp_steps(H.seg_utci, enter, e)
        sh = interp_steps(H.seg_shade, enter, e)
        return dict(t=float(tt.sum()), dose=float((g * tt).sum()), edges=e, enter=enter, utci=u, shade=sh, tt=tt,
                    fwd=(self.edges.u.values[e] == a))

    def _edge_line(self, edges, fwd):
        pts = []
        for e, f in zip(edges, fwd):
            c = self.geom_coords[self.geom_offs[e]:self.geom_offs[e + 1]]
            if not f:
                c = c[::-1]
            if pts and np.allclose(pts[-1], c[0]):
                c = c[1:]
            pts.extend(c.tolist())
        return pts

    # ------------------------------------------------------------------ main search
    def plan(self, origin, destination, earliest, latest, weights=None, segment_heat=None, stop_heat=None,
             lambdas=None):
        tq = time.time()
        H = self.heat_for(weights, segment_heat, stop_heat)
        lam = dict(LAMBDA, **(lambdas or {}))
        t0, t1 = hhmm_to_s(earliest), hhmm_to_s(latest)
        notes = list(H.notes)
        if t0 < DAY_START or t1 > DAY_END:
            notes.append("window clipped to 06:00-22:00 (heat day and transit day)")
            t0, t1 = max(t0, DAY_START), min(t1, DAY_END)
        if t1 <= t0:
            raise ValueError("latest must be after earliest")
        o_node, o_snap = self.snap(origin)
        d_node, d_snap = self.snap(destination)
        for nm, sd in (("origin", o_snap), ("destination", d_snap)):
            if sd > 150:
                notes.append(f"{nm} is {sd:.0f} m from the walk network (outside Śródmieście?)")
        o_extra, d_extra = o_snap / WALK_SPEED, d_snap / WALK_SPEED
        for a, b in FEED_GAPS:
            if t0 < hhmm_to_s(b) and t1 > hhmm_to_s(a):
                notes.append(f"transit feed gap {a}-{b}: runs missing from the record, not cancelled service")

        S = heatmod.N_STEPS
        s_lo = int(np.floor(step_pos(t0)))
        s_hi = int(np.ceil(step_pos(t1)))
        steps = np.arange(s_lo, s_hi + 1)

        # --- trees ------------------------------------------------------------
        Gs = self._graph(self.edge_time)
        trees = {}
        dist, pred = dijkstra(Gs, indices=[o_node, d_node], return_predecessors=True)
        for k, (end, node) in enumerate((("o", o_node), ("d", d_node))):
            acc = self._tree_accumulate_fixed(pred[k], np.vstack([self.edge_time[None, :],
                                                                  H.seg_g * self.edge_time[None, :]]))
            trees[(end, "fastest", None)] = Tree(dist[k], pred[k], acc[0], acc[1:])
        for fam in ("coolest", "balanced"):
            for s in steps:
                Gw = self._graph(self.edge_time * (H.seg_g[s] + lam[fam]) + 1e-6)
                dist, pred = dijkstra(Gw, indices=[o_node, d_node], return_predecessors=True)
                for k, end in enumerate(("o", "d")):
                    acc = self._tree_accumulate_fixed(pred[k], self.edge_time[None, :])[0]
                    trees[(end, fam, int(s))] = Tree(dist[k], pred[k], acc, dist[k] - lam[fam] * acc)
        t_trees = time.time() - tq

        best = {}
        for fam in ("coolest", "balanced", "fastest"):
            best[fam] = self._search_family(fam, lam[fam], trees, steps, t0, t1, o_node, d_node,
                                            o_extra, d_extra, H)
        t_search = time.time() - tq - t_trees

        # walk-only baseline: quickest path, best departure (lowest dose) in the window
        fast_path = self._path_nodes(trees[("o", "fastest", None)].pred, d_node)
        walk_t = trees[("o", "fastest", None)].time[d_node] + o_extra + d_extra
        best_wo, best_dose = None, INF
        last = max(t0, t1 - walk_t)
        for dep in np.unique(np.r_[np.arange(t0, last + 1, WALK_DEPART_GRID_S), last]):
            pw = self._price_walk(fast_path, dep + o_extra, H)
            if pw["dose"] < best_dose - 1e-9:
                best_dose, best_wo = pw["dose"], dep
        wo = Itin("walk_only", best_dose, walk_only=True, depart=best_wo, path_nodes=fast_path)

        # render every candidate exactly, then pick per family on the exact cost dose + lam * duration
        rendered, seen = [], set()
        for fam in ("coolest", "balanced", "fastest"):
            for it in best[fam]:
                op = self._render(it, H, trees, o_node, d_node, o_extra, d_extra, origin, destination)
                sig = tuple((lg["mode"], lg.get("run_id"), lg["start"], lg["end"]) for lg in op["legs"])
                if sig in seen or op["arrive_s"] > s_to_hhmm(t1, True) or op["depart_s"] < s_to_hhmm(t0, True):
                    continue
                seen.add(sig)
                rendered.append(op)
        options = []
        for fam in ("coolest", "balanced", "fastest"):
            if not rendered:
                notes.append(f"no {fam} option fits the window")
                continue
            costs = [op["dose_Ks"] + lam[fam] * op["duration_min"] * 60 for op in rendered]
            op = dict(rendered[int(np.argmin(costs))])
            op["kind"] = fam
            op["search_cost"] = round(float(min(costs)), 1)
            options.append(op)
        options.append(self._render(wo, H, trees, o_node, d_node, o_extra, d_extra, origin, destination))
        options[-1]["search_cost"] = None
        n_cand = len(rendered)
        if t1 - t0 < walk_t:
            notes.append("walking the whole way does not fit the window; walk_only shown departing at earliest")
        sigs = {}
        for op in options:
            sig = tuple((lg["mode"], lg.get("run_id"), lg["start"]) for lg in op["legs"])
            if sig in sigs and op["kind"] != "walk_only":
                op["same_as"] = sigs[sig]
            sigs.setdefault(sig, op["kind"])
        return {
            "query": {"origin": list(map(float, origin)), "destination": list(map(float, destination)),
                      "earliest": s_to_hhmm(t0), "latest": s_to_hhmm(t1), "date": "2026-06-28",
                      "heat_source": H.source, "weights": [list(x) for x in heatmod.parse_weights(weights)],
                      "lambda_K": lam, "snap_m": [round(o_snap, 1), round(d_snap, 1)],
                      "runtime_s": round(time.time() - tq, 3),
                      "runtime_detail_s": {"trees": round(t_trees, 3), "search": round(t_search, 3)},
                      "candidates": n_cand},
            "options": options,
            "notes": notes,
        }

    def _search_family(self, fam, lam, trees, steps, t0, t1, o_node, d_node, o_extra, d_extra, H):
        S_ = len(self.stops)
        tkey = (lambda end, s: (end, fam, None if fam == "fastest" else int(s)))
        sn = self.stop_node
        sx = self.stop_extra_t

        def end_values(end, t):
            """cost (dose+lam*time), time, at stop nodes, for walks starting (o) / starting (d) at time t[i]."""
            p = step_pos(t)
            i0 = np.clip(np.floor(p).astype(int), steps[0], steps[-1])
            out_c = np.full(len(t), INF)
            out_t = np.full(len(t), INF)
            stops_i = np.arange(S_)
            if fam == "fastest":
                tr = trees[tkey(end, None)]
                tim = tr.time[sn] + sx
                dose = interp_steps(tr.dose, t, sn)
                return dose + lam * tim, tim, i0
            for s in np.unique(i0):
                tr = trees[tkey(end, s)]
                m = i0 == s
                tim = tr.time[sn] + sx
                dose = tr.dose[sn]
                out_c[m] = dose[m] + lam * tim[m]
                out_t[m] = tim[m]
            return out_c, out_t, i0

        cand = []
        # ---- walk only, family path, departures on a grid
        if fam == "fastest":
            paths = {None: self._path_nodes(trees[("o", fam, None)].pred, d_node)}
        else:
            paths = {int(s): self._path_nodes(trees[("o", fam, int(s))].pred, d_node) for s in steps}
        seen = set()
        for s, pn in paths.items():
            key = tuple(pn)
            if key in seen:
                continue
            seen.add(key)
            pt = self.edge_time[self._edge_between(np.array(pn[:-1]), np.array(pn[1:]))].sum() + o_extra + d_extra
            last = t1 - pt
            if last < t0:
                continue
            for dep in np.unique(np.r_[np.arange(t0, last + 1, WALK_DEPART_GRID_S), last]):
                pw = self._price_walk(pn, dep + o_extra, H)
                c = pw["dose"] + lam * pt
                cand.append((c, Itin(fam, c, walk_only=True, depart=dep, path_nodes=pn)))

        # ---- transit
        win = (self.ev_dep >= t0) & (self.ev_arr <= t1)
        # per-stop access/egress at the time of each event
        E = len(self.ev_dep)
        idx = np.where(win & self.ev_use)[0]
        dep = self.ev_dep[idx]
        st = self.ev_stop[idx]
        mg = self.ev_margin[idx]
        # access: approximate start time using the quickest walk time
        tf = trees[("o", "fastest", None)].time[sn] + sx + o_extra
        start = dep - mg - tf[st]
        acc_c = np.full(len(idx), INF)
        acc_t = np.full(len(idx), INF)
        acc_s = np.zeros(len(idx), int)
        for s in (steps if fam != "fastest" else [None]):
            if fam == "fastest":
                m = np.ones(len(idx), bool)
                tr = trees[("o", fam, None)]
                tim = tr.time[sn[st]] + sx[st]
                acc_t[m] = tim + o_extra
                acc_c[m] = interp_steps(tr.dose, start, sn[st]) + lam * (tim + o_extra)
            else:
                si = np.clip(np.rint(step_pos(start)).astype(int), steps[0], steps[-1])
                m = si == s
                if not m.any():
                    continue
                tr = trees[("o", fam, int(s))]
                tim = tr.time[sn[st[m]]] + sx[st[m]]
                acc_t[m] = tim + o_extra
                acc_c[m] = tr.dose[sn[st[m]]] + lam * (tim + o_extra)
                acc_s[m] = s
        home = dep - mg - acc_t
        wait_g = interp_steps(H.stop_g, dep - mg / 2, st)
        head = np.full(E, INF)
        ok = np.isfinite(acc_c) & (home >= t0)
        head[idx[ok]] = (acc_c + wait_g * mg + lam * mg - lam * dep)[ok]
        acc_step = np.zeros(E, int)
        acc_step[idx] = acc_s

        HB, HBarg = self._run_cummin(head)

        # egress values per alight event
        aidx = np.where(win & self.ev_use & np.isfinite(HB))[0]
        arr = self.ev_arr[aidx]
        ast = self.ev_stop[aidx]
        eg_c = np.full(len(aidx), INF)
        eg_t = np.full(len(aidx), INF)
        eg_s = np.zeros(len(aidx), int)
        if fam == "fastest":
            tr = trees[("d", fam, None)]
            tim = tr.time[sn[ast]] + sx[ast] + d_extra
            eg_t[:] = tim
            eg_c[:] = interp_steps(tr.dose, arr, sn[ast]) + lam * tim
        else:
            si = np.clip(np.rint(step_pos(arr)).astype(int), steps[0], steps[-1])
            for s in np.unique(si):
                m = si == s
                tr = trees[("d", fam, int(s))]
                tim = tr.time[sn[ast[m]]] + sx[ast[m]] + d_extra
                eg_t[m] = tim
                eg_c[m] = tr.dose[sn[ast[m]]] + lam * tim
                eg_s[m] = s
        tot = HB[aidx] + lam * arr + eg_c
        tot[arr + eg_t > t1] = INF
        if np.isfinite(tot).any():
            k = int(np.argmin(tot))
            e2 = int(aidx[k])
            e1 = int(HBarg[e2])
            cand.append((float(tot[k]), Itin(fam, float(tot[k]), e1=e1, e2=e2,
                                             tree_o=(fam, acc_step[e1]), tree_d=(fam, eg_s[k]))))

        # ---- one transfer
        if len(aidx):
            # rows: (alight event, transfer pair)
            lo = self.tr_ptr[ast]
            hi = self.tr_ptr[ast + 1]
            cnt = hi - lo
            rep = np.repeat(np.arange(len(aidx)), cnt)
            pairs = self.tr_order[np.concatenate([np.arange(a, b) for a, b in zip(lo, hi)])] if len(rep) else \
                np.zeros(0, int)
            if len(rep):
                e2r = aidx[rep]
                tprime = self.ev_arr[e2r] + self.tr_t[pairs]
                trd = self._tr_dose(H)
                pp = step_pos(self.ev_arr[e2r])
                i0 = np.minimum(np.floor(pp).astype(int), heatmod.N_STEPS - 2)
                f = pp - i0
                dw = trd[i0, pairs] * (1 - f) + trd[i0 + 1, pairs] * f
                base = HB[e2r] + dw
                C = self.tr_b[pairs]
                # next departures at C after tprime (+ margin applied per candidate below)
                ptr0 = self.stop_board_ptr[C]
                ptr1 = self.stop_board_ptr[C + 1]
                # first board index with dep >= tprime + 60 (min margin)
                first = np.empty(len(C), np.int64)
                for c in np.unique(C):
                    m = C == c
                    a, b = self.stop_board_ptr[c], self.stop_board_ptr[c + 1]
                    first[m] = a + np.searchsorted(self.board_dep[a:b], tprime[m] + MARGIN_BASE_S)
                R, K = len(C), NEXT_DEPS_PER_TRANSFER
                j = first[:, None] + np.arange(K)[None, :]
                valid = j < ptr1[:, None]
                j = np.minimum(j, len(self.board_idx) - 1)
                e3 = self.board_idx[j]
                d3 = self.ev_dep[e3]
                wait = d3 - tprime[:, None]
                valid &= (wait >= self.ev_margin[e3]) & (wait <= MAX_TRANSFER_WAIT_S)
                valid &= self.ev_gid[e3] != self.ev_gid[e2r][:, None]
                valid &= d3 <= t1
                gC = interp_steps(H.stop_g, (tprime[:, None] + d3) / 2, C[:, None])
                h2 = base[:, None] + gC * wait
                h2[~valid] = INF
                rr, cc = np.nonzero(np.isfinite(h2))
                if len(rr):
                    vals = h2[rr, cc]
                    tgt = e3[rr, cc]
                    o = np.lexsort((vals, tgt))
                    tgt_o, first_o = tgt[o], np.r_[True, tgt[o][1:] != tgt[o][:-1]]
                    sel = o[first_o]
                    head2 = np.full(E, INF)
                    head2[tgt[sel]] = vals[sel]
                    src_row = np.full(E, -1)
                    src_row[tgt[sel]] = rr[sel]
                    HB2, HB2arg = self._run_cummin(head2)
                    a2 = np.where(win & self.ev_use & np.isfinite(HB2))[0]
                    if len(a2):
                        arr2 = self.ev_arr[a2]
                        st2 = self.ev_stop[a2]
                        if fam == "fastest":
                            tr = trees[("d", fam, None)]
                            tim2 = tr.time[sn[st2]] + sx[st2] + d_extra
                            c2 = interp_steps(tr.dose, arr2, sn[st2]) + lam * tim2
                            s2 = np.zeros(len(a2), int)
                        else:
                            s2 = np.clip(np.rint(step_pos(arr2)).astype(int), steps[0], steps[-1])
                            tim2 = np.full(len(a2), INF)
                            c2 = np.full(len(a2), INF)
                            for s in np.unique(s2):
                                m = s2 == s
                                tr = trees[("d", fam, int(s))]
                                tim2[m] = tr.time[sn[st2[m]]] + sx[st2[m]] + d_extra
                                c2[m] = tr.dose[sn[st2[m]]] + lam * tim2[m]
                        tot2 = HB2[a2] + lam * arr2 + c2
                        tot2[arr2 + tim2 > t1] = INF
                        if np.isfinite(tot2).any():
                            k = int(np.argmin(tot2))
                            e4 = int(a2[k])
                            e3b = int(HB2arg[e4])
                            row = int(src_row[e3b])
                            e2b = int(e2r[row])
                            e1b = int(HBarg[e2b])
                            cand.append((float(tot2[k]), Itin(fam, float(tot2[k]), e1=e1b, e2=e2b, e3=e3b, e4=e4,
                                                              tpair=int(pairs[row]),
                                                              tree_o=(fam, acc_step[e1b]),
                                                              tree_d=(fam, int(s2[k])))))
        if not cand:
            return []
        # best candidate of each type (walk / direct / one transfer); exact re-ranking happens in plan()
        out = {}
        for c, it in cand:
            typ = "walk" if it.walk_only else ("transfer" if it.e3 >= 0 else "direct")
            if typ not in out or c < out[typ][0]:
                out[typ] = (c, it)
        return [it for _, it in out.values()]

    def _run_cummin(self, head):
        """For each event i: min of head[j] over earlier events j of the same run (strictly before)."""
        E = len(head)
        BIG, CAP = 1e10, 1e9
        gid = self.ev_gid.astype(float)
        v = np.where(np.isfinite(head), head, CAP) - gid * BIG
        rm = np.minimum.accumulate(v)
        pos = np.arange(E)
        newmin = v <= rm
        arg = np.maximum.accumulate(np.where(newmin, pos, 0))
        val = rm + gid * BIG
        HB = np.full(E, INF)
        HBarg = np.full(E, -1)
        same = np.r_[False, self.ev_gid[1:] == self.ev_gid[:-1]]
        HB[1:] = np.where(same[1:], val[:-1], INF)
        HBarg[1:] = np.where(same[1:], arg[:-1], -1)
        bad = HB >= CAP / 2
        HB[bad] = INF
        HBarg[bad] = -1
        return HB, HBarg

    # ------------------------------------------------------------------ rendering
    def _render(self, it: Itin, H, trees, o_node, d_node, o_extra, d_extra, origin, destination,
                kind: str | None = None):
        legs, tl_t, tl_u = [], [], []
        walk = {"t": 0.0, "sh": 0.0}

        def walk_leg(nodes, start, extra_start=0.0, extra_end=0.0, p0=None, p1=None):
            pw = self._price_walk(nodes, start + extra_start, H)
            e = pw["edges"]
            if len(e):
                g_first = float(interp_steps(H.seg_g, start, e[:1])[0])
                g_last = float(interp_steps(H.seg_g, start + extra_start + pw["t"], e[-1:])[0])
                u_first, u_last = float(pw["utci"][0]), float(pw["utci"][-1])
                sh_first, sh_last = float(pw["shade"][0]), float(pw["shade"][-1])
            else:
                g_first = g_last = 0.0
                u_first = u_last = sh_first = sh_last = None
            dose = pw["dose"] + g_first * extra_start + g_last * extra_end
            tt = pw["t"] + extra_start + extra_end
            coords = self._edge_line(e, pw["fwd"]) if len(e) else []
            if p0 is not None:
                coords = [list(p0)] + coords
            if p1 is not None:
                coords = coords + [list(p1)]
            if len(coords) == 1:
                coords = coords * 2
            if len(e):
                tl_t.extend(pw["tt"].tolist())
                tl_u.extend(pw["utci"].tolist())
                walk["t"] += float(pw["tt"].sum())
                walk["sh"] += float((pw["shade"] * pw["tt"]).sum())
                for ex, uu, sh in ((extra_start, u_first, sh_first), (extra_end, u_last, sh_last)):
                    if ex > 0:
                        tl_t.append(ex)
                        tl_u.append(uu)
                        walk["t"] += ex
                        walk["sh"] += sh * ex
            leg = {"mode": "walk", "start": s_to_hhmm(start, True), "end": s_to_hhmm(start + tt, True),
                   "dose_Ks": round(float(dose), 1), "line": None, "run_id": None, "from_stop": None, "to_stop": None,
                   "length_m": round(float(self.edges.length_m.values[e].sum()) +
                                     (extra_start + extra_end) * WALK_SPEED, 1),
                   "duration_min": round(tt / 60, 1),
                   "geometry": {"type": "LineString",
                                "coordinates": [[round(float(x), 6), round(float(y), 6)] for x, y in coords]}}
            if len(e):
                leg["peak_utci"] = round(float(pw["utci"].max()), 1)
            legs.append(leg)
            return start + tt, dose

        def wait_leg(stop, a, b):
            mid = np.array([(a + b) / 2])
            g = float(interp_steps(H.stop_g, mid, np.array([stop]))[0])
            u = float(interp_steps(H.stop_utci, mid, np.array([stop]))[0])
            r = self.stops.iloc[stop]
            tl_t.append(b - a)
            tl_u.append(u)
            legs.append({"mode": "wait", "start": s_to_hhmm(a, True), "end": s_to_hhmm(b, True),
                         "dose_Ks": round(float(g * (b - a)), 1), "line": None, "run_id": None,
                         "from_stop": str(r.stop_id), "to_stop": str(r.stop_id), "stop_name": str(r.stop_name),
                         "utci": round(u, 1), "duration_min": round((b - a) / 60, 1),
                         "geometry": {"type": "Point", "coordinates": [round(float(r.lon), 6), round(float(r.lat), 6)]}})
            return g * (b - a)

        def ride_leg(e1, e2):
            r1 = self.ev.iloc[e1]
            seg = self.ev.iloc[e1:e2 + 1]
            coords = [[round(float(x), 6), round(float(y), 6)] for x, y in zip(seg.stop_lon.values, seg.stop_lat.values)]
            a, b = float(self.ev_dep[e1]), float(self.ev_arr[e2])
            tl_t.append(b - a)
            tl_u.append(heatmod.RIDE_UTCI - 1e-6)  # counted in "<26"
            legs.append({"mode": "ride", "start": s_to_hhmm(a, True), "end": s_to_hhmm(b, True), "dose_Ks": 0.0,
                         "line": str(r1.line), "run_id": str(r1.run_id), "from_stop": str(r1.stop_id),
                         "to_stop": str(self.ev.stop_id.values[e2]), "from_stop_name": str(r1.stop_name),
                         "to_stop_name": str(self.ev.stop_name.values[e2]), "vehicle_mode": str(r1["mode"]),
                         "headsign": str(r1.headsign), "ac": True, "n_stops": int(e2 - e1),
                         "duration_min": round((b - a) / 60, 1),
                         "geometry": {"type": "LineString", "coordinates": coords}})
            return b

        o_pt = [float(origin[0]), float(origin[1])]
        d_pt = [float(destination[0]), float(destination[1])]
        dose = 0.0
        if it.walk_only:
            depart = float(it.depart)
            t_end, d0 = walk_leg(it.path_nodes, depart, o_extra, d_extra, o_pt, d_pt)
            dose += d0
            arrive = t_end
        else:
            fam = it.kind
            so = None if fam == "fastest" else int(it.tree_o[1])
            sd = None if fam == "fastest" else int(it.tree_d[1])
            st1 = int(self.ev_stop[it.e1])
            p_acc = self._path_nodes(trees[("o", fam, so)].pred, int(self.stop_node[st1]))
            pw = self._price_walk(p_acc, 0.0, H)
            T = pw["t"] + o_extra + self.stop_extra_t[st1]
            dep1 = float(self.ev_dep[it.e1])
            depart = dep1 - float(self.ev_margin[it.e1]) - T
            t, d0 = walk_leg(p_acc, depart, o_extra, self.stop_extra_t[st1], o_pt, None)
            dose += d0
            dose += wait_leg(st1, t, dep1)
            last_alight = it.e2
            t = ride_leg(it.e1, it.e2)
            if it.e3 >= 0:
                a, b = int(self.tr_a[it.tpair]), int(self.tr_b[it.tpair])
                if a != b:
                    t, d0 = walk_leg(self.tr_paths[it.tpair], t, self.stop_extra_t[a], self.stop_extra_t[b])
                    dose += d0
                dep3 = float(self.ev_dep[it.e3])
                dose += wait_leg(b, t, dep3)
                t = ride_leg(it.e3, it.e4)
                last_alight = it.e4
            st2 = int(self.ev_stop[last_alight])
            p_eg = self._path_nodes(trees[("d", fam, sd)].pred, int(self.stop_node[st2]))[::-1]
            t, d0 = walk_leg(p_eg, t, self.stop_extra_t[st2], d_extra, None, d_pt)
            dose += d0
            arrive = t
        tl_t, tl_u = np.asarray(tl_t, float), np.asarray(tl_u, float)
        cat = np.digitize(tl_u, CAT_EDGES)
        mins = {c: round(float(tl_t[cat == i].sum()) / 60, 1) for i, c in enumerate(CATS)}
        walk_lines = [lg["geometry"]["coordinates"] for lg in legs if lg["mode"] in ("walk", "ride")]
        active = [lg for lg in legs if lg["mode"] in ("walk", "wait")]
        peak = max([lg.get("peak_utci", lg.get("utci", -99)) for lg in active] + [-99])
        n_rides = sum(lg["mode"] == "ride" for lg in legs)
        return {
            "kind": kind or it.kind, "depart": s_to_hhmm(depart), "arrive": s_to_hhmm(arrive),
            "depart_s": s_to_hhmm(depart, True), "arrive_s": s_to_hhmm(arrive, True),
            "duration_min": round(float(arrive - depart) / 60, 1), "dose_Ks": round(float(dose), 1),
            "peak_utci": round(peak, 1) if peak > -99 else None,
            "minutes_per_category": mins,
            "shade_share": round(walk["sh"] / walk["t"], 3) if walk["t"] > 0 else None,
            "walk_min": round(walk["t"] / 60, 1),
            "walk_m": round(sum(lg.get("length_m", 0) for lg in legs), 0),
            "n_rides": n_rides, "n_transfers": max(0, n_rides - 1),
            "search_cost": round(float(it.cost), 1),
            "legs": legs,
            "geometry": {"type": "MultiLineString", "coordinates": walk_lines},
        }

    # ------------------------------------------------------------------ external path pricing
    def price_path(self, coords_lonlat, depart, weights=None, segment_heat=None, stop_heat=None):
        """Snap a foreign walking line (e.g. ORS) to the network and price it from `depart`."""
        tq = time.time()
        H = self.heat_for(weights, segment_heat, stop_heat)
        c = np.asarray(coords_lonlat, float)
        xy = np.c_[c[:, 0] * self.kx, c[:, 1] * self.ky]
        pts = [xy[0]]
        for a, b in zip(xy[:-1], xy[1:]):
            L = float(np.hypot(*(b - a)))
            n = max(1, int(L // 20))
            for k in range(1, n + 1):
                pts.append(a + (b - a) * k / n)
        pts = np.array(pts)
        dd, nn = self.kd.query(pts)
        keep = dd <= 60
        keep[0] = keep[-1] = True
        dd, nn = dd[keep], nn[keep]
        seq = [int(nn[0])]
        for x in nn[1:]:
            if int(x) != seq[-1]:
                seq.append(int(x))
        Gs = self._graph(self.edge_time)
        path = [seq[0]]
        gaps = 0
        for a, b in zip(seq[:-1], seq[1:]):
            dist, pred = dijkstra(Gs, indices=a, return_predecessors=True, limit=600)
            if not np.isfinite(dist[b]):
                gaps += 1
                continue
            p = self._path_nodes(pred, b)
            path.extend(p[1:])
        # loop erasure: snapping noise creates spurs and small loops; keep a simple path
        clean, pos = [], {}
        for n in path:
            if n in pos:
                for x in clean[pos[n] + 1:]:
                    pos.pop(x, None)
                del clean[pos[n] + 1:]
                continue
            pos[n] = len(clean)
            clean.append(n)
        t0 = hhmm_to_s(depart)
        o_extra = float(dd[0]) / WALK_SPEED
        d_extra = float(dd[-1]) / WALK_SPEED
        it = Itin("external", 0.0, walk_only=True, depart=t0, path_nodes=clean)
        op = self._render(it, H, {}, clean[0], clean[-1], o_extra, d_extra, c[0], c[-1], kind="external")
        op["snap"] = {"mean_m": round(float(dd.mean()), 1), "max_m": round(float(dd.max()), 1),
                      "unconnected_gaps": gaps, "input_points": len(c)}
        op["runtime_s"] = round(time.time() - tq, 3)
        return op
