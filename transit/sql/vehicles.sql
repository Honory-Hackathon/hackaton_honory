-- Fleet register row per (mode, vehicle_number) and its model, from the snapshot closest to the
-- service day: the latest one taken before the day ends, else the earliest one after.
WITH day_end AS (
  SELECT TIMESTAMP(DATETIME(DATE_ADD(@service_date, INTERVAL 1 DAY)), 'Europe/Warsaw') AS t
),
veh AS (
  SELECT v.mode, v.vehicle_number, v.model_id, v.production_year, v.depot, v.snapshot_at
  FROM `hackaton-honory.warsaw_transport.ztm_vehicles` v, day_end
  WHERE v.mode IN ('tram', 'bus')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY v.mode, v.vehicle_number
    ORDER BY v.snapshot_at <= day_end.t DESC, ABS(TIMESTAMP_DIFF(v.snapshot_at, day_end.t, SECOND))) = 1
),
models AS (
  SELECT m.mode, m.model_id, m.brand, m.type AS model_type, m.air_conditioning
  FROM `hackaton-honory.warsaw_transport.ztm_vehicle_models` m, day_end
  WHERE m.mode IN ('tram', 'bus')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY m.mode, m.model_id
    ORDER BY m.snapshot_at <= day_end.t DESC, ABS(TIMESTAMP_DIFF(m.snapshot_at, day_end.t, SECOND))) = 1
)
SELECT veh.mode, veh.vehicle_number, veh.model_id, veh.production_year, veh.depot,
       models.brand, models.model_type, models.air_conditioning
FROM veh LEFT JOIN models USING (mode, model_id)
