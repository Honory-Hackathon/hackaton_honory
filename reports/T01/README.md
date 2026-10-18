# T01 report: LiDAR → 1 m urban geometry

**Status:** done (first pass), 2026-10-18. All rasters cover the full shared grid.

## What was produced

Everything is in `gs://hackaton-honory-heat/T01/`:

| Path | Content |
|---|---|
| `output/*.tif` | 11 COGs on the shared grid (below) |
| `output/README.md`, `output/layers.json` | Per-layer source, units, nodata |
| `output/summary.json` | Class histograms, shares, spot checks, inventory comparison |
| `output/previews/` | PNG previews (copies of the main ones are in `previews/` here) |
| `laz/2023/`, `laz/2025/` | All 538 raw LAZ sheets (269 per flight, 44.4 GiB), so nobody has to download them again |
| `tiles/` | Per-1 km intermediate npz files (one per tile) |
| `meta/` | AOI, halo and grid GeoJSON, `grid.json`, sheet lists with URLs, tree inventory extract |
| `code/` | The scripts and Dockerfile that produced all of this (mirrored in [`lidar/`](../../lidar/README.md)) |

**Grid** (matches the shared conventions): EPSG:2180, 1 m cells, bounds E 634000–642000, N 483000–492000, 8000 × 9000 px. The halo bbox is 634163–641922 / 483246–491255, snapped outward to whole kilometres. The AOI is PRG `146510_8` (15.56 km²); the halo is 37.4 km². Sheets were taken for the **whole grid**, not just the halo, so every cell has data.

| Layer | Type | Meaning |
|---|---|---|
| `dtm` | float32, m EVRF2007 | Ground. Built from 2025 class 2, then water surface, then 2023 ground; pyramid inpainting under buildings |
| `dsm` | float32, m EVRF2007 | Buildings + ground (no vegetation). Built from 2025 class 6 max within the building mask, DTM elsewhere |
| `building_mask` | uint8 0/1 | 2025 class 6 ≥ 2 m above ground. 3×3 closing, holes ≤ 25 m² filled, parts < 10 m² dropped |
| `water_mask` | uint8 0/1 | LiDAR water (class 9) grown into connected Sentinel-2 NDWI > 0 areas |
| `canopy_height` | float32, m above ground | 2023 class 5 max − DTM where ≥ 2.5 m; 0 = no canopy |
| `crown_base_height` | float32, m above ground | p10 of class-5 point heights per cell, 3×3 mean. NaN outside canopy |
| `crown_gap_fraction` | float32, 0–1 | Crown light-transmission estimate (see below). NaN outside canopy |
| `surface_type` | uint8 | 1 building, 2 water, 3 grass/low vegetation, 4 shrubs, 5 paved/bare, 6 other |
| `canopy_felled_2023_2025` | uint8 0/1 | 2023 canopy with no 2025 vegetation nearby (removed from canopy) |
| `density_2023`, `density_2025` | uint16 | Points per m², excluding noise and overlap classes |

## How it was run

- The work used Cloud Run Jobs, not a VM:
  - The download took about 4 minutes with 20 parallel tasks.
  - Processing used 72 tile tasks of 2 vCPU / 8 GiB each, spread over europe-central2, -west3, -west4 and -north1. The 20 vCPU / 40 GiB Cloud Run quota is per region. Each tile takes about 2–6 minutes; the whole processing step took about 15 minutes.
  - One mosaic task (8 vCPU / 32 GiB) took about 4 minutes.
- Each tile is processed with a 100 m buffer, and every layer is computed inside the tile. The mosaic then pastes the tile centres together.
- **Stack:** pip only (laspy+lazrs, numpy, scipy, rasterio, pyproj). The image is `europe-central2-docker.pkg.dev/hackaton-honory/hackathon/t01-lidar:2`; it pulls the script from `code/` when it starts.
- **Rerun:**
  - `gcloud run jobs execute t01-tiles` in each of the 4 regions (env `TILE_BASE` = 0 / 18 / 36 / 54, 18 tasks each).
  - Then `gcloud run jobs execute t01-mosaic --region=europe-central2`.

## Spot checks

| Place | Result | Reference |
|---|---|---|
| PKiN | 235.6 m above ground (DSM top 352.6 m, ground 116.9 m) | ≈ 237 m ✅ |
| Varso | 233.6 m above ground | Roof ≈ 230 m ✅. The spire (to 310 m) is **not** in class 6, so it is missing from the DSM. A thin spire's shadow is negligible |
| Aleje Ujazdowskie (20 m radius) | Canopy 39 %, median height 7.6 m (p95 15.7), crown base 4.9 m, gap fraction 0.16 | Both tree rows are clearly visible ([preview](previews/zoom_Aleje_Ujazdowskie_heights.png)) |
| Ogród Saski (100 m radius) | Canopy 65 %, median height 18.5 m (p95 24.1), crown base 10.4 m, gap fraction 0.014 | Dense old park ✅ |
| Plac Defilad (30 m radius) | Canopy 3 %, 84 % paved | Open plaza ✅ |
| Tree inventory (2010–13), 39,091 trees with height in the grid | 93 % have LiDAR canopy within the crown radius. r = 0.53; LiDAR − inventory median +2.9 m (MAD 2.6 m); 43 % within ±3 m | The positive bias fits 10–13 years of growth. Inventory heights are coarse and top out at about 22 m ([plot](previews/tree_inventory_vs_lidar.png)) |

