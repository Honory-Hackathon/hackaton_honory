# T03 outputs: walkable mask, walk network, stop waiting areas

Location: `gs://hackaton-honory-heat/T03/`. Code: `walk/` in this repo (`fetch.sh` → `python -m walk.run` → `python -m walk.routes`).
Report: `reports/T03/README.md`.

## Sources and vintage

| Input | File in `T03/input/` | Vintage |
|---|---|---|
| OpenStreetMap, Geofabrik Mazowieckie (cut to the halo grid with `osmium extract`) | `mazowieckie-2026-10-16.osm.pbf` | replication timestamp 2026-10-16T20:21Z |
| GTFS stops served by tram or bus (`hackaton-honory.warsaw_transport.gtfs_*`) | `gtfs_stops.csv` | feed 2026-10-16 |
| AOI and grid | `T01/meta/aoi_halo_grid.geojson`, `T01/meta/grid.json` | PRG `146510_8` |

All vectors are EPSG:2180. Rerunning on the same inputs reproduces every file byte for byte.

## Files

### `walkable.tif`
uint8 COG on the shared grid (EPSG:2180, 1 m, bounds 634000 483000 642000 492000, 8000 × 9000). No nodata value: 0 = not walkable.
Categories: 1 sidewalk/footway, 2 crossing, 3 plaza (pedestrian street or area), 4 park path, 5 stop waiting area,
6 covered (arcade, building passage), 7 steps, 8 shared street (service road, living street).
Each pixel holds one category; where categories overlap, the higher one in this order wins: shared street < sidewalk < park path < plaza < steps < covered < crossing < stop area.
Building footprints (OSM `building=*` except `roof`) are cut out, except under covered segments.
Indoor and underground segments are **not** in the mask. The mask follows the network up to 50 m outside the AOI.

### `network/segments.parquet` (GeoParquet, one row per segment)

| Column | Meaning |
|---|---|
| `segment_id` | int64, stable: a hash of (OSM way id, side, OSM end nodes of the parent edge, piece index). Connectors: hash of their two nodes |
| `u`, `v` | node ids. Positive = OSM node id; negative = split node inside a way |
| `osm_way_id`, `side` | source way; `c` = on the way itself, `l`/`r` = synthetic sidewalk left/right of a street centreline |
| `kind` | `sidewalk`, `crossing`, `footway`, `path`, `steps`, `pedestrian`, `platform`, `shared_street`, `street_side` (synthetic), `corridor` (indoor), `connector` (gap closure ≤ 5 m) |
| `highway`, `name`, `surface` | OSM tags |
| `length_m` | length along the way (for `street_side`: along the centreline) |
| `width_m` | footprint width: the OSM `width` tag, else a default per kind |
| `crossing`, `crossing_signals`, `steps`, `covered`, `indoor` | flags. `indoor` = underground or indoor: walkable, but outdoor UTCI does not apply |
| `in_aoi` | segment intersects the AOI. Segments are kept up to 50 m outside it |
| `component` | connected component, 0 = main network (route on this one) |
| `mask_category` | category burnt into `walkable.tif` (null for indoor segments) |
| `pixel_count` | rows in `segment_pixels.parquet` |
| `geometry` | line. Synthetic sides are offset from the centreline, so their ends sit a few metres from `u`/`v` |
| `footprint` | buffered polygon used for the mask and pixel lookup |

Segments are at most 25 m long (median 14 m). The network is undirected.

### `network/nodes.parquet`
`node_id`, `osm_node_id` (null for split nodes), `degree`, `component`, point geometry.

### `network/segment_pixels.parquet`
`segment_id`, `row`, `col`: grid cells of `walkable.tif` (row 0 = northern edge y = 492000) whose centre lies in the segment footprint and in the mask. A pixel can belong to several segments.
Every outdoor segment has at least one pixel. If a segment's footprint contains no mask pixel, the pixels under its line are used and added to the mask.

### `stops/stop_areas.parquet` (GeoParquet, one row per GTFS `stop_id`)
Every tram/bus `stop_id` within 25 m of the AOI. `in_aoi` marks the ones inside the PRG polygon itself, because the boundary runs along the axes of avenues with stops on it.

| Column | Meaning |
|---|---|
| `stop_id`, `stop_name`, `mode` | GTFS; `mode` ∈ bus, tram, bus+tram |
| `shelter`, `bench` | OSM tags of the matched platform (true/false, null = untagged or unmatched) |
| `match` | `ref` (OSM `ref` = `stop_id`), `spatial` (nearest unclaimed platform ≤ 30 m) or `none` |
| `osm_type`, `osm_id`, `match_dist_m` | matched OSM platform |
| `geometry_source` | `osm_polygon` (platform area), `osm_line` (platform way buffered by 1.5 m) or `osm_point`/`gtfs_point` (a 3 m deep rectangle, 15 m long for bus and 30 m for tram, aligned with the nearest sidewalk) |
| `access_node_id`, `access_dist_m` | nearest main-network node to the area centroid |

### BigQuery: `hackaton-honory.warsaw_heat.t03_*`
`t03_walk_segments`, `t03_walk_nodes`, `t03_segment_pixels`, `t03_stop_areas` and `t03_test_routes` mirror the files above.
They are clustered by their id column and loaded with `python -m walk.to_bq OUT_DIR`.
Geometry columns are GEOGRAPHY in WGS84, reprojected from EPSG:2180. Use `length_m` for distances, not `ST_LENGTH`; the two differ by about 0.15 %.

### `checks/`
`routes.parquet` and `routes.csv`: test routes (shortest by length) with segment ids. `stats.json`: coverage figures. `previews/*.png`.
