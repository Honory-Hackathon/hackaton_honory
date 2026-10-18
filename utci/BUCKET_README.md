# T07: UTCI and Tmrt on walkable pixels, 2026-06-28

Produced by `utci/field.py` in the repo (report: `reports/T07/README.md`), Cloud Run Job `t07-final` (europe-west4), 2026-10-18.

**Source:** SOLWEIG 0.1.0b96 district run (T06): the 27 tile cores in
`gs://hackaton-honory-heat/T06/district/tiles/{e0}_{n0}/{tmrt,utci,shadow}.tif` (1 km cores, halo 500 m or 1000 m).
Values are copied from the tiles unchanged; only the mask is applied. Same numbers as the T06 mosaic
`T06/district/{tmrt,utci,shadow}_20260628.tif`.

**Grid:** EPSG:2180, 1 m, E 634000–642000, N 483000–492000 (8000 × 9000), identical to T01/T02/T03.

**Mask:** T03 `walkable.tif` > 0 (categories 1–8). Everything else is nodata. 2,230,894 of 2,231,803 walkable pixels
have values; the other 909 lie outside the 27 SOLWEIG tiles (all outside the AOI, in the mask's 50 m fringe) and are
nodata (15 T03 segments have no pixel left; see `checks/checks.json` → `coverage`).
Indoor and underground segments have no pixels (T03 keeps them out of the mask); T08 handles them.

**Bands:** 33 = time steps 2026-06-28 06:00–22:00 CEST every 30 min (04:00Z–20:00Z). Band descriptions are copied
from T02 (`2026-06-28T06:00:00+02:00 (04:00Z) az=68.0 el=12.58`). Bands 31–33 (21:00–22:00) have the sun below the horizon.

| File | Content |
|---|---|
| `utci.tif` | float32 COG, UTCI °C, nodata NaN. pythermalcomfort UTCI from SOLWEIG Tmrt, T05 `ta_c`, `rh_pct`, `wind_10m_utci_ms` = max(0.5, 10 m wind). **Hand-over to T08.** |
| `tmrt.tif` | float32 COG, mean radiant temperature °C (standing person, isotropic sky, default surfaces), nodata NaN |
| `sun_solweig.tif` | uint8 COG, SOLWEIG `shadow` × 250: share of the direct beam reaching the pixel (250 sun, 0 building/terrain shade, ≈8 under tree crowns, transmissivity 0.03). 255 = nodata (not walkable, or sun below horizon) |
| `checks/checks.json` | Coverage, tile seams, far casters vs T02, pilot consistency, sanity per step |
| `checks/previews_8m.npz` | 8 m block means used for the report figures |
| `checks/run_{0,1,2}.json` | Tiles used, runtime per task (0 = checks + utci, 1 = tmrt, 2 = sun_solweig) |
| `_SUCCESS` | Written once the final rasters (all 27 tiles) and this README are uploaded |

**Known gaps** (details in the report): far shadows of towers beyond a tile's halo are missing at low sun; no evening
heat release from pavement (Tmrt ≈ 1–2 K below air temperature after sunset); no street-canyon wind reduction; air
temperature is a park-station (cool-side) series.