Grid-wide shares:
- canopy 30.4 %
- buildings 20.0 %
- water 3.2 %
- surface types: grass 17 %, shrubs 8 %, paved/bare 50 %, other 1 %
- felled canopy 2023→2025: 0.24 km², about 1.1 % of the 2023 canopy

## What we learned about the data (corrections to the brief)

- **Water is class 9, not class 8.** Class 8 does not occur in either flight; class 9 has 11.1 M points in 2023 and 1.9 M in 2025.
- **Water coverage is still patchy.** Much of the Vistula surface gives no returns at all. Those areas were added with Sentinel-2 NDWI > 0 (2026-06-27, 0.05 % cloud, tile 34UDC), restricted to areas connected to LiDAR water. NDVI cannot do this: over the turbid river it is about the same as asphalt.
- **Class 12 (overlap) is large.** It is 36 % of all 2023 points (1.41 G of 3.95 G) and 27 % of 2025 (0.85 G of 3.20 G). Those points keep no semantic class, so class-based statistics only use the rest: median 20 (2023) and 17 (2025) classified points/m². That is plenty for 1 m cells.
- **Class 0 exists.** About 63 M points in 2023 and 50 M in 2025 were never classified (likely vehicles and other non-ground objects). Class 1 does not occur.
- **Bridges and flyovers are class 6.** They are therefore in `building_mask` and the DSM. That is right for shade (decks do cast shade), but T03 must treat bridge decks as walkable.
- **GUGiK rarely uses class 3 (low vegetation).** As a result, LiDAR alone labelled only 4 % of the grid as grass. Outside canopy, Sentinel-2 NDVI ≥ 0.45 was relabelled grass, and NDVI < 0.25 overrides LiDAR "vegetation" (it was probably cars). Under canopy, the ground type still comes from LiDAR only.

## Artefact handling (deliberately simple)

- **Cranes, scaffolding, glass roofs, arcades, covered walkways:** these are whatever GUGiK put in class 6, and they count as buildings (no separate handling, as agreed). Arcades and covered walkways appear as solid roof in the DSM.
- **Vehicles:** these are class 0 or 4. They are not in the DSM; on the ground they end up as paved, via NDVI.
- **Reconciling the two flights:**
  - Canopy is removed where a 2025 building stands, or where 2025 has no vegetation returns in a 5×5 window (trees felled).
  - New trees planted after 2023 are not added.
- **Vistula embankments:** these are handled as ground. The water surface is inpainted into the DTM where it has no returns.

## Crown transmission: what the number means

`crown_gap_fraction` is the share of 2023 first returns that come within 1 m of the ground. It is summed over the canopy cells in a 3×3 window. Where a cell had too few returns, the canopy median was used instead (7.5 k px).

Over the canopy:
- p10 0.00
- p25 0.00
- median 0.042
- p75 0.21
- p90 0.37

Crown interiors are close to 0. Edges, young trees and street trees are 0.1–0.4.

This is a **near-nadir laser gap fraction at 1064 nm**, not shortwave transmissivity:
- It includes gaps between crowns at the crown edge.
- Within a crown, leaf-on gaps scale roughly with the path length at the sun's elevation (more opaque at low sun).
- Scattering by leaves (especially in the near infrared) makes real shortwave transmissivity somewhat higher than the gap fraction.

The median of 4 % sits inside the literature range for leaf-on urban trees, about 3–10 % (Lindberg & Grimmond 2011; Konarska et al. 2014). **Suggestion for T02/T07:** use `clip(gap, 0.03, 0.5)` as the crown transmissivity, so dense crowns do not become fully opaque.

## Open issues

1. **Varso spire** is missing (class unknown, probably 0 or 7). It doesn't matter for shade.
2. **Crown base** is the 10th percentile of class-5 point heights. Trunk returns pull it down a little, and nothing validates it (the inventory has no crown base height). Ratio of crown base to top height: Saski 0.56, Ujazdowskie 0.64.
3. **Ground type under canopy** comes from LiDAR only, which is weak. Park ground under trees often shows up as paved.
4. **Bridges** are in the building mask (see above).
5. **The halo outside the AOI** went through the same processing; it was not checked separately.
