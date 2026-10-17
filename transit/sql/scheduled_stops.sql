-- Every scheduled stop call on @service_date of the tram and bus blocks (line + brigade) that have
-- at least one trip calling at an AOI stop between @window_start and @window_end. Whole blocks are
-- returned because the matcher assigns a vehicle's pings to the trips of its block in order;
-- serves_window marks the trips that get events, in_aoi the stops that get events.
WITH aoi AS (
  SELECT ST_GEOGFROMTEXT(@aoi_wkt) AS g
),
feed AS (
  SELECT MAX(feed_version) AS feed_version
  FROM `hackaton-honory.warsaw_transport.gtfs_calendar_dates`
  WHERE date = @service_date AND exception_type = 1
),
trips AS (
  SELECT t.feed_version, t.trip_id, r.route_short_name AS line,
         IF(r.route_type = 0, 'tram', 'bus') AS mode,
         LTRIM(t.block_short_name, '0') AS brigade,
         t.direction_id, t.trip_headsign, t.shape_id, t.variant_code
  FROM `hackaton-honory.warsaw_transport.gtfs_calendar_dates` c
  JOIN feed USING (feed_version)
  JOIN `hackaton-honory.warsaw_transport.gtfs_trips` t USING (feed_version, service_id)
  JOIN `hackaton-honory.warsaw_transport.gtfs_routes` r USING (feed_version, route_id)
  WHERE c.date = @service_date AND c.exception_type = 1 AND r.route_type IN (0, 3)
),
calls AS (
  SELECT tr.*, st.stop_sequence, st.stop_id, s.stop_name, s.stop_code, s.stop_lat, s.stop_lon,
         st.shape_dist_traveled * 1000 AS shape_dist_m,
         TIMESTAMP_ADD(TIMESTAMP(DATETIME(@service_date), 'Europe/Warsaw'), INTERVAL st.arrival_s SECOND) AS sched_arrival,
         TIMESTAMP_ADD(TIMESTAMP(DATETIME(@service_date), 'Europe/Warsaw'), INTERVAL st.departure_s SECOND) AS sched_departure,
         st.pickup_type, st.drop_off_type,
         ST_DWITHIN(s.location, aoi.g, @stop_buffer_m) AS in_aoi,
         ST_INTERSECTS(s.location, aoi.g) AS in_aoi_strict
  FROM trips tr
  JOIN `hackaton-honory.warsaw_transport.gtfs_stop_times` st USING (feed_version, trip_id)
  JOIN `hackaton-honory.warsaw_transport.gtfs_stops` s USING (feed_version, stop_id)
  CROSS JOIN aoi
),
window_trips AS (
  SELECT DISTINCT trip_id, mode, line, brigade FROM calls
  WHERE in_aoi AND sched_departure BETWEEN @window_start AND @window_end
)
SELECT calls.* EXCEPT (feed_version),
       calls.trip_id IN (SELECT trip_id FROM window_trips) AS serves_window
FROM calls
JOIN (SELECT DISTINCT mode, line, brigade FROM window_trips) USING (mode, line, brigade)
ORDER BY trip_id, stop_sequence
