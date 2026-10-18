"""Merge parts/*.json into T12/manifest.json and per-folder READMEs."""
import json
import time

from common import (BUCKET, OUT_PREFIX, STEP_LABELS, STEP_UTC, STEPS, gcs, log, upload_bytes, upload_json)

UTCI_CATEGORIES = [
    {"label": "no thermal stress", "label_pl": "brak stresu cieplnego", "max": 26, "color": "#abd9e9"},
    {"label": "moderate heat stress", "label_pl": "umiarkowany stres cieplny", "min": 26, "max": 32, "color": "#fee090"},
    {"label": "strong heat stress", "label_pl": "silny stres cieplny", "min": 32, "max": 38, "color": "#f46d43"},
    {"label": "very strong heat stress", "label_pl": "bardzo silny stres cieplny", "min": 38, "max": 46,
     "color": "#d73027"},
    {"label": "extreme heat stress", "label_pl": "ekstremalny stres cieplny", "min": 46, "color": "#6e0046"},
]

CAPTIONS = {
    "cool_points": ("Punkty wody i chłodu: poidełka, fontanny, kurtyny wodne, parki, ławki w cieniu, chłodne wnętrza.",
                    "Cool and water points: drinking fountains, fountains, water curtains, parks, shaded benches, cool "
                    "indoor places."),
    "shadow": ("Cień budynków (granat) i drzew (zieleń) w danej chwili, SOLWEIG.",
               "Shade of buildings (navy) and trees (green) at this moment, SOLWEIG."),
    "utci": ("Temperatura odczuwalna UTCI na wysokości przechodnia (bez dachów).",
             "Felt temperature UTCI at pedestrian height (roofs masked)."),
    "tmrt": ("Średnia temperatura promieniowania Tmrt: ile ciepła dostaje ciało ze słońca i nagrzanych powierzchni.",
             "Mean radiant temperature Tmrt: heat the body receives from sun and warm surfaces."),
    "albedo": ("Albedo: jaką część światła słonecznego odbija powierzchnia (Sentinel-2, 27.06.2026).",
               "Albedo: share of sunlight a surface reflects (Sentinel-2, 2026-06-27)."),
    "lst_1125": ("Temperatura powierzchni z satelity Landsat, 25.06.2026, 11:25.",
                 "Land surface temperature from Landsat, 2026-06-25 at 11:25."),
}


def _part(name):
    b = gcs().blob(f"{OUT_PREFIX}/parts/{name}.json")
    return json.loads(b.download_as_text()) if b.exists() else None


