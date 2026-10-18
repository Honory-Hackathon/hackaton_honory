"""STUB router with the T09 contract shape. Fake numbers; used only while router/ is not in the repo."""
from __future__ import annotations

import math

KINDS = ["coolest", "balanced", "fastest", "walk_only"]
CATS = ["<26", "26-32", "32-38", "38-46", ">46"]


def _hm(t_min: float) -> str:
    t = int(round(t_min)); return f"{t // 60:02d}:{t % 60:02d}"


def _hms(t_min: float) -> str:
    s = int(round(t_min * 60)); return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")[:2]; return int(h) * 60 + int(m)


def _utci_at(t_min: float) -> float:
    """Fake district-mean UTCI by time of day (peak ~41 degC around 15:30)."""
    h = t_min / 60
    return 27 + 14 * max(0.0, math.sin(math.pi * (h - 5.5) / 16)) if 5.5 <= h <= 21.5 else 27.0


def _g(u: float) -> float:
    return max(0, u - 26) + max(0, u - 32) + 2 * max(0, u - 38)


def _dist_m(a, b):
    kx = 111320 * math.cos(math.radians((a[1] + b[1]) / 2)); ky = 110540
    return math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * ky)


def _lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def _bend(a, b, f_off):
    """Point halfway, pushed sideways so options look different on the map."""
    m = _lerp(a, b, 0.5); dx, dy = b[0] - a[0], b[1] - a[1]
    return (m[0] - dy * f_off, m[1] + dx * f_off)


def _leg(mode, t0, t1, pts, shade, **kw):
    u = 26.0 if mode == "ride" else _utci_at((t0 + t1) / 2) - (5 if shade > 0.5 else 0)
    leg = {"mode": mode, "start": _hms(t0), "end": _hms(t1), "dose_Ks": round(_g(u) * (t1 - t0) * 60, 1),
           "line": None, "run_id": None, "from_stop": None, "to_stop": None,
           "geometry": ({"type": "Point", "coordinates": list(pts[0])} if mode == "wait"
                        else {"type": "LineString", "coordinates": [list(p) for p in pts]})}
    leg.update(kw)
    return leg, u


def _option(kind, origin, dest, depart_min):
    d = _dist_m(origin, dest)
    if kind in ("walk_only", "fastest") and kind == "walk_only":
        mid = _bend(origin, dest, 0.12)
        pts = [origin, mid, dest]; L = _dist_m(origin, mid) + _dist_m(mid, dest)
        t1 = depart_min + L / 60.0
        legs_u = [_leg("walk", depart_min, t1, pts, 0.35)]
    else:
        off = {"coolest": -0.15, "balanced": -0.05, "fastest": 0.0}[kind]
        s1 = _lerp(origin, _bend(origin, dest, off), 0.5); s2 = _lerp(_bend(origin, dest, off), dest, 0.5)
        w1 = _dist_m(origin, s1) / 60.0; ride = _dist_m(s1, s2) / 300.0; w2 = _dist_m(s2, dest) / 60.0
        wait = {"coolest": 4, "balanced": 3, "fastest": 2}[kind]
        t = depart_min; legs_u = []
        legs_u.append(_leg("walk", t, t + w1, [origin, s1], 0.7 if kind == "coolest" else 0.4)); t += w1
        legs_u.append(_leg("wait", t, t + wait, [s1], 0.5, from_stop="STUB-A")); t += wait
        legs_u.append(_leg("ride", t, t + ride, [s1, _bend(origin, dest, off), s2], 1.0, line="15", run_id="stub",
                           from_stop="STUB-A", to_stop="STUB-B")); t += ride
        legs_u.append(_leg("walk", t, t + w2, [s2, dest], 0.7 if kind == "coolest" else 0.4))
    legs = [l for l, _ in legs_u]
    mins = {c: 0.0 for c in CATS}
    for (l, u) in legs_u:
        dur = (_min(l["end"]) - _min(l["start"])) + 0.0 or 0.5
        c = CATS[0] if u < 26 else CATS[1] if u < 32 else CATS[2] if u < 38 else CATS[3] if u < 46 else CATS[4]
        mins[c] += dur
    t_end = _min(legs[-1]["end"]) + int(legs[-1]["end"][-2:]) / 60
    return {
        "kind": kind, "depart": _hm(depart_min), "arrive": _hm(t_end),
        "duration_min": round(t_end - depart_min, 1),
        "dose_Ks": round(sum(l["dose_Ks"] for l in legs), 0),
        "peak_utci": round(max(u for _, u in legs_u), 1),
        "minutes_per_category": {k: round(v, 1) for k, v in mins.items()},
        "shade_share": {"coolest": 0.62, "balanced": 0.48, "fastest": 0.31, "walk_only": 0.35}[kind],
        "legs": legs,
        "geometry": {"type": "MultiLineString",
                     "coordinates": [l["geometry"]["coordinates"] for l in legs if l["geometry"]["type"] == "LineString"]},
    }


def plan(origin, destination, earliest, latest, weights=None, segment_heat=None, stop_heat=None) -> dict:
    e, l = _min(earliest), _min(latest)
    if l < e:
        e, l = l, e
    best = {}
    for kind in KINDS:
        cands = [_option(kind, tuple(origin), tuple(destination), t) for t in range(e, max(e, l - 10) + 1, 15)]
        if kind == "fastest":
            best[kind] = min(cands, key=lambda o: (o["duration_min"], o["depart"]))
        elif kind == "balanced":
            best[kind] = min(cands, key=lambda o: o["dose_Ks"] + 20 * o["duration_min"])
        else:
            best[kind] = min(cands, key=lambda o: o["dose_Ks"])
    return {"query": {"origin": list(origin), "destination": list(destination), "earliest": earliest,
                      "latest": latest, "weights": weights or "spec §7 default"},
            "options": [best[k] for k in KINDS],
            "notes": ["STUB router: geometry and numbers are fake (T09 router not deployed yet)."]}


def price_path(coords_lonlat, depart, weights=None) -> dict:
    o = _option("walk_only", tuple(coords_lonlat[0]), tuple(coords_lonlat[-1]), _min(depart))
    o["kind"] = "external"
    o["legs"][0]["geometry"]["coordinates"] = [list(c) for c in coords_lonlat]
    o["geometry"] = {"type": "MultiLineString", "coordinates": [[list(c) for c in coords_lonlat]]}
    return o
