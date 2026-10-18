# shade: T02 shade engine

Per-pixel direct-sun shade on a 1 m grid, split into building/terrain shade and tree shade, for the 33 time steps of 2026-06-28. Method and results: root [`README.md`](../README.md).

## Interface

```python
from shade import engine
from shade.sun import sun_positions

geom = engine.Geometry.from_layers(dtm, dsm, canopy_height, crown_base, transmission, res=1.0,
                                   far_dsm=far, far_offset=(row, col))   # far casters, optional
for local, row in sun_positions().iterrows():          # 33 steps, azimuth/elevation for 52.23 N 21.01 E
    solid, tau = engine.shade_step(geom, row.azimuth, row.elevation)
    shares = engine.shares(solid, tau)                  # f_sun, f_bldg, f_tree, f_both, summing to 1
svf_solid, svf_total = engine.sky_view_factor(geom)
```

Inputs are T01's layers on the shared grid: `dtm` ground (m), `dsm` buildings + ground (m), `canopy_height` and `crown_base` above ground (m), `transmission` vertical crown light transmission 0–1. NaN means "absent" in the canopy layers and in `dsm` off buildings. `far_dsm` is the ground + buildings surface over the wider caster extent (same cells; the grid's top-left cell at `far_offset`).

## Method

- **Sun:** pvlib (NREL SPA), refracted elevation, 25 °C. Matches the conventions: 12.5° at 06:00, 61.0° at 13:00, 3.1° at 20:30, below the horizon from 21:00 (−0.14°).
- **Buildings and terrain (`solid`):** opaque. The grid is reoriented so the shadow direction runs down the rows, then one sweep carries, for every digital ray, the height of the highest shadow line (`h ← max(h − Δs·tan e, z)`). A cell is shaded when that line is above it. This is exact for the digitised ray, costs O(pixels) per step whatever the shadow length, and reaches casters anywhere upstream. Shadow length is never capped: with `far_dsm` the sweep runs over the whole caster extent and is cropped to the grid afterwards.
- **Trees:** crowns are slabs between `dtm + crown_base` and `dtm + canopy_height`. Each receiver marches 1 m steps towards the sun and adds optical depth `μ · Δs / cos e` wherever the ray is inside a crown, with `μ = −ln(transmission) / crown depth` (Beer–Lambert, calibrated so a vertical ray through the crown gets the measured transmission). Low sun therefore passes under crowns, and long oblique paths through a crown attenuate more than vertical ones. The march stops when the ray rises above the highest crown in the grid.
- **Shares:** with `B` = solid shade (0/1) and `τ` = tree transmission:

  | | f_sun | f_bldg | f_tree | f_both |
  |---|---|---|---|---|
  | B = 0 | τ | 0 | 1 − τ | 0 |
  | B = 1 | 0 | τ | 0 | 1 − τ |

  Transmission enters as a fractional split: the beam that passes the crowns counts as sun (or as building shade when a building also blocks it). B and τ can be recovered: `B = f_bldg + f_both`, `τ = f_sun + f_bldg`. Light through crowns may then be counted twice if the thermal model's tree-zone factor also represents it; this is recorded in spec §13 (points to settle).
- **Terrain shade counts as `f_bldg`:** the "solid" layer is ground + buildings. On the dev DTM, micro-relief (curbs, ramps, walls) shades 1–2 % of cells at sun below 20°.
- **Sky view factor:** 32 azimuths × 8 elevation bands. A patch is visible when its central ray reaches the pixel. Weights are `(sin² e_hi − sin² e_lo) / 32` (cosine-weighted solid angle). Band 1 is buildings + terrain, band 2 also applies crown transmission.
- **Receiver height:** default 0 m (shade on the surface, comparable with orthophotos). `--receiver-height 1.1` evaluates at body height instead.
- **Caster extent:** the grid's 1 km halo only covers sun above ~13° for a 237 m tower. The caster extent (13 × 11 km, E 630,000–643,000, N 483,000–494,000) covers the real shadow of a 310 m tower at every sunlit step (5.7 km at 20:30), so the full shade is computed when `--far-dsm` is given. Without it, casters outside the grid are missing.
- **Point sun:** the sun is treated as a point. The real disc (0.53°) means the full shadow of an object of width w ends after about 107·w: for a 40 m wide tower that is ~4 km, so building shadows are real at these lengths, but a 2 m spire's shadow turns into penumbra after ~200 m. Thin spires are therefore slightly overstated far away; the area involved is negligible.

## Production run

```bash
pip install -r shade/requirements.txt
python -m shade.run IN_DIR OUT_DIR --far-dsm FAR.tif
# IN_DIR: dtm.tif dsm.tif canopy_height.tif crown_base.tif transmission.tif on the shared grid
# FAR.tif: ground + buildings surface over the caster extent (E 630000-643000, N 483000-494000)
```

Outputs, all COG on the input grid:
- `f_sun.tif` `f_bldg.tif` `f_tree.tif` `f_both.tif`: uint8, 33 bands (one per time step, band description = local time, UTC, sun azimuth/elevation). Share = value / 250, and the four always sum to exactly 250. 255 = sun below the horizon (21:00–22:00), where the shares are undefined and the thermal model should drop direct shortwave.
- `svf.tif`: float32, 2 bands as above.
- `timings.json` (runtime and peak memory per step) and `preview_0700.png`, `preview_1300.png`, `preview_1900.png`.

## Far-caster surface

- `far_dsm.py`: the far-caster surface over the caster extent from 2025 LAZ sheets, with the same rule as T01's `dsm` (max of class 6, else mean of class 2), left empty inside the shared grid.
