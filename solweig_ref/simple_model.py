"""Spec §13 closed-form model: verbatim scalar version, a vectorised copy, and its tunable generalisation."""

import numpy as np


def compute_inputs_for_utci(
    t_air_meteo: float,
    alt_deg: float,
    f_sun: float,
    f_bldg: float,
    f_tree: float,
    f_both: float,
    pmwc: float = 0.0,
) -> tuple[float, float]:
  """Returns (t_air_local, mrt_celsius) ready to pass into an external UTCI(t_air, mrt, wind, rh) function."""
  # 1. Local air temperature adjusted for Surface UHI anomaly
  # (Empirical ratio: Canopy Layer air UHI is ~15% of Landsat Surface UHI anomaly)
  t_air_local = t_air_meteo + 0.15 * pmwc

  alt_rad = np.radians(np.clip(alt_deg, 2.0, 90.0))
  sin_a = np.sin(alt_rad)
  cos_a = np.cos(alt_rad)

  # 2. Linearized radiative heat transfer coefficient h_r = 4 * eps_p * sigma * T_k^3
  h_r = 4.0 * 0.97 * 5.67e-8 * ((t_air_local + 273.15) ** 3)

  # 3. Shortwave solar radiation load on a standing human (VDI 3787 projection factor f_p)
  f_p = 0.308 * cos_a + 0.04
  i_direct = 880.0 * (sin_a**0.25)
  i_diffuse = 110.0 * sin_a

  # Max shortwave equivalent temperature rise in full sun (absorptivity = 0.70)
  delta_t_sw_max = (0.70 * (f_p * i_direct + 0.5 * i_diffuse)) / h_r

  # Apply zone shortwave transmissivities
  delta_t_sw = delta_t_sw_max * (
      1.00 * f_sun + 0.12 * f_tree + 0.07 * f_bldg + 0.03 * f_both
  )

  # 4. Longwave surface temperature offset (T_surf - T_air_local) * Ground View Factor (0.40)
  # Unshaded asphalt heats up by ~15-20°C at high sun; background walls add a small PMWC term
  dt_surf_sun = (15.0 + 0.30 * max(0.0, t_air_local - 25.0)) * sin_a
  dt_surf_bldg = 2.0
  dt_surf_tree = -0.5  # Leaf transpiration keeps canopy near/below air temp
  dt_surf_both = -1.5

  delta_t_lw = (
      0.40
      * (
          dt_surf_sun * f_sun
          + dt_surf_bldg * f_bldg
          + dt_surf_tree * f_tree
          + dt_surf_both * f_both
      )
      + 0.20 * pmwc
  )  # Background thermal wall emission in hot urban quarters

  mrt_celsius = float(t_air_local + delta_t_sw + delta_t_lw)
  return t_air_local, mrt_celsius


ZONES = ("sun", "bldg", "tree", "both")
SPEC = {"tau": (1.00, 0.07, 0.12, 0.03), "dt": (None, 2.0, -0.5, -1.5), "gvf": 0.40}
# README "mrt factors" for (f_sun, f_bldg, f_tree, f_both)
README_TAU = (1.00, 0.10, 0.20, 0.08)


def sw_max(t_air_local, alt_deg, dni=None, dhi=None):
    """Full-sun shortwave temperature rise. Parametric clear sky (spec) unless ERA5 dni/dhi are given."""
    alt_rad = np.radians(np.clip(alt_deg, 2.0, 90.0))
    sin_a, cos_a = np.sin(alt_rad), np.cos(alt_rad)
    h_r = 4.0 * 0.97 * 5.67e-8 * ((t_air_local + 273.15) ** 3)
    f_p = 0.308 * cos_a + 0.04
    i_direct = 880.0 * (sin_a**0.25) if dni is None else dni
    i_diffuse = 110.0 * sin_a if dhi is None else dhi
    return (0.70 * (f_p * i_direct + 0.5 * i_diffuse)) / h_r, sin_a


def simple_mrt(t_air_meteo, alt_deg, zone, pmwc=0.0, tau=SPEC["tau"], dni=None, dhi=None, night_off=False):
    """Vectorised spec model for one time step. `zone` is a uint8 array 0 sun, 1 bldg, 2 tree, 3 both; `pmwc` scalar or array."""
    t_air_local = t_air_meteo + 0.15 * np.asarray(pmwc, np.float32)
    swm, sin_a = sw_max(t_air_local, alt_deg, dni, dhi)
    if night_off and alt_deg <= 0:
        swm, sin_a = 0.0 * swm, 0.0
    dt_sun = (15.0 + 0.30 * np.maximum(0.0, t_air_local - 25.0)) * sin_a
    tau = np.asarray(tau, np.float32)
    dt = np.where(zone == 0, dt_sun, np.array([0.0, 2.0, -0.5, -1.5], np.float32)[zone])
    return (t_air_local + swm * tau[zone] + 0.40 * dt + 0.20 * np.asarray(pmwc, np.float32)).astype(np.float32)


def tuned_mrt(params, t_air_local, swm, sin_a, zone):
    """Tuned form: Tmrt - Ta = tau_z * swm + a_z * sin_a + b_z (12 parameters, per zone)."""
    tau, a, b = (np.asarray(params[k], np.float32) for k in ("tau", "a", "b"))
    return (t_air_local + tau[zone] * swm + a[zone] * sin_a + b[zone]).astype(np.float32)
