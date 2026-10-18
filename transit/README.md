# transit: recorded tram and bus stop events with AC (task T04)

Turns the vehicle positions recorded in BigQuery into what trams and buses actually did at the
Śródmieście stops on a service day: one arrival and departure per run and stop, with the AC flag of
the vehicle that drove the run. The router (T09) uses these events instead of a timetable and boards
only `ac_boardable` runs. Method and results: root [`README.md`](../README.md).

## Run

```bash
pip install -r transit/requirements.txt
python -m transit.stop_events --date 2026-10-17 --start 06:00 --end 22:00 --out t04_out/2026-10-17
python -m transit.report t04_out/2026-10-17          # writes transit/line_delays.csv (router input)
```

Needs read access to `hackaton-honory.warsaw_transport` and the AOI in
`gs://hackaton-honory-heat/T01/meta/aoi_halo_grid.geojson`, and write access to `warsaw_heat` and the
bucket (skip with `--no-upload`). `--cache` reuses the BigQuery extracts saved in `--out`. Another
recorded day only needs a different `--date` (the GTFS feed in force on that date is picked
automatically). About 1 min; BigQuery does the filtering, Python handles ~1.4 M reports.

## Outputs

| Output | Where |
|---|---|
| `stop_events` | BigQuery `hackaton-honory.warsaw_heat.stop_events` (partitioned by `service_date`), `gs://hackaton-honory-heat/T04/<date>/stop_events.parquet` |
| `runs` | `hackaton-honory.warsaw_heat.runs`, `gs://hackaton-honory-heat/T04/<date>/runs.parquet` |
| `diagnostics.json` | `gs://hackaton-honory-heat/T04/<date>/` (parameters and counts) |

Times are UTC `TIMESTAMP`s. A run is one GTFS trip operated by one vehicle: `run_id = trip_id|vehicle_number`.

**`stop_events`** — one row per run and Śródmieście stop it was recorded at, in stop order:
`service_date`, `run_id`, `trip_id`, `mode` (tram/bus), `line`, `brigade` (leading zeros stripped),
`direction_id`, `trip_headsign`, `vehicle_number`, `ac` (register value, NULL = unknown),
`ac_boardable` (`ac` is TRUE), `stop_sequence`, `stop_id` (GTFS pole), `stop_name`, `stop_code`,
`stop_lat`, `stop_lon`, `in_aoi_strict` (inside the boundary, not only within 30 m),
`sched_arrival`, `sched_departure`, `arrival`, `departure`, `arrival_method` / `departure_method`
(`observed` or `interpolated`), `delay_s` (departure − scheduled departure), `n_reports_near`,
`interp_gap_s` (time between the two reports bracketing an interpolated stop), `pickup_type`,
`drop_off_type` (GTFS; 1 = no boarding / alighting).

**`runs`** — one row per run: trip fields, `vehicle_number`, register fields (`in_register`,
`model_id`, `brand`, `model_type`, `production_year`, `depot`), `ac`, `ac_boardable`,
`sched_first_aoi` / `sched_last_aoi`, `first_event` / `last_event`, `n_aoi_stops`, `n_events`,
`n_observed`, `n_interpolated`, `share_stops_matched`, `median_delay_s`, `n_reports` (assigned to
the run), `n_reports_used` (after the GPS-noise filter), `first_report` / `last_report`,
`vehicles_on_trip` (vehicles that claimed the trip; the best one is kept).

## Method

1. **Schedule** (`sql/scheduled_stops.sql`): tram and bus blocks (line + brigade) of the service day
   with at least one trip departing a Śródmieście stop inside the window; all trips of those blocks
   with all their stops. A stop counts as Śródmieście if it lies within 30 m of the district
   boundary (stops on boundary streets). Shapes (`sql/shapes.sql`) are densified to 5 m in
   EPSG:2180; stop positions along a shape come from GTFS `shape_dist_traveled`.
2. **Reports** (`sql/pings.sql`): report age ≤ 120 s, within 1.5 km of the district, one row per
   `(vehicle_type, vehicle_number, reported_at)` — this removes both the old/new API copies and the
   repeated polls of one report. Brigades are compared without leading zeros. Vehicles report
   about every 10 s.
3. **Assignment**: reports are grouped by vehicle and (line, brigade) label. A Viterbi pass walks
   each group in time and puts every report on one trip of that block or on none. Trips may only
   advance (later trips, skipping allowed at a small cost). A report can belong to a trip only if
   it lies within 60 m of the trip's shape and within the trip's scheduled span −30 / +60 min. Its
   cost adds the squared distance to the shape, the angle between the vehicle's motion and the
   shape direction (separates the two directions on shared tracks), and the lateness against the
   timetable interpolated at the projected position (separates consecutive trips on the same
   shape). Leaving a report unassigned costs a fixed amount. Groups that never move (frozen GPS)
   are skipped.
4. **Stop events**: per run, reports more than 40 m behind the run's progress are dropped as GPS
   noise. A stop is `observed` if reports lie within 30 m of it along the shape (less where stops
   are closer together) and within 40 m of the shape: arrival = first, departure = last such report.
   Otherwise the passing time is `interpolated` along the shape between the last report before
   and the first after the stop, if they are at most 300 s apart; else the stop gets no event.
   Arrival ≥ previous departure and departure ≥ arrival are enforced.
5. **Conflicts**: when two vehicles claim the same trip (label errors, swaps), the run with more
   events, then more observed events, is kept.
6. **AC** (`sql/vehicles.sql`): register row of `(mode, vehicle_number)` and its model, from the
   snapshot closest to the service day. Bus and tram fleet numbers collide, so mode is always part
   of the key. Missing vehicle or NULL `air_conditioning` → `ac` NULL, `ac_boardable` FALSE.

Parameters are constants at the top of `stop_events.py` and are written to `diagnostics.json`.

## Limits

- Only operations that match the Saturday GTFS blocks are kept; extra or replacement brigades
  are listed in the report but have no events.
- A run more than 60 min late or 30 min early is not matched to its trip.
- With one report at a stop, arrival = departure; interpolated stops always have arrival = departure.
  Long dwells at terminal stops include the layover.
- Shapes that pass the same point twice project reports to the nearest pass.
