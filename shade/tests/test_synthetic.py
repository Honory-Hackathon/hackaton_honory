import numpy as np
import pytest

from shade import engine, synthetic
from shade.sun import sun_positions


def _shadow_extent(shaded, centre, azimuth, max_len):
    """Length (cells) of the shadow along the anti-sun ray from `centre`, counted outward."""
    a = np.radians(azimuth)
    dr, dc = np.cos(a), -np.sin(a)
    last = 0
    for t in range(1, max_len):
        r, c = int(round(centre[0] + t * dr)), int(round(centre[1] + t * dc))
        if not (0 <= r < shaded.shape[0] and 0 <= c < shaded.shape[1]):
            break
        if shaded[r, c]:
            last = t
    return last


@pytest.mark.parametrize("az,el", [(180, 30), (90, 45), (270, 20), (135, 30), (250, 15), (20, 10), (315, 60)])
def test_box_shadow_length_and_direction(az, el):
    half, h = 10, 30.0
    g = synthetic.box(n=401, half=half, height=h)
    sh = engine.solid_shade(g.solid, az, el)
    c = 200
    roof = g.solid > 0
    ground_shade = sh.astype(bool) & ~roof
    # direction: centroid of ground shadow lies along the anti-sun azimuth
    rr, cc = np.nonzero(ground_shade)
    ang = np.degrees(np.arctan2(cc.mean() - c, -(rr.mean() - c))) % 360  # bearing of shadow centroid
    assert abs(((ang - (az + 180)) + 180) % 360 - 180) < 2.0
    # length from the centre along the anti-sun ray: box exit distance + h / tan(el)
    a = np.radians(az)
    exit_t = (half + 0.5) / max(abs(np.cos(a)), abs(np.sin(a)))
    expected = exit_t + h / np.tan(np.radians(el))
    got = _shadow_extent(sh, (c, c), az, 400)
    assert abs(got - expected) <= 2.0  # at 45 deg the tip is the image of one corner cell
    # no shade on the sun side
    assert _shadow_extent(sh, (c, c), (az + 180) % 360, 400) <= exit_t + 0.5


@pytest.mark.parametrize("el", [3.0, 5.0, 12.5])
def test_tower_low_sun(el):
    h = 100.0
    g = synthetic.tower(n=4001 if el < 6 else 1501, height=h)
    az = 290.0
    sh = engine.solid_shade(g.solid, az, el)
    c = g.solid.shape[0] // 2
    expected = h / np.tan(np.radians(el))
    got = _shadow_extent(sh, (c, c), az, g.solid.shape[0])
    assert abs(got - expected) <= 3.0  # shadow follows the digital ray (+-0.5 cell), the probe the exact line
    assert sh.sum() <= 2.5 * expected  # a thin line, at most ~2 cells wide


def _cylinder_crossing(y, el, radius, base, top):
    """Analytic horizontal crossing length through the crown for a ground point `y` m from the tree axis
    on the anti-sun side."""
    te = np.tan(np.radians(el))
    lo, hi = max(y - radius, base / te), min(y + radius, top / te)
    return max(0.0, hi - lo)


@pytest.mark.parametrize("el", [60.0, 30.0, 10.0])
def test_cylinder_tree_transmission(el):
    r, base, top, tau = 5.0, 4.0, 12.0, 0.05
    g = synthetic.cylinder_tree(n=301, radius=r, base=base, top=top, tau=tau)
    az = 180.0  # sun in the south, shadow to the north (decreasing row)
    T = engine.tree_transmission(g, az, el)
    c = 150
    mu = -np.log(tau) / (top - base)
    for y in range(0, 80, 2):
        expected = _cylinder_crossing(y, el, r, base, top)
        got = -np.log(T[c - y, c]) * np.cos(np.radians(el)) / mu  # crossing length implied by T
        assert abs(got - expected) <= 1.5, (y, got, expected)


def test_light_under_crown_at_low_sun():
    g = synthetic.cylinder_tree(radius=5, base=4, top=12)
    c = g.solid.shape[0] // 2
    assert engine.tree_transmission(g, 180, 10)[c, c] == 1.0   # sun passes under the crown
    assert engine.tree_transmission(g, 180, 60)[c, c] < 0.2    # through 2.7 m of crown: analytic 0.13


def test_shares_sum_to_one():
    rng = np.random.default_rng(0)
    b = rng.integers(0, 2, (50, 50)).astype(np.uint8)
    t = rng.random((50, 50)).astype(np.float32)
    s = engine.shares(b, t)
    total = sum(s.values())
    assert np.allclose(total, 1.0)
    assert all((v >= 0).all() and (v <= 1).all() for v in s.values())


def test_tree_and_building_both():
    g = synthetic.box(n=201, half=5, height=30.0)
    g.crown_top[100:106, 120:130] = 15.0
    g.crown_base[100:106, 120:130] = 3.0
    g.mu[100:106, 120:130] = -np.log(0.05) / 12
    b, t = engine.shade_step(g, 270.0, 30.0)  # sun in the west, shadow to the east
    s = engine.shares(b, t)
    assert s["f_both"][103, 135] > 0.5     # behind both the box and the crown
    assert s["f_sun"][150, 100] == 1.0


def test_svf_flat_and_canyon():
    g = synthetic.canyon(n=401, width=20, height=20.0)
    svf, _ = engine.sky_view_factor(g, n_azimuth=32, n_elevation=9, with_trees=False)
    c = 200
    expected = np.cos(np.arctan(20.0 / 10.0))  # infinite canyon, centre of the floor
    assert abs(svf[c, c] - expected) < 0.04
    flat = synthetic.box(n=101, half=0, height=0.0)
    svf_flat, _ = engine.sky_view_factor(flat, n_azimuth=8, n_elevation=6, with_trees=False)
    assert np.allclose(svf_flat, 1.0, atol=1e-5)


def test_sun_positions_match_conventions():
    sp = sun_positions()
    assert len(sp) == 33
    el = sp["elevation"]
    assert abs(el.iloc[0] - 12.5) < 0.5          # 06:00
    assert 60.0 < el.max() < 62.0                 # ~12:30-13:00
    assert abs(el.iloc[29] - 2.9) < 0.5           # 20:30
    assert (el.iloc[30:] < 0.5).all()             # 21:00 onwards


def test_far_casters_reach_into_grid():
    """A tower outside the grid shades it through far_dsm, identically to running on the whole far grid."""
    n_far, n, off = 1200, 200, (500, 900)
    far = np.zeros((n_far, n_far), np.float32)
    far[600, 300] = 120.0  # 600 m west of the grid, outside it
    az, el = 270.0, 10.0   # sun in the west: shadow 680 m long, pointing east
    full = engine.solid_shade(far, az, el)
    z = np.zeros((n, n), np.float32)
    nan = np.full((n, n), np.nan, np.float32)
    g = engine.Geometry.from_layers(z, nan, nan, nan, nan, far_dsm=far, far_offset=off)
    b, _ = engine.shade_step(g, az, el)
    assert b.sum() > 0
    assert np.array_equal(b, full[off[0]:off[0] + n, off[1]:off[1] + n])
    alone = engine.Geometry.from_layers(z, nan, nan, nan, nan)
    assert engine.shade_step(alone, az, el)[0].sum() == 0
