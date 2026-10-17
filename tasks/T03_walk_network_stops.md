# T03: Walkable space, walk network and stop waiting areas

**Spec:** §5.1 (walkable mask), §8, §9 · **Depends on:** nothing · **Consumed by:** T07, T08, T09

## Goal

Define *where* a pedestrian can be in Śródmieście and *how* they move:
- **Walkable mask:** a 1 m raster of public walkable space, where UTCI will be evaluated.
- **Walk network:** a routable network split into short segments, each linked to the pixels it covers.
- **Stop waiting areas:** the area of every tram and bus stop where people wait, with shelter information.

## Why it matters

UTCI only matters where people actually walk and wait, and the walkable mask keeps the thermal computation small. The segments are the units the router will price in heat dose. Which side of the street a sidewalk is on is exactly where shade differs.

## Data

- **OpenStreetMap:** Geofabrik Mazowieckie or BBBike Warsaw PBF.
  - Footways, sidewalks, pedestrian streets and areas, paths in parks, crossings (with `crossing=traffic_signals`), steps.
  - `covered`, `tunnel` and `indoor` ways (arcades, underground passages around Centralna and Rondo Dmowskiego), `width`, `surface`.
  - Bus stops and platforms (`public_transport=platform`, `highway=bus_stop`, `railway=tram_stop`) with `shelter`, `bench` and `ref`.
  - Inside Śródmieście OSM has ~405 stop nodes/platforms: 297 `shelter=yes`, 79 `no`, 29 untagged.
  - Do not depend on Overpass, which was unreliable in October 2026; work from a PBF.
- **BDOT10k Warszawa** (GUGiK, GPKG, 2025-06): roads, building footprints and land cover. Useful where OSM sidewalks are missing or for widths.
- **GTFS stops** from BigQuery `hackaton-honory.warsaw_transport.gtfs_stops`: 250 bus, 76 tram and 4 bus+tram stops inside the district (metro and rail excluded).
  - OSM `ref` matches about 81% of GTFS `stop_id`s; the rest need a spatial match.
- **AOI and grid:** see the [shared conventions](README.md).

## Expected output

- **Walkable mask:** a COG on the shared grid (0/1, optionally with a category: sidewalk / crossing / plaza / park path / stop area / covered).
- **Walk network:** GeoParquet (and/or a BigQuery table).
  - Nodes, and segments of short length (tens of metres at most).
  - Per segment: stable ID, geometry, length, flags for crossing, signalised crossing, covered/indoor and steps, and a way to get the pixels it covers (e.g. a buffered footprint).
  - The network should be connected for routing.
- **Stop waiting areas:** per GTFS `stop_id`, a polygon of where people wait, plus a shelter flag and the matched OSM ID.
- **Report:** coverage stats; places where sidewalks are not mapped and how that was handled.

## Constraints and things to handle

- Where streets have no separately mapped sidewalks, decide how to represent both sides.
- Underground and indoor passages are walkable but outdoor UTCI does not apply to them. Flag them; the thermal treatment comes later.
- Keep segment IDs stable across reruns: the router and aggregation tables join on them.

## Done when

- Mask, network and stop areas exist for the whole district.
- A few manual routes (e.g. Plac Zbawiciela → Krakowskie Przedmieście, Centralna → Łazienki gate) are routable.
- Every Śródmieście tram/bus `stop_id` has a waiting area.

## Out of scope

Heat values per segment (T08), routing logic (T09).