def build():
    pts = _part("points")
    scene = _part("scene")
    f_t02 = _part("frames_t02")
    f_sol = _part("frames_solweig")
    f_sta = _part("frames_static")
    m = {
        "title": "T12 visual layers, Śródmieście heat day 2026-06-28",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_gs": f"gs://{BUCKET}/{OUT_PREFIX}/",
        "base_note": "All paths are relative to base_gs. The bucket is private: copy T12/ into the views image "
                     "(gsutil -m cp -r gs://hackaton-honory-heat/T12 .) or serve through the service. *.geojson "
                     "objects are stored gzip-encoded; gsutil and HTTP clients decompress them transparently.",
        "time": {"steps": STEPS, "labels_local": STEP_LABELS, "ts_utc": STEP_UTC,
                 "note": "step i = 06:00 CEST + 30 min * i; steps 30-32 (21:00-22:00) sun below the horizon"},
        "utci_categories": UTCI_CATEGORIES,
        "captions_pl_en": CAPTIONS,
        "views": {},
    }
    v1 = {"description": "Cool and water points over the walking-route heat map (route segments: warsaw_heat."
                         "t08_segment_heat + t03 segments, from T08).",
          "layers": {}}
    if pts:
        v1["layers"]["cool_points"] = {
            "path": "points/cool_points.geojson", "format": "GeoJSON points EPSG:4326",
            "geoparquet": "points/cool_points.parquet", "bigquery": "hackaton-honory.warsaw_heat.t12_cool_points",
            "counts": pts["points"],
            "properties": {
                "category": "drinking_water | fountain | water_curtain_or_splash | toilet | bench | shelter | park | "
                            "water_body | cool_indoor",
                "subtype": "e.g. mpwik_fountain, church_or_temple, library, shopping_mall, museum, garden, pond",
                "shade_share": "array[33] share of 3x3 m around the point in shade per step (SOLWEIG where "
                               "covered, else T02); null at night",
                "shade_share_t02": "array[33] same from T02 shade shares",
                "utci": "array[33] UTCI °C at the point (SOLWEIG, 3x3 m mean), null if not covered",
                "tmrt": "array[33] Tmrt °C (SOLWEIG)",
                "shaded_rest": "bench/shelter with mean shade >= 0.5 over 12:00-15:00",
                "shade_hours_10_18": "hours in shade (share >= 0.5) between 10:00 and 18:00",
                "in_aoi": "inside Śródmieście (else within 300 m of it)",
                "area_m2": "polygon area for parks / water / buildings (0 for points)"},
            "source": "OpenStreetMap (Geofabrik Mazowieckie 2026-10-16). City API (dane.um.warszawa.pl toilets, "
                      "MPWiK poidełka) needs a token / was unavailable (HTTP 500 'Service unavailable').",
        }
        v1["layers"]["cool_areas"] = {"path": "points/cool_areas.geojson",
                                      "format": "GeoJSON polygons EPSG:4326 (parks, water, cool indoor buildings)"}
    m["views"]["routes_cool_points"] = v1
    v2 = {"description": "3D view of buildings and trees with shadows moving with the sun, and cloud cover.",
          "layers": {}}
    if scene:
        v2["layers"].update(scene)
    for key, part in (("shadow_solweig", f_sol), ("shadow_t02", f_t02)):
        if part and key in part:
            v2["layers"][key] = dict(part[key], format="PNG frames EPSG:3857, paletted with alpha, one per step",
                                     encoding="navy = building/terrain shade, green = tree-only shade; alpha "
                                              "grows with shade share in the 2 m cell; transparent = sun",
                                     placement="MapLibre image source 'coordinates' or deck.gl BitmapLayer "
                                               "bounds [W,S,E,N] from frame")
    v2["recommended_shadow_layer"] = "shadow_solweig" if (f_sol and "shadow_solweig" in f_sol) else "shadow_t02"
    m["views"]["shadows_3d"] = v2
    v3 = {"description": "Temperature and albedo through the day: maps per step and a per-surface series.",
          "layers": {}}
    for part in (f_sol, f_sta):
        if part:
            for k in ("utci", "tmrt", "albedo", "lst_1125"):
                if k in part:
                    v3["layers"][k] = part[k]
            if "series" in part:
                v3["layers"]["series"] = {
                    "path": part["series"], "format": "JSON",
                    "content": "forcing per step (air temperature, humidity, wind, GHI/DNI/DHI, sun, clouds); "
                               "by_surface: per step mean UTCI, Tmrt, sun share, estimated incoming and reflected "
                               "shortwave (k_in_est = sun*DNI*sin(el) + DHI*SVF; k_up_est = albedo*k_in_est) for "
                               "paved, grass, shrubs, water, under_trees, other, all_ground (AOI, no roofs); "
                               "by_zone: UTCI/Tmrt in sun vs shade; static_by_surface: albedo and Landsat LST 11:25 "
                               "per surface type"}
    m["views"]["temperature_albedo"] = v3
    tot = 0
    for b in gcs().client.list_blobs(BUCKET, prefix=f"{OUT_PREFIX}/"):
        tot += b.size or 0
    m["total_bytes_in_T12"] = tot
    upload_json(m, f"{OUT_PREFIX}/manifest.json")
    upload_bytes(README.encode(), f"{OUT_PREFIX}/README.md", "text/markdown")
    log("manifest written", tot)


README = """# T12 visual layers (Śródmieście, heat day 2026-06-28)

Produced by `layers/` in the repo (report `reports/T12/README.md`). **Start from `manifest.json`**: it lists every
layer, its path (relative to this folder), format, frame bounds, legend, and step meaning.

- `points/` cool and water points (GeoJSON + GeoParquet; BigQuery `warsaw_heat.t12_cool_points`) and park/water polygons.
  Source OSM 2026-10-16. Per point: shade share, UTCI and Tmrt per step (arrays of 33).
- `scene/` 3D assets: `buildings.geojson` (LiDAR 2025 building blocks, 3 m height steps, `h` m above ground),
  `trees.geojson` (LiDAR 2023 treetops with crown base and radius), `sun_cloud.json` (sun vector + clouds per step).
- `frames/<layer>/NN.png` per-step images in EPSG:3857, NN = step 00..32 (06:00..22:00 CEST every 30 min).
  `shadow_solweig` / `shadow_t02` 2 m, `utci` / `tmrt` 4 m colour frames, `*_value` grey frames for read-out
  (value = vmin + (gray - 0.5) * (vmax - vmin) / 255, gray 0 = nodata). `albedo.png`, `lst_1125.png` 10 m.
- `rasters/` 10 m COGs in EPSG:2180 (albedo, LST).
- `series/district_series.json` per-surface-type series through the day.

CRS: web frames EPSG:3857 with lon/lat corners in the manifest; vectors EPSG:4326. Nodata: transparent / null.
"""

if __name__ == "__main__":
    build()
