# T09: Heat-aware router

**Spec:** §7, §8, §9 · **Depends on:** T03, T04, T08 (develop on T03 + T04 now, with placeholder heat costs) · **Consumed by:** T10, T11

## Goal

Given an origin, a destination and a time window on the heat day, find trips that walk and ride only air-conditioned trams and buses. Return a few options (*coolest*, *balanced*, *fastest*, plus walk-only), each with its best departure time.

## Why it matters

This is the product: the pensioner plans the trip in space and time. Everything before this task exists to price it.

## Data

| Input | Where | Notes |
|---|---|---|
| Walk network | `hackaton-honory.warsaw_heat.t03_walk_segments`, `t03_walk_nodes`; `gs://hackaton-honory-heat/T03/network/` | Undirected, segments ≤ 25 m. Route on `component = 0`. Flags: `crossing_signals`, `steps`, `indoor`, `covered`. [`walk/README.md`](../walk/README.md) |
| Stops | `t03_stop_areas` | `access_node_id` joins each stop to the network |
| Transit | `hackaton-honory.warsaw_heat.stop_events` (`service_date = 2026-10-17`), `runs` | Board only `ac_boardable = TRUE`. `departure` = recorded time the vehicle left the pole. Join `stop_id` to T03. [`transit/README.md`](../transit/README.md) |
| Timing margin | [`reports/T04/line_delays.csv`](../reports/T04/line_delays.csv) | Per-line early side (p05/p10) and late side (p90/p95) |
| Heat costs | `t08_segment_heat`, `t08_stop_heat` ([T08](T08_segment_stop_heat.md)) | Until T08 lands, use a placeholder (e.g. constant, or sun share) with the same columns |

## Rules from the spec

- **Persona:** walks at about 1.0 m/s; stands while waiting.
- **Clock mapping:** transit is the Saturday 2026-10-17, heat is 2026-06-28. Clock time maps onto clock time, 06:00–22:00 local.
- **In an AC vehicle:** UTCI ≈ 26 °C, so dose ≈ 0. Waiting at home before departure is free.
- **Waiting at a stop,** including transfers, is charged at the stop's T08 value.
- **The traveller arrives shortly before the recorded departure,** with a safety margin.
- **Time-dependent costs:** a segment is priced at the time the traveller enters it, interpolated between the 30-min steps.
- **Signalised crossings** add a short wait.
- **Trips** board and alight only at stops inside Śródmieście.

## Expected output

- **Router code** in the repo with a function or HTTP endpoint: (origin, destination, earliest departure, latest arrival) → options.
- **Each option:** legs (walk / wait / ride with line and run), times, dose, total time, peak UTCI, minutes per UTCI category, share walking in shade, and the path geometry (for T10).
- **Report** in `reports/T09/`: method, runtime per query, a few example trips.

## Constraints and things to handle

- Dose and time trade off. Decide how *balanced* is defined, and keep the dose weights (spec §7) as parameters.
- Searching over departure times must be fast enough for an interactive app (seconds per query).
- Runs with unknown AC are not boardable. Lines 7 and 28 are mostly non-AC trams.
- Feed gaps (e.g. 21:53–22:07) mean missing runs, not cancelled service. Note them, don't hide them.
- Keep the walk-only fastest route as a baseline in every answer.

## Done when

- The router answers the T03 test pairs (Plac Zbawiciela → Krakowskie Przedmieście, Dworzec Centralny → Łazienki gate, …) at several departure times.
- On at least one pair the coolest option has a clearly lower dose than the fastest, and the report says what it costs in time.

## Out of scope

User interface (T10), metro and rail, trips leaving Śródmieście.
