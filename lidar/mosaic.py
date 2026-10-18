"""T01 Cloud Run task: assemble tile npz files into full-grid COGs, previews and spot checks."""
import io
import json
import os

import matplotlib
import numpy as np
import rasterio
from google.cloud import storage
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BKT = storage.Client().bucket("hackaton-honory-heat")
OUT = "/tmp/out"
os.makedirs(f"{OUT}/previews", exist_ok=True)
grid = json.loads(BKT.blob("T01/meta/grid.json").download_as_text())
ge0, gn0, ge1, gn1 = grid["bounds"]
W, H = ge1 - ge0, gn1 - gn0
T = 1000
transform = from_origin(ge0, gn1, 1, 1)

LAYERS = {  # name: (dtype, nodata, units, description)
    "dtm": ("float32", np.nan, "m EVRF2007", "Ground elevation (2025 class 2 mean per cell; water surface; 2023 ground; pyramid inpainting under buildings)"),
    "dsm": ("float32", np.nan, "m EVRF2007", "Building surface: max of 2025 class 6 (>= 2 m above ground, cleaned mask) else DTM; no vegetation"),
    "building_mask": ("uint8", 255, "0/1", "2025 class 6 >= 2 m above ground, 3x3 closing, holes <= 25 m2 filled, components < 10 m2 dropped; cranes/structures included"),
    "water_mask": ("uint8", 255, "0/1", "Class 8/9 (water; data use 9) in 2023 or 2025, 5x5 closing, holes filled, components < 50 m2 dropped; plus Sentinel-2 2026-06-27 NDWI > 0 areas (non-building, no canopy, bare/no-return ground) connected to LiDAR water"),
    "canopy_height": ("float32", np.nan, "m above ground", "2023 class 5 max minus DTM where >= 2.5 m; 0 = no canopy. Removed where 2025 building or no 2025 vegetation in 5x5 window (felled)"),
    "crown_base_height": ("float32", np.nan, "m above ground", "10th percentile of 2023 class-5 point heights per cell (>= 4 pts), 3x3 mean; fallback 0.25 x canopy height; NaN outside canopy"),
    "crown_gap_fraction": ("float32", np.nan, "0-1", "LiDAR first-return gap fraction: share of 2023 first returns < 1 m above ground over canopy cells in 3x3 window; NaN outside canopy"),
    "surface_type": ("uint8", 0, "class", "1 building, 2 water, 3 grass/low vegetation, 4 shrubs, 5 paved/bare ground, 6 other/no low returns. LiDAR 2023 low-point classes in 3x3 window, refined outside canopy by Sentinel-2 NDVI 2026-06-27 (>= 0.45 -> grass, < 0.25 -> paved)"),
    "canopy_felled_2023_2025": ("uint8", 255, "0/1", "2023 canopy with no 2025 vegetation returns in 5x5 window (removed from canopy)"),
    "density_2023": ("uint16", 65535, "points/m2", "2023 points per cell excl. noise (7) and overlap (12)"),
    "density_2025": ("uint16", 65535, "points/m2", "2025 points per cell excl. noise (7) and overlap (12)"),
}

