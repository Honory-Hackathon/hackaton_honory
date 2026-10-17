-- Shape points (distances in metres) of the given shapes, from the feed in force on @service_date.
SELECT shape_id, shape_pt_sequence, shape_pt_lat, shape_pt_lon, shape_dist_traveled * 1000 AS dist_m
FROM `hackaton-honory.warsaw_transport.gtfs_shapes`
WHERE feed_version = (
    SELECT MAX(feed_version) FROM `hackaton-honory.warsaw_transport.gtfs_calendar_dates`
    WHERE date = @service_date AND exception_type = 1)
  AND shape_id IN UNNEST(@shape_ids)
ORDER BY shape_id, shape_pt_sequence
