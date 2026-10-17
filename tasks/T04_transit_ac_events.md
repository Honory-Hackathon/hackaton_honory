# T04: Recorded tram and bus operations → stop events with AC

**Spec:** §8 · **Depends on:** nothing · **Consumed by:** T09

## Goal

From the vehicle positions recorded in BigQuery for **Saturday 2026-10-17, 06:00–22:00 local**, reconstruct what trams and buses actually did. Produce:
- **Stop events:** for every run, the real arrival and departure time at each stop it served inside or through Śródmieście.
- **AC flag per run:** whether the vehicle that actually drove the run has air conditioning.

These events replace a timetable in the router: the pensioner only boards AC vehicles and times the walk to the recorded departure.

## Why it matters

Decisions 3 and "transit data" in the spec: only AC vehicles are boarded, and the transit side uses real-time data already stored in BigQuery, not a timetable.

## Data (BigQuery, project `hackaton-honory`, dataset `warsaw_transport`, europe-central2)

| Table | Content |
|---|---|
| `vehicle_positions` | `vehicle_type`, `vehicle_number`, `line`, `brigade`, `reported_at`, `report_age_s`, `lat`/`lon`, `api` (old/new). Coverage for 2026-10-17 06:00–22:00 local is complete: every minute, ~0.5M fresh pings and ~1,100 vehicles per hour. |
| `ztm_vehicles` | `mode`, `vehicle_number`, `model_id`, `production_year`; snapshot rows |
| `ztm_vehicle_models` | `mode`, `model_id`, `brand`, `type`, `air_conditioning` (BOOL, may be NULL) |
| `gtfs_stops`, `gtfs_trips`, `gtfs_stop_times` | mkuran.pl feed of 2026-10-16. Saturday service id `2026-10-17:SbS`. `gtfs_trips.block_short_name` is the brigade; `trip_id` looks like `date:line:daytype:brigade:start`. |

**Known quirks (verified):**
- Stale rows exist, some with `report_age_s` in the millions. Filter fresh pings, e.g. `report_age_s < 120`.
- Both old and new API copies are stored. De-duplicate them.
- About 150 frozen buses, and a few vehicles outside Warsaw.
- **Bus and tram fleet numbers collide**, so always join the register on `(mode, vehicle_number)`.
- Matching positions to GTFS on `(line, LTRIM(brigade,'0'))` = `(route_id, LTRIM(block_short_name,'0'))` works. An earlier pass matched ~21.8k tram and ~31.3k bus stop passes on this day with pings within 30 m of stops.
- AC on that Saturday in Śródmieście: 96.4% of tram and 99.1% of bus vehicle-minutes.
  - Mixed tram lines: e.g. 7 (≈36% AC) and 28 (≈70%).
  - Non-AC trams are mainly the 105N/105Na and 116N types.
  - A few vehicles are missing from the register (AC unknown); treat them as not boardable.

## Scope

- Trams and buses only. No metro, no suburban rail (SKM, KM, WKD).
- Stops: tram and bus stops inside Śródmieście (district boundary per the [shared conventions](README.md)). A run may leave the district between two inside stops.

## Expected output

BigQuery tables in `hackaton-honory.warsaw_heat` (create the dataset if needed) or Parquet in GCS:
- **`stop_events`:** run ID, mode, line, brigade, vehicle number, AC flag, stop sequence, `stop_id`, arrival, departure, and how each time was obtained (observed vs interpolated).
- **`runs`:** one row per run with its vehicle, AC flag and quality indicators (share of stops matched).
- **Report:**
  - Coverage compared with the scheduled Saturday service.
  - Typical deviation from the timetable per line (useful later for the walk-timing safety margin).
  - Known gaps.

## Constraints and things to handle

- Vehicles that pass a stop quickly may have no ping close to it. Decide how to estimate the passing time.
- Shared stop poles and parallel lines need care to avoid snapping to the wrong stop.
- Short-turning, depot runs and breaks between runs of the same brigade.
- Keep the SQL/Python reproducible: the same method may later be run for another recorded day.

## Done when

- Every hour 06–22 has stop events at the main Śródmieście stops (e.g. Centrum, Plac Zbawiciela, Rondo de Gaulle'a).
- Per-run AC is set from the actual vehicle.
- Event sequences are time-monotonic.
- The report states the coverage.

## Out of scope

Routing (T09), walking to and waiting at stops (T03/T08).