arrs = {k: np.full((H, W), nd, dt) for k, (dt, nd, _, _) in LAYERS.items()}
stats = []
for i in range((W // T) * (H // T)):
    e0, n0 = ge0 + (i % (W // T)) * T, gn0 + (i // (W // T)) * T
    z = np.load(io.BytesIO(BKT.blob(f"T01/tiles/{e0}_{n0}.npz").download_as_bytes()))
    r0, c0 = gn1 - (n0 + T), e0 - ge0
    for k in LAYERS:
        arrs[k][r0:r0 + T, c0:c0 + T] = z[k]
    stats.append(json.loads(BKT.blob(f"T01/tiles/{e0}_{n0}.json").download_as_text()))

canopy = arrs["canopy_height"] > 0

# Sentinel-2 L2A NDVI (2026-06-27, 0.05% cloud) refines LiDAR ground cover: GUGiK rarely uses
# class 3, so lawns look like bare ground. Not applied under canopy (NDVI sees the crowns).
S2 = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/34/U/DC/2026/6/S2C_34UDC_20260627_0_L2A"
bands = {}
for b in ("B03", "B04", "B08"):
    with rasterio.open(f"{S2}/{b}.tif") as src, WarpedVRT(src, crs="EPSG:2180", transform=transform, width=W, height=H,
                                                          resampling=Resampling.bilinear) as vrt:
        bands[b] = vrt.read(1).astype(np.float32)
with np.errstate(invalid="ignore", divide="ignore"):
    ndvi = (bands["B08"] - bands["B04"]) / (bands["B08"] + bands["B04"])
    ndwi = (bands["B03"] - bands["B08"]) / (bands["B03"] + bands["B08"])
del bands
st = arrs["surface_type"]
ground = (st >= 3) & ~canopy
lidar_surface_shares = {int(v): round(float((st == v).mean()), 4) for v in range(1, 7)}
st[ground & (ndvi >= 0.45) & (st != 4)] = 3
st[ground & (ndvi < 0.25) & ((st == 3) | (st == 4))] = 5
# Specular river surface often returns no class-9 points: grow LiDAR water into connected
# Sentinel-2 NDWI > 0 areas on bare/no-return ground (turbid Vistula has NDVI ~ asphalt).
from scipy import ndimage as ndi  # noqa: E402
cand = ndi.binary_closing((ndwi > 0) & ((st == 5) | (st == 6) | (st == 2)) & ~canopy, np.ones((5, 5), bool))
cand &= arrs["building_mask"] == 0
lab, nlab = ndi.label(cand | (arrs["water_mask"] == 1))
touch = np.zeros(nlab + 1, bool)
touch[np.unique(lab[arrs["water_mask"] == 1])] = True
touch[0] = False
water_s2 = touch[lab] & cand
water_lidar_share = round(float((arrs["water_mask"] == 1).mean()), 4)
arrs["water_mask"][water_s2] = 1
st[arrs["water_mask"] == 1] = 2
gap_med = float(np.nanmedian(arrs["crown_gap_fraction"][canopy]))
gaps_filled = int((canopy & np.isnan(arrs["crown_gap_fraction"])).sum())
arrs["crown_gap_fraction"][canopy & np.isnan(arrs["crown_gap_fraction"])] = gap_med

for k, (dt, nd, units, desc) in LAYERS.items():
    path = f"{OUT}/{k}.tif"
    with rasterio.open(path, "w", driver="COG", width=W, height=H, count=1, dtype=dt, crs="EPSG:2180",
                       transform=transform, nodata=nd, compress="deflate", predictor=3 if dt == "float32" else 2,
                       BIGTIFF="IF_SAFER") as dst:
        dst.write(arrs[k], 1)
        dst.update_tags(units=units, description=desc)

# Spot checks.
to2180 = Transformer.from_crs(4326, 2180, always_xy=True)
to2180_from2000 = Transformer.from_crs(2178, 2180, always_xy=True)


def window(lat, lon, rad):
    e, n = to2180.transform(lon, lat)
    c, r = int(e - ge0), int(gn1 - n)
    return np.s_[max(r - rad, 0):r + rad, max(c - rad, 0):c + rad]


hag = arrs["dsm"] - arrs["dtm"]
checks = {}
for name, lat, lon, rad, ref in [("PKiN", 52.23178, 21.00594, 60, "~237 m incl. spire"),
                                 ("Varso", 52.2287, 20.9992, 60, "~310 m incl. spire")]:
    s = window(lat, lon, rad)
    checks[name] = {"max_height_above_ground_m": round(float(np.nanmax(hag[s])), 1),
                    "max_dsm_m": round(float(np.nanmax(arrs["dsm"][s])), 1),
                    "ground_m": round(float(np.nanmedian(arrs["dtm"][s])), 1), "reference": ref}
for name, lat, lon, rad in [("Aleje Ujazdowskie", 52.2244, 21.0240, 20), ("Ogrod Saski", 52.2413, 21.0066, 100),
                            ("Plac Defilad", 52.2321, 21.0096, 30)]:
    s = window(lat, lon, rad)
    ch, st = arrs["canopy_height"][s], arrs["surface_type"][s]
    can = ch > 0
    checks[name] = {"canopy_share": round(float(can.mean()), 3),
                    "canopy_height_median_m": round(float(np.median(ch[can])), 1) if can.any() else None,
                    "canopy_height_p95_m": round(float(np.percentile(ch[can], 95)), 1) if can.any() else None,
                    "crown_base_median_m": round(float(np.nanmedian(arrs["crown_base_height"][s][can])), 1) if can.any() else None,
                    "gap_fraction_median": round(float(np.nanmedian(arrs["crown_gap_fraction"][s][can])), 3) if can.any() else None,
                    "surface_type_shares": {int(v): round(float((st == v).mean()), 3) for v in np.unique(st)}}

# Tree inventory (2010-13 measurements): max canopy height within crown radius vs inventory height.
inv = json.loads(BKT.blob("T01/meta/tree_inventory.json").download_as_text())
rows = []
for t in inv:
    if not t["height"] or t["height"] <= 0 or not t["x2000"] or not t["y2000"]:
        continue
    e, n = to2180_from2000.transform(t["x2000"], t["y2000"])  # easting 7xxxxxx, northing
    c, r = int(e - ge0), int(gn1 - n)
    rad = int(max(2, min(10, (t["crown_d"] or 4) / 2)))
    if not (rad <= r < H - rad and rad <= c < W - rad):
        continue
    ch = arrs["canopy_height"][r - rad:r + rad + 1, c - rad:c + rad + 1]
    rows.append((t["height"], float(ch.max()), t["district"] == "Śródmieście"))
inv_arr = np.array(rows, float)
found = inv_arr[:, 1] > 0
d = inv_arr[found, 1] - inv_arr[found, 0]
checks["tree_inventory"] = {
    "trees_in_grid_with_height": int(len(inv_arr)), "with_lidar_canopy_within_crown": int(found.sum()),
    "pearson_r": round(float(np.corrcoef(inv_arr[found, 0], inv_arr[found, 1])[0, 1]), 3),
    "median_lidar_minus_inventory_m": round(float(np.median(d)), 2),
    "mad_m": round(float(np.median(np.abs(d - np.median(d)))), 2),
    "share_abs_diff_le_3m": round(float((np.abs(d) <= 3).mean()), 3),
    "note": "inventory heights measured 2010-13 (growth/pruning since); LiDAR = max canopy height within crown radius"}
fig, ax = plt.subplots(figsize=(5, 5))
ax.hexbin(inv_arr[found, 0], inv_arr[found, 1], gridsize=50, bins="log", cmap="viridis", extent=(0, 40, 0, 40))
ax.plot([0, 40], [0, 40], "w--", lw=1)
ax.set_xlabel("inventory height 2010-13 [m]")
ax.set_ylabel("LiDAR 2023 canopy height [m]")
fig.tight_layout()
fig.savefig(f"{OUT}/previews/tree_inventory_vs_lidar.png", dpi=120)
plt.close(fig)

summary = {
    "grid": grid, "canopy_share_grid": round(float(canopy.mean()), 4),
    "building_share_grid": round(float((arrs["building_mask"] == 1).mean()), 4),
    "water_share_lidar_only": water_lidar_share,
    "water_share_grid": round(float((arrs["water_mask"] == 1).mean()), 4),
    "felled_px": int((arrs["canopy_felled_2023_2025"] == 1).sum()),
    "gap_fraction_canopy": {q: round(float(np.nanpercentile(arrs["crown_gap_fraction"][canopy], p)), 3)
                            for q, p in [("p10", 10), ("p25", 25), ("median", 50), ("p75", 75), ("p90", 90)]},
    "gap_fraction_filled_with_median_px": gaps_filled,
    "surface_type_shares_lidar_only": lidar_surface_shares,
    "surface_type_shares": {int(v): round(float((arrs["surface_type"] == v).mean()), 4) for v in range(1, 7)},
    "density_median": {"2023": float(np.median(arrs["density_2023"])), "2025": float(np.median(arrs["density_2025"]))},
    "class_hist_2023": np.sum([s["class_hist_2023"] for s in stats], 0).tolist(),
    "class_hist_2025": np.sum([s["class_hist_2025"] for s in stats], 0).tolist(),
    "tile_seconds_max": max(s["total_s"] for s in stats),
    "spot_checks": checks,
}
json.dump(summary, open(f"{OUT}/summary.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(summary, ensure_ascii=False))


def preview(name, a, cmap, vmin, vmax, step=8, crop=None, title=None):
    sub = a[crop] if crop else a[::step, ::step]
    fig, ax = plt.subplots(figsize=(8, 8 * sub.shape[0] / sub.shape[1]))
    im = ax.imshow(sub, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    ax.set_axis_off()
    ax.set_title(title or name)
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()
    fig.savefig(f"{OUT}/previews/{name}.png", dpi=110)
    plt.close(fig)


preview("dtm", arrs["dtm"], "terrain", 75, 125)
preview("height_above_ground", np.where(arrs["building_mask"] == 1, hag, np.nan), "magma", 0, 60)
preview("canopy_height", np.where(canopy, arrs["canopy_height"], np.nan), "Greens", 0, 30)
preview("ndvi_s2_20260627", ndvi, "RdYlGn", -0.2, 0.9)
preview("ndwi_s2_20260627", ndwi, "RdBu", -0.6, 0.4)
preview("crown_gap_fraction", arrs["crown_gap_fraction"], "viridis", 0, 0.6)
preview("surface_type", arrs["surface_type"].astype(float), matplotlib.colors.ListedColormap(
    ["#000000", "#c0392b", "#2e86de", "#7bc043", "#1e6b30", "#bdbdbd", "#f5e6c4"]), -0.5, 6.5)
for name, lat, lon, rad in [("PKiN_Varso", 52.2302, 21.0026, 450), ("Ogrod_Saski", 52.2413, 21.0066, 350),
                            ("Aleje_Ujazdowskie", 52.2215, 21.0247, 300)]:
    s = window(lat, lon, rad)
    preview(f"zoom_{name}_heights", np.where(canopy[s], arrs["canopy_height"][s], np.where(arrs["building_mask"][s] == 1, -hag[s] / 4, np.nan)),
            "PiYG", -30, 30, crop=np.s_[:, :], title=f"{name}: canopy (green, m) / buildings (pink, -h/4)")
    preview(f"zoom_{name}_gap", arrs["crown_gap_fraction"][s], "viridis", 0, 0.6, crop=np.s_[:, :])

for f in sorted(os.listdir(OUT)):
    if f.endswith((".tif", ".json")):
        BKT.blob(f"T01/output/{f}").upload_from_filename(f"{OUT}/{f}")
for f in sorted(os.listdir(f"{OUT}/previews")):
    BKT.blob(f"T01/output/previews/{f}").upload_from_filename(f"{OUT}/previews/{f}")
BKT.blob("T01/output/layers.json").upload_from_string(json.dumps(
    {k: {"dtype": dt, "nodata": None if isinstance(nd, float) else nd, "units": u, "description": d}
     for k, (dt, nd, u, d) in LAYERS.items()}, indent=1))
