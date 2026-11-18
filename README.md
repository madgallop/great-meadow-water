# Great Meadow 3D water visualization — working folder

Earth Engine builds the data, Python prepares it, a static MapLibre page (`viewer/`) shows it.
No Earth Engine or Python is needed to *view* the result, so only `viewer/` needs to be published
(e.g. GitHub Pages). Run Python from this folder (`viz`), conda env `greatmeadow`.
View locally: `cd viewer && python -m http.server`, then open http://localhost:8000.

## Run order
1. `python/01_clean_canals_intakes.py` (standard library only)
   `python python/01_clean_canals_intakes.py --in-dir <EarthEngines/IntakesAndCanals> --out-dir data/clean`
   -> cleaned layers, `cleaning_report.json`, `rejected_features.csv`; upload `data/clean/for_gee_upload/*.geojson`
   as assets `canals_clean` and `pumping_stations` (paths in `gee/04` assume `projects/earthenginesgallop/assets/...`).
2. Earth Engine (`gee/`), exporting to `data/gee_outputs/`:
   - 01 DEM and reservoir footprint (check the printed area); 02 period composites (summer 2022, summer 2026)
   - 03 drainage water frames (pre-breach + two-month water-class frames); 04 canal status and
     canal-to-reservoir distance (re-run step 1 with `--reservoir-dist-csv` to replace the dam-distance fallback)
   - 05 June 2023 frames; 06 period water masks; 07-09 false-colour/shortwave previews (experiments)
   - 10 muted land/water; 11 map-palette preview and export (the c.1775-map colours baked into satellite tiles);
     12 `period2_pale_ag` (same as 11 but crop plots stay pale, for the pale-to-dark fade)
3. Tiles (`python/02_build_tiles.py`, wraps gdal2tiles; flags `--src --out --zmin --zmax`, plus
   `--clean-edges --black-max --trim` for scanned maps, `--keep-alpha` for RGBA, `--resampling`):
   - Period 2 and 3 imagery -> `viewer/tiles/styled/mappalette/period2`, `.../period3`
   - Pale crops (gee/12 output) -> `.../period2_pale`
   - Land-only Period 2: `python python/13_p2_land_only.py`, then
     `python python/02_build_tiles.py --src data/derived/period2_land_only.tif --out viewer/tiles/styled/mappalette/period2_land --zmin 7 --zmax 12 --keep-alpha`
   - Period 3 observed frames: `python/03_build_frame_tiles.py` (recolour with `09_recolour_frame_tiles.py`) -> `viewer/tiles/frames`
4. Period 1 map: `python/08_georeference_map.py` (c.1775 map, hand control points) -> `data/experimental/georef_1775_map.tif`,
   tiled with `--clean-edges --black-max 60 --trim 4` into `viewer/tiles/period1_map1775`.
5. Canals and intakes: `05_prepare_manual_network.py`, `06_merge_status.py` -> `viewer/data/canals_manual.geojson`,
   `pumps_manual.geojson` (and `canals.geojson`, `intake_stations.geojson`).
6. Bathymetry fill (Period 2 reservoir): `python/11_bathymetry_window.py` windows a model grid to
   `data/bathymetry/<grid>_window.tif`; then `python/12_reservoir_levels.py --grid dniep_topo_ndh_bathyadj_low_wide`
   (priority flood from a seed upstream of the dam; 48 frames cut by equal cell counts, smoothed)
   -> `viewer/data/reservoir_levels.geojson`.
7. Optional/parked: `python/14_terrain_p3.py` (merged DEM with modelled bed -> `viewer/tiles/terrain_p3`) is disabled in the
   viewer (`USE_DEM3 = false`): the merged DEM only covers the study area, which caused cliff walls and dark edge lines.
   Fixing it needs the DEM padded to the full map extent. `python/04_swot_frames.py` / `07_stylize_tiles.py` /
   `10_reservoir_fill_strips.py` are earlier experiments, not used by the viewer.

