"""Sun positions for the 33 time steps of the heat day (shared conventions)."""

import pandas as pd
import pvlib

LAT, LON = 52.23, 21.01  # Śródmieście
ALTITUDE_M = 100.0


def time_steps(day="2026-06-28", start="06:00", end="22:00", freq="30min", tz="Europe/Warsaw"):
    """Local time steps of the heat day (33 steps by default)."""
    return pd.date_range(f"{day} {start}", f"{day} {end}", freq=freq, tz=tz)


def sun_positions(times=None, lat=LAT, lon=LON, altitude=ALTITUDE_M, temperature=25.0):
    """DataFrame indexed by local time: utc, azimuth (deg clockwise from north), elevation (deg, refracted)."""
    if times is None:
        times = time_steps()
    sp = pvlib.solarposition.get_solarposition(times, lat, lon, altitude=altitude, temperature=temperature)
    return pd.DataFrame(
        {
            "utc": times.tz_convert("UTC"),
            "azimuth": sp["azimuth"].to_numpy(),
            "elevation": sp["apparent_elevation"].to_numpy(),
        },
        index=times,
    )
