# T12: Data layers for the visualisations

**Spec:** §10 · **Depends on:** T01, T05, T06 (all usable now; the district SOLWEIG run is finishing) · **Consumed by:** T13, T10

## Goal

Prepare web-ready data for three visualisations (spec §10):
1. **Cool and water points** over the walking-route heat map.
2. **3D view of the day:** buildings and trees in 3D, shadows moving with the sun, cloud cover.
3. **Temperature and albedo through the day.**

## Why it matters

These pictures carry the pitch. They also show the parts of the physics the route cards hide: why a street is cool at 18:00 and hot at 13:00.

## Data and what to look for

**Cool and water points:**
- **OSM** (`T03/input/mazowieckie-2026-10-16.osm.pbf`): `amenity=drinking_water`, `amenity=fountain`, water features, benches in shade, parks.
- **City and MPWiK sources:** summer drinking fountains ("poidełka"), water curtains ("kurtyny wodne"), public toilets. A toilet sample from the city API is in `~/warsaw-open-data/downloads/samples/` (workspace, not in the repo).
- **Shaded rest spots:** can be derived from the SOLWEIG shadow and UTCI per step.
- Explore what exists and say how complete it is. Don't hammer city servers: their WAF answers HTTP 200 with a block page, so check response bodies.

**3D view:**
- Building heights and footprints from T01 (`dsm`, `dtm`, `building_mask`); trees from `canopy_height` and `crown_base_height`.
- Shadow per step from SOLWEIG (`T06/district/shadow_20260628.tif`) or T02 shares.
- Sun azimuth and elevation, cloud cover (`cloud_okta`, `cloud_era5_pct`) per step: T05 forcing. The day was nearly clear.

**Temperature and albedo:**
- Air temperature series: T05.
- Tmrt and UTCI per step: SOLWEIG.
- SOLWEIG can write further outputs per step (e.g. incoming and reflected shortwave, longwave, ground temperature; check the `solweig` package). Reflected ÷ incoming shortwave gives an effective albedo that changes with sun and shade.
  - Rerunning the pilot (`solweig_ref/`, `MODE=pilot`, ~10 min on 8 vCPU) with these outputs is cheap. A district rerun is ~20 min on the 5 regions.
- Static broadband albedo from Sentinel-2 L2A 2026-06-27 (tile 34UDC, the scene T01 used).
- Landsat surface temperature at 11:25: `T05/pmwc/lst_20260625_masked_10m.tif`.

## Expected output

- **In `gs://hackaton-honory-heat/T12/`, in formats a browser map or 3D viewer reads directly** (GeoJSON or vector/raster tiles in EPSG:4326/3857, glTF or 3D Tiles, PNG frames, JSON series), each with a short README:
  - **Cool and water points:** GeoParquet and GeoJSON, plus BigQuery `warsaw_heat.t12_cool_points`. Each point has its type and source.
  - **3D scene assets:**
    - buildings as extruded footprints with heights (or a mesh);
    - trees;
    - shadow per step, in a web format;
    - sun vector and cloud cover per step.
  - **Temperature/albedo layers:**
    - per-step rasters or frames of the chosen quantities;
    - the albedo map;
    - a small JSON of district and per-surface-type series through the day.
- **Report** in `reports/T12/` with previews.

## Constraints

- **Keep files small enough for a browser:** the full grid is 72 Mpx × 33 steps. Clip, tile or downsample as needed.
- **Work in the cloud:** heavy processing runs on Cloud Run Jobs, not on laptops.

## Done when

T13 can build all three views from `T12/` without further processing.

## Out of scope

The views themselves (T13), the route heat map's segment values (T08).