## Conventions carried over from class
Numbered sections (SETTINGS / LOAD / DISPLAY / EXPORT), CloudScore+ + SCL masking, the three-class
water scheme from the animation script, EPSG:32636 (the class DEM default, 32635, is the wrong zone here),
cloud-optimized GeoTIFF exports.

## Honesty rules built in
- Canal order is a display sequence (hierarchy, then distance), NOT dates. `date_basis` stays "none"
  until you supply `canal_dates.csv` (columns in the script 01 docstring).
- Culverts/tunnels are flagged `observable = false`; canals under ~30 m wide are sub-pixel at 10 m, so
  `reliable` in `canal_status` only holds for tagged widths >= 30 m (only 16 segments carry a width tag).
- Period 1 has no satellite imagery; it is the georeferenced c.1775 map, labelled as a reconstruction.
- Crossfades between observed frames are visual transitions, not interpolated observations.

## model_data (bathymetry) — what I found from the headers
- 4 bathymetry grids, 18553 x 10849 cells, 30 m cellsize, ~1.5 GB each as ASCII.
- CRS is Mercator (WGS84, metres), not UTM: "30 m" is 30 Mercator metres, roughly 20 m on the ground at this
  latitude. Reproject to EPSG:32636 before combining with the Sentinel-2/DEM layers.
- Extent is about lon 31.0-36.0, lat 45.8-47.81, which stops just short of the reservoir's northern tip
  (the reservoir extends to ~47.86). Check coverage before relying on it.
- Convert once to compressed GeoTIFF (e.g. `gdal_translate -of COG -co COMPRESS=DEFLATE`), and confirm the
  vertical datum against the README's 17.42 m reservoir surface. These grids are interpolated bed
  reconstructions, so label them as modelled, not surveyed.

## Bathymetry fill (how Period 2 fills)
The four 30 m grids (Lehnigk et al. SWOT/GeoClaw study) are in Mercator on the WGS84 spheroid, i.e. EPSG:3395
(not 3857; reading them as 3857 shifts everything ~30 km). Reservoir surface 17.42 m; bed minimum is about -11.5 m
(GLOBathy bed) or -5.6 m (`low_wide`, river-like). The fill floods the footprint from the old channel upward in
equal-cell-count steps. It is a display sequence over a *modelled* bed, not surveyed bathymetry or the 1956 history;
colour is styling.

## Viewer notes (moved out of the on-screen panel)
- Prototype. The basemap is a placeholder (Esri World Imagery) under the period tiles. Canals and intakes are
  hand-traced; their order is distance from the reservoir, not a construction chronology.
- Period 2 reservoir fill is a modelled rising water level over a reconstructed bed (see Bathymetry fill below),
  not the 1956 chronology. No imagery exists before the fill, so the not-yet-flooded valley is a pale tint
  that shrinks over the real 2022 imagery. Sequence: reservoir fills, then canals extend, then canals and intakes fill (display order by
  distance from the reservoir, not dates).
- Period 3 drain is the reverse of the fill order at the same pace; only the summer-2026 end state is measured.
- Canals and intakes: all are assumed operating before the breach (World Bank 2019 pump list); only the
  "after" is measured. Water within 500 m does not mean an intake can draw from it.
- Period 1: no satellite imagery exists for this period. It is a c.1775 map, georeferenced by hand: a
  reconstruction, with positions approximate. Faint modern imagery underneath is only for place context.
- Period 3 observed-water frames: Sentinel-2 two-month composites (Jul 2023 to Dec 2025), water classes drawn
  over summer 2026 imagery. Each position is an observation; the fade between observations is visual only.
  The SWOT daily-pass option is commented out in app.js (FRAME_SETS).
- Hidden for now (commented out in index.html): the timeline sliders and notes. The palette/basemap switching and
  the old west-to-east "strips" fill were removed from app.js; python/10 is kept only as a superseded fallback.
