"""Shadow casting on a regular grid.

Arrays are (row, col) with row 0 at the north edge and square cells of `res` metres.
Sun azimuth is in degrees clockwise from north, elevation in degrees above the horizon.

Two kernels:
- `solid_shade`: buildings + terrain are opaque. A line sweep along the shadow direction keeps,
  for every digital ray, the height of the highest shadow line reaching the current cell, so one
  time step costs O(pixels) whatever the shadow length (a 310 m tower at 3° sun casts ~6 km).
  Shadow length is never capped: casters beyond the grid come from an optional wider surface model
  (`far_solid`), which is swept together with the grid and cropped afterwards.
- `tree_transmission`: crowns are semi-transparent slabs between crown base and crown top. Each
  receiver marches towards the sun and accumulates optical depth (Beer-Lambert) where the ray is
  inside a crown, so low sun can pass under crowns and long oblique paths attenuate more.
"""

from dataclasses import dataclass

import numpy as np
from numba import njit, prange

EPS_M = 0.01


@dataclass
class Geometry:
    solid: np.ndarray       # ground + buildings, absolute height (m): opaque casters and receiver surface
    crown_base: np.ndarray  # absolute height of crown base (m); equal to crown_top where no canopy
    crown_top: np.ndarray   # absolute height of crown top (m)
    mu: np.ndarray          # crown attenuation per metre of ray path (1/m)
    res: float = 1.0
    far_solid: np.ndarray | None = None  # wider ground + buildings surface (m) with `solid` pasted in
    far_offset: tuple[int, int] = (0, 0)  # (row, col) of the grid's top-left cell inside far_solid

    @classmethod
    def from_layers(cls, dtm, dsm, canopy_height, crown_base_height, transmission, res=1.0, tau_min=0.01,
                    far_dsm=None, far_offset=(0, 0)):
        """Build from T01-style layers.

        dtm: ground (m); dsm: buildings + ground (m); canopy_height, crown_base_height: above ground (m);
        transmission: vertical crown light transmission in 0..1. NaN means "absent" for canopy layers.
        far_dsm: optional ground + buildings surface (m) on a wider grid with the same cells, the grid's
        top-left cell at `far_offset` (row, col). Used only as shadow casters; NaN = no caster.
        """
        dtm = np.asarray(dtm, np.float32)
        solid = np.where(np.isfinite(dsm), np.maximum(dsm, dtm), dtm).astype(np.float32)
        ch = np.nan_to_num(np.asarray(canopy_height, np.float32), nan=0.0)
        cb = np.nan_to_num(np.asarray(crown_base_height, np.float32), nan=0.0)
        cb = np.minimum(cb, ch)
        tau = np.clip(np.nan_to_num(np.asarray(transmission, np.float32), nan=1.0), tau_min, 1.0)
        depth = ch - cb
        mu = np.where(depth > 0, -np.log(tau) / np.maximum(depth, 1.0), 0.0).astype(np.float32)
        far = None
        if far_dsm is not None:
            far = np.asarray(far_dsm, np.float32)
            far = np.where(np.isfinite(far), far, np.nanmin(solid)).astype(np.float32)
            r0, c0 = far_offset
            far[r0:r0 + solid.shape[0], c0:c0 + solid.shape[1]] = solid
        return cls(solid, (dtm + cb).astype(np.float32), (dtm + ch).astype(np.float32), mu, float(res),
                   far, tuple(far_offset))


# ---------------------------------------------------------------- solid (buildings + terrain)

@njit(parallel=True, cache=True)
def _sweep(zc, zr, s, drop):
    """Canonical sweep: the shadow direction is +row, with `s` columns of lateral shift per row (|s| <= 1).

    Ray i visits column i + round(j*s) - kmax at row j, so every cell belongs to exactly one ray.
    h[i] is the height of the highest shadow line on ray i at the current row.
    """
    H, W = zc.shape
    klast = int(np.floor((H - 1) * s + 0.5))
    kmin = min(0, klast)
    kmax = max(0, klast)
    h = np.full(W + kmax - kmin, -np.inf, dtype=np.float32)
    shaded = np.zeros((H, W), dtype=np.uint8)
    for j in range(H):
        k = int(np.floor(j * s + 0.5))
        for c in prange(W):
            i = c - k + kmax
            hp = h[i] - drop
            if hp > zr[j, c] + EPS_M:
                shaded[j, c] = 1
            z = zc[j, c]
            h[i] = z if z > hp else hp
    return shaded


def _canonical(azimuth):
    """Orientation transform that makes the shadow direction point along +row."""
    a = np.radians(azimuth)
    ar, ac = np.cos(a), -np.sin(a)  # shadow direction (away from the sun) in (row, col)
    transpose = abs(ac) > abs(ar)
    if transpose:
        ar, ac = ac, ar
    flip = ar < 0
    return transpose, flip, ac / abs(ar)


def _to_canon(x, transpose, flip):
    if transpose:
        x = x.T
    if flip:
        x = x[::-1]
    return np.ascontiguousarray(x)


def _from_canon(x, transpose, flip):
    if flip:
        x = x[::-1]
    if transpose:
        x = x.T
    return np.ascontiguousarray(x)


def solid_shade(solid, azimuth, elevation, res=1.0, receiver=None):
    """uint8 (H, W): 1 where buildings or terrain block the direct sun."""
    if elevation <= 0:
        return np.ones(solid.shape, np.uint8)
    transpose, flip, s = _canonical(azimuth)
    drop = np.float32(res * np.sqrt(1.0 + s * s) * np.tan(np.radians(elevation)))
    zc = _to_canon(solid, transpose, flip)
    zr = zc if receiver is None else _to_canon(receiver, transpose, flip)
    out = _sweep(zc, zr, s, drop)
    return _from_canon(out, transpose, flip)


