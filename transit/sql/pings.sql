-- Fresh tram/bus position reports near the AOI, one row per physical report: the old and new API
-- copies and the repeated polls of the same report share (vehicle_type, vehicle_number, reported_at).
WITH aoi AS (
  SELECT ST_BUFFER(ST_GEOGFROMTEXT(@aoi_wkt), @ping_buffer_m) AS g
)
SELECT vehicle_type AS mode, vehicle_number, line, LTRIM(TRIM(brigade), '0') AS brigade, reported_at, lat, lon
FROM `hackaton-honory.warsaw_transport.vehicle_positions`, aoi
WHERE fetched_at BETWEEN TIMESTAMP_SUB(@window_start, INTERVAL 120 MINUTE)
                     AND TIMESTAMP_ADD(@window_end, INTERVAL 120 MINUTE)
  AND reported_at BETWEEN TIMESTAMP_SUB(@window_start, INTERVAL 90 MINUTE)
                      AND TIMESTAMP_ADD(@window_end, INTERVAL 90 MINUTE)
  AND vehicle_type IN ('tram', 'bus')
  AND report_age_s BETWEEN 0 AND @max_report_age_s
  AND line IS NOT NULL AND brigade IS NOT NULL
  AND ST_INTERSECTS(location, aoi.g)
QUALIFY ROW_NUMBER() OVER (PARTITION BY vehicle_type, vehicle_number, reported_at ORDER BY fetched_at, api) = 1
