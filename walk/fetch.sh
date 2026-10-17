#!/bin/bash
# Pin T03 inputs into IN_DIR (and gs://hackaton-honory-heat/T03/input/):
# OSM PBF (Geofabrik Mazowieckie, cut to the halo grid), T01 AOI + grid, GTFS tram/bus stops.
# usage: walk/fetch.sh IN_DIR [PBF_DATE]   (PBF_DATE=YYYY-MM-DD reuses the pinned PBF in GCS)
set -euo pipefail
IN=$1; DATE=${2:-}
BUCKET=gs://hackaton-honory-heat
mkdir -p "$IN" && cd "$IN"

if [ -z "$DATE" ]; then
  curl -sSfL -o mazowieckie.osm.pbf https://download.geofabrik.de/europe/poland/mazowieckie-latest.osm.pbf
  TS=$(osmium fileinfo -g header.option.osmosis_replication_timestamp mazowieckie.osm.pbf)
  DATE=${TS%%T*}
  mv mazowieckie.osm.pbf "mazowieckie-$DATE.osm.pbf"
  gsutil -q cp "mazowieckie-$DATE.osm.pbf" $BUCKET/T03/input/
else
  gsutil -q cp "$BUCKET/T03/input/mazowieckie-$DATE.osm.pbf" .
fi

gsutil -q cp $BUCKET/T01/meta/aoi_halo_grid.geojson $BUCKET/T01/meta/grid.json .
BBOX=$(python3 -c "
import json
from pyproj import Transformer
x0, y0, x1, y1 = json.load(open('grid.json'))['bounds']
t = Transformer.from_crs(2180, 4326, always_xy=True)
xs, ys = t.transform([x0, x1, x0, x1], [y0, y0, y1, y1])
print('%f,%f,%f,%f' % (min(xs), min(ys), max(xs), max(ys)))")
osmium extract -b "$BBOX" -s complete_ways --overwrite -o halo.osm.pbf "mazowieckie-$DATE.osm.pbf"

# Every stop_id served by at least one tram (0) or bus (3) route; metro/rail-only stops excluded
bq query --nouse_legacy_sql --format=csv --max_rows=100000 '
with served as (
  select distinct st.stop_id, r.route_type
  from `hackaton-honory.warsaw_transport.gtfs_stop_times` st
  join `hackaton-honory.warsaw_transport.gtfs_trips` using (trip_id)
  join `hackaton-honory.warsaw_transport.gtfs_routes` r using (route_id)
  where r.route_type in (0, 3))
select s.stop_id, any_value(s.stop_name) stop_name, any_value(s.stop_lat) lat, any_value(s.stop_lon) lon,
  case when count(distinct route_type) = 2 then "bus+tram" when max(route_type) = 0 then "tram" else "bus" end mode
from `hackaton-honory.warsaw_transport.gtfs_stops` s join served using (stop_id)
group by s.stop_id order by s.stop_id' > gtfs_stops.csv
gsutil -q cp gtfs_stops.csv $BUCKET/T03/input/
wc -l gtfs_stops.csv
