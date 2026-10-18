"""ORS Shaded (HeiGIT, openrouteservice Shaded Edition) walking routes for the demo trips, cached in GCS.

Endpoint (from the public app bundle at shaded.openrouteservice.org):
  POST https://api.shaded.openrouteservice.org/ors/v2/directions/foot-walking/geojson
  options.profile_params.weightings = {csv_column: "<doy>_<hour>", csv_factor: 0.8}
No API key is needed. Columns: doy 141/172/202/233 (21st of May-Aug) x hour 9/12/15/18.
The app maps a time of day to a slot: <=11:30 -> 9, <=14:30 -> 12, <=17:30 -> 15, else 18; June -> 172.
csv_factor 0 gives ORS's unweighted reference route.

Every answer is cached as gs://hackaton-honory-heat/T11/ors_cache/<trip>__<column>__f<factor>.json and
never re-requested while the cache holds it. Usage: python -m demo.ors_shaded [--trips demo/demo_trips.json]
"""
import argparse
import json
import time

import requests

from demo import common

URL = "https://api.shaded.openrouteservice.org/ors/v2/directions/foot-walking/geojson"
DOY = 172  # 21 June, the app's slot for June
HOURS = (9, 12, 15, 18)
FACTOR = 0.8  # the app's default
PAUSE_S = 2.0


def slot_hour(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    t = h * 60 + m
    if t <= 11 * 60 + 30:
        return 9
    if t <= 14 * 60 + 30:
        return 12
    if t <= 17 * 60 + 30:
        return 15
    return 18


def column_for(hhmm: str) -> str:
    return f"{DOY}_{slot_hour(hhmm)}"


def cache_name(trip_id, column, factor):
    return f"ors_cache/{trip_id}__{column}__f{factor:g}.json"


def request(origin, destination, column, factor):
    body = {"coordinates": [list(origin), list(destination)],
            "options": {"profile_params": {"weightings": {"csv_factor": factor, "csv_column": column}}},
            "extra_info": ["csv"], "instructions": False}
    r = requests.post(URL, json=body, timeout=60,
                      headers={"Accept": "application/geo+json, application/json",
                               "User-Agent": "warsaw-heat-hackathon-T11 (research; cached)"})
    r.raise_for_status()
    data = r.json()
    if "features" not in data:
        raise RuntimeError(f"ORS answer without features: {str(data)[:300]}")
    return {"request": body, "response": data, "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def get_route(trip, column, factor=FACTOR, fetch=True):
    """Cached ORS answer for a trip, or None if unavailable."""
    name = cache_name(trip["id"], column, factor)
    cached = common.gcs_read_json(name)
    if cached is not None or not fetch:
        return cached
    try:
        ans = request(trip["origin"], trip["destination"], column, factor)
    except Exception as e:  # external service: note and move on
        print(f"ORS failed for {trip['id']} {column} f{factor}: {e}")
        return None
    common.gcs_write_json(name, ans)
    time.sleep(PAUSE_S)
    return ans


def route_coords(ans):
    return ans["response"]["features"][0]["geometry"]["coordinates"]


def route_summary(ans):
    p = ans["response"]["features"][0]["properties"]
    csv = p.get("extras", {}).get("csv", {}).get("summary", [])
    dist = sum(c["distance"] for c in csv) or p["summary"]["distance"]
    sun = sum(c["value"] * c["distance"] for c in csv) / dist if csv else None  # ORS 0-100 = sun exposure
    return {"distance_m": p["summary"]["distance"], "duration_s": p["summary"]["duration"],
            "ors_sun_pct": sun}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trips", default=common.TRIPS_PATH)
    a = ap.parse_args()
    trips = common.load_trips(a.trips)
    for t in trips:
        for h, f in [(h, FACTOR) for h in HOURS] + [(12, 0.0)]:  # unweighted route is the same for any column
            if True:
                ans = get_route(t, f"{DOY}_{h}", f)
                if ans:
                    s = route_summary(ans)
                    print(f"{t['id']:36s} {DOY}_{h:<3d} f{f:g}  {s['distance_m']:7.0f} m  sun {s['ors_sun_pct']:.0f}%")


if __name__ == "__main__":
    main()
