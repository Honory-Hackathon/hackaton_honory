"""Heat-aware router (T09): walking + air-conditioned trams/buses on the heat day 2026-06-28.

    from router import plan, price_path
    plan((21.01864, 52.21971), (21.01661, 52.24007), "13:00", "15:00")

Data: ROUTER_BUNDLE (local dir or gs:// prefix, default gs://hackaton-honory-heat/T09/bundle).
Heat: HEAT_SOURCE = placeholder | stub | final. Loaded once per process.
"""
from __future__ import annotations

import threading

from .core import Router  # noqa: F401

_router = None
_lock = threading.Lock()


def get_router() -> Router:
    global _router
    if _router is None:
        with _lock:
            if _router is None:
                _router = Router()
    return _router


def plan(origin, destination, earliest, latest, weights=None, segment_heat=None, stop_heat=None) -> dict:
    return get_router().plan(origin, destination, earliest, latest, weights=weights,
                             segment_heat=segment_heat, stop_heat=stop_heat)


def price_path(coords_lonlat, depart, weights=None, segment_heat=None, stop_heat=None) -> dict:
    return get_router().price_path(coords_lonlat, depart, weights=weights,
                                   segment_heat=segment_heat, stop_heat=stop_heat)
