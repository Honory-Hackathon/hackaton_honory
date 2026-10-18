"""Synthetic test scenes on flat ground at height 0 (1 m cells unless stated)."""

import numpy as np

from .engine import Geometry


def _empty(n):
    z = np.zeros((n, n), np.float32)
    nan = np.full((n, n), np.nan, np.float32)
    return z, nan


def box(n=301, half=10, height=30.0):
    """Square building of side 2*half+1 cells, centred."""
    dtm, nan = _empty(n)
    dsm = dtm.copy()
    c = n // 2
    dsm[c - half:c + half + 1, c - half:c + half + 1] = height
    return Geometry.from_layers(dtm, dsm, nan, nan, nan)


def tower(n=1501, height=100.0):
    """1 x 1 cell tower, centred."""
    dtm, nan = _empty(n)
    dsm = dtm.copy()
    dsm[n // 2, n // 2] = height
    return Geometry.from_layers(dtm, dsm, nan, nan, nan)


def cylinder_tree(n=201, radius=5.0, base=4.0, top=12.0, tau=0.05):
    """Cylindrical crown between base and top, centred, no trunk."""
    dtm, nan = _empty(n)
    yy, xx = np.mgrid[:n, :n] - n // 2
    inside = np.hypot(yy, xx) <= radius
    ch = np.where(inside, top, np.nan).astype(np.float32)
    cb = np.where(inside, base, np.nan).astype(np.float32)
    tr = np.where(inside, tau, np.nan).astype(np.float32)
    return Geometry.from_layers(dtm, nan, ch, cb, tr)


def canyon(n=401, width=20, height=20.0):
    """Infinite-like north-south street of `width` cells between two building blocks."""
    dtm, nan = _empty(n)
    dsm = np.full((n, n), height, np.float32)
    c = n // 2
    dsm[:, c - width // 2:c - width // 2 + width] = 0.0
    return Geometry.from_layers(dtm, dsm, nan, nan, nan)