def grid_solid_shade(geom, azimuth, elevation, receiver_height=0.0):
    """solid_shade on the geometry's grid, with far casters when the geometry has them."""
    h = np.float32(receiver_height)
    if geom.far_solid is None:
        return solid_shade(geom.solid, azimuth, elevation, geom.res, geom.solid + h if h else None)
    out = solid_shade(geom.far_solid, azimuth, elevation, geom.res, geom.far_solid + h if h else None)
    r0, c0 = geom.far_offset
    return np.ascontiguousarray(out[r0:r0 + geom.solid.shape[0], c0:c0 + geom.solid.shape[1]])


# ---------------------------------------------------------------- trees

@njit(parallel=True, cache=True)
def _march(zr, cb, ct, mu, dc, dr, tan_e, path_per_step, step_px, zmax):
    H, W = zr.shape
    out = np.ones((H, W), dtype=np.float32)
    for r in prange(H):
        for c in range(W):
            z0 = zr[r, c]
            od = 0.0
            n = 0
            while True:
                t = (n + 0.5) * step_px  # distance to sample point, in cells
                zray = z0 + t * tan_e
                if zray > zmax:
                    break
                ci = int(np.floor(c + 0.5 + t * dc))
                ri = int(np.floor(r + 0.5 + t * dr))
                if ci < 0 or ci >= W or ri < 0 or ri >= H:
                    break
                if cb[ri, ci] <= zray and zray < ct[ri, ci]:
                    od += mu[ri, ci] * path_per_step
                    if od > 20.0:
                        break
                n += 1
            out[r, c] = np.exp(-od)
    return out


def tree_transmission(geom, azimuth, elevation, receiver=None, step=None):
    """float32 (H, W): share of the direct beam that passes the crowns (1 = no tree in the way)."""
    if elevation <= 0:
        return np.zeros(geom.solid.shape, np.float32)
    receiver = geom.solid if receiver is None else receiver
    step = geom.res if step is None else step
    a, e = np.radians(azimuth), np.radians(elevation)
    tan_e = np.tan(e) * geom.res  # height gain per cell of horizontal travel
    has_crown = geom.crown_top > geom.crown_base
    zmax = float(geom.crown_top[has_crown].max()) if has_crown.any() else -np.inf
    return _march(
        receiver, geom.crown_base, geom.crown_top, geom.mu,
        np.sin(a), -np.cos(a), tan_e, step / np.cos(e), step / geom.res, zmax,
    )


# ---------------------------------------------------------------- shares

SHARE_NAMES = ("f_sun", "f_bldg", "f_tree", "f_both")


def shares(solid_shaded, tree_tau):
    """Shade zone shares from solid shade (0/1) and tree transmission (0..1).

    The transmitted fraction of the beam counts as "not tree-shaded":
      not solid-shaded: f_sun = tau,  f_tree = 1 - tau
      solid-shaded:     f_bldg = tau, f_both = 1 - tau
    """
    b = solid_shaded.astype(bool)
    tau = tree_tau.astype(np.float32)
    zero = np.zeros_like(tau)
    return {
        "f_sun": np.where(b, zero, tau),
        "f_bldg": np.where(b, tau, zero),
        "f_tree": np.where(b, zero, 1 - tau),
        "f_both": np.where(b, 1 - tau, zero),
    }


def shade_step(geom, azimuth, elevation, receiver_height=0.0):
    """One time step -> (solid shade 0/1, tree transmission 0..1)."""
    receiver = geom.solid + np.float32(receiver_height) if receiver_height else geom.solid
    return (
        grid_solid_shade(geom, azimuth, elevation, receiver_height),
        tree_transmission(geom, azimuth, elevation, receiver),
    )


# ---------------------------------------------------------------- sky view factor

def sky_view_factor(geom, n_azimuth=32, n_elevation=8, with_trees=True, receiver_height=0.0):
    """SVF of a horizontal receiver from visibility of sky patches.

    The hemisphere is split into n_elevation bands x n_azimuth sectors. A patch counts as visible when
    the direct beam from its centre reaches the pixel. Patch weight (sin^2 e_hi - sin^2 e_lo) / n_azimuth
    is its cosine-weighted solid angle / pi, so the weights sum to 1. Far casters are ignored: beyond the
    grid's 1 km halo even a 310 m tower covers a negligible share of the hemisphere.
    Returns (svf_solid, svf_total); svf_total also applies crown transmission, or is None.
    """
    edges = np.linspace(0.0, 90.0, n_elevation + 1)
    svf_solid = np.zeros(geom.solid.shape, np.float32)
    svf_total = np.zeros(geom.solid.shape, np.float32) if with_trees else None
    receiver = geom.solid + np.float32(receiver_height) if receiver_height else geom.solid
    for lo, hi in zip(edges[:-1], edges[1:]):
        w = (np.sin(np.radians(hi)) ** 2 - np.sin(np.radians(lo)) ** 2) / n_azimuth
        el = 0.5 * (lo + hi)
        for k in range(n_azimuth):
            az = (k + 0.5) * 360.0 / n_azimuth
            vis = 1.0 - solid_shade(geom.solid, az, el, geom.res, receiver)
            svf_solid += np.float32(w) * vis
            if with_trees:
                svf_total += np.float32(w) * vis * tree_transmission(geom, az, el, receiver)
    return svf_solid, svf_total
