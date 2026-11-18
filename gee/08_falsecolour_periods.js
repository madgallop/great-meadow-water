// ============================================================
// 08 · FALSE-COLOUR PERIOD IMAGES WITH ELEVATION TINT + HILLSHADE
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Exports one 8-bit RGB GeoTIFF per period, same grid as script 02:
//   Base       Sentinel-2 "Agriculture" false colour (SWIR1-NIR-Blue).
//              Water stays its natural dark blue (real pixels).
//   Hillshade  strong, exaggerated relief shading on LAND only
//   Elevation  a soft colour tint by height on LAND only
// Water is left untouched, so it never gets shaded or tinted.
//
// HONEST LIMIT: the Copernicus DEM records the reservoir as a flat water
// surface, so the exposed Period 3 lake bed has no relief here. Hillshade
// and tint show the valley walls, terraces and plateau. The bed itself needs
// the bathymetry model (later step).
// Colour is styling. Brightness does not encode a measurement.
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);
var OUTPUT_CRS = 'EPSG:32636';
var EXPORT_SCALE = 20;

var PERIODS = {
  period2_reservoir: {start: '2022-06-01', end: '2022-10-01', label: 'Reservoir era - summer 2022'},
  period3_postdam:   {start: '2026-06-01', end: '2026-10-01', label: 'Post-dam - summer 2026'}
};

var STRETCH = {bands: ['B11', 'B8', 'B2'], min: [300, 300, 150], max: [4500, 5000, 2200], gamma: 1.1};

// Relief look. Tune these and re-run the preview.
var VERT_EXAG = 3;        // vertical exaggeration for shading only (1 = none)
var SUN_AZIMUTH = 315;    // degrees
var SUN_ELEV = 30;        // lower sun = longer shadows
var HILL_STRENGTH = 0;    // 0 = no hillshade, 1 = normal, >1 = stronger (was 1.2, too strong)
var TINT_OPACITY = 0;     // 0 = no elevation tint (was 0.25)
var ELEV_MIN = 0;         // metres, bottom of tint ramp
var ELEV_MAX = 130;       // metres, top of tint ramp
var TINT_PALETTE = ['2a1a7a', '2f64d6', '35c9c0', 'f2e86a', 'ffffff'];

Map.centerObject(ee.Geometry.Point([34.0, 47.2]), 10);
Map.setOptions('SATELLITE');

// ------------------------------------------------------------
// 2. SENTINEL-2 COMPOSITES (class cloud masking)
// ------------------------------------------------------------
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(aoi)
  .filterDate('2022-01-01', '2026-11-01')
  .linkCollection(
    ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'),
    ['cs_cdf']
  )
  .map(function(image) {
    var scl = image.select('SCL');
    var clear = image.select('cs_cdf').gte(0.50)
      .and(scl.neq(3)).and(scl.neq(8)).and(scl.neq(9))
      .and(scl.neq(10)).and(scl.neq(11));
    return image.updateMask(clear)
      .select(['B2', 'B3', 'B4', 'B8', 'B11'])
      .toFloat()
      .copyProperties(image, ['system:time_start']);
  });

var empty = ee.Image.constant([0, 0, 0, 0, 0])
  .rename(['B2', 'B3', 'B4', 'B8', 'B11'])
  .toFloat()
  .updateMask(ee.Image.constant(0));

function composite(start, end) {
  return ee.ImageCollection([empty])
    .merge(s2.filterDate(start, end))
    .median()
    .clip(aoi);
}

// ------------------------------------------------------------
// 3. TERRAIN: relief shading factor and elevation tint
// ------------------------------------------------------------
var demCol = ee.ImageCollection('COPERNICUS/DEM/GLO30_2024_1').filterBounds(aoi).select('DEM');
// A mosaic loses its projection; restore it so slope is computed at 30 m.
var dem = demCol.mosaic()
  .setDefaultProjection(ee.Image(demCol.first()).projection())
  .resample('bicubic');

var hs = ee.Terrain.hillshade(dem.multiply(VERT_EXAG), SUN_AZIMUTH, SUN_ELEV);
// Flat ground = 1, so shading only darkens slopes facing away from the sun
// and brightens slopes facing it.
var flatHs = Math.sin(SUN_ELEV * Math.PI / 180) * 255;
var shadeFactor = ee.Image(1).add(hs.divide(flatHs).subtract(1).multiply(HILL_STRENGTH))
  .clamp(0.25, 1.7);

var tint = dem.visualize({min: ELEV_MIN, max: ELEV_MAX, palette: TINT_PALETTE})
  .divide(255);

// ------------------------------------------------------------
// 4. STYLE ONE PERIOD
// ------------------------------------------------------------
function style(image) {
  var base = image.visualize(STRETCH).divide(255);               // 0..1 false colour
  var mndwi = image.normalizedDifference(['B3', 'B11']);
  var water = mndwi.gt(0.15).and(image.select('B8').lt(1800)).unmask(0);

  var landLit = base.multiply(shadeFactor)
    .multiply(1 - TINT_OPACITY).add(tint.multiply(TINT_OPACITY).multiply(shadeFactor));
  var out = landLit.where(water, base);                          // water untouched
  return out.multiply(255).clamp(0, 255).unmask(0).toUint8();
}

// ------------------------------------------------------------
// 5. PREVIEW AND EXPORT
// ------------------------------------------------------------
Object.keys(PERIODS).forEach(function(key) {
  var p = PERIODS[key];
  var image = composite(p.start, p.end);
  print(p.label + ' - scenes used:', s2.filterDate(p.start, p.end).size());

  var styled = style(image);
  Map.addLayer(styled, {min: 0, max: 255}, p.label + ' (styled)', key === 'period3_postdam');

  Export.image.toDrive({
    image: styled,
    description: key + '_falsecolour',
    folder: 'GEE_VIZ',
    fileNamePrefix: key + '_falsecolour',
    region: aoi,
    scale: EXPORT_SCALE,
    crs: OUTPUT_CRS,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF',
    formatOptions: {cloudOptimized: true}
  });
});

print('Look at the two styled layers first. Tune VERT_EXAG, HILL_STRENGTH, TINT_OPACITY, then run both tasks.');
