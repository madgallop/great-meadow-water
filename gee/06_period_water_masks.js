// ============================================================
// 06 · PERIOD WATER MASKS (for the stylised water highlight)
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// The stylised map recolours the satellite composite and needs to know where
// the water is, to give it the glowing rim. This exports the water classes for
// the same two periods and the SAME GRID as script 02, so they line up pixel
// for pixel with period2_reservoir_truecolour / period3_postdam_truecolour.
//
// Classes match script 03: 0 none, 1 weak, 2 secondary/shallow, 3 open water.
// 255 = no clear observation. Only classes 2 and 3 are styled as water.
//
// OUTPUT (Tasks tab -> Run)
//   period2_water   summer 2022
//   period3_water   summer 2026
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS (identical to script 02 so the grids match)
// ------------------------------------------------------------
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);
var OUTPUT_CRS = 'EPSG:32636';
var EXPORT_SCALE = 20;

var PERIODS = {
  period2_water: {start: '2022-06-01', end: '2022-10-01', label: 'Reservoir era - summer 2022'},
  period3_water: {start: '2026-06-01', end: '2026-10-01', label: 'Post-dam - summer 2026'}
};

Map.centerObject(aoi, 9);
Map.setOptions('SATELLITE');

// ------------------------------------------------------------
// 2. SENTINEL-2 WITH CLOUD AND SHADOW MASKING (class convention)
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

// Restrict weaker signals to the historic water corridor (as in script 03).
var corridor = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('max_extent').gt(0)
  .focal_max({radius: 500, units: 'meters'})
  .clip(aoi);

function classify(image) {
  var mndwi = image.normalizedDifference(['B3', 'B11']);
  var ndvi = image.normalizedDifference(['B8', 'B4']);
  var nir = image.select('B8');
  var weak = mndwi.gt(-0.25).and(ndvi.lt(0.40)).and(corridor);
  var secondary = mndwi.gt(-0.05).and(nir.lt(2200)).and(corridor);
  var open = mndwi.gt(0.15).and(nir.lt(1800));
  return ee.Image.constant(0)
    .where(weak, 1).where(secondary, 2).where(open, 3)
    .updateMask(image.select('B3').mask())
    .clip(aoi)
    .rename('waterClass')
    .toByte();
}

// ------------------------------------------------------------
// 3. BUILD, PREVIEW, EXPORT
// ------------------------------------------------------------
var palette = ['000000', 'F2D45C', '54CEE0', '1267CF'];
Object.keys(PERIODS).forEach(function(key) {
  var p = PERIODS[key];
  var cls = classify(composite(p.start, p.end));
  Map.addLayer(cls.selfMask(), {min: 0, max: 3, palette: palette}, p.label, key === 'period2_water');

  Export.image.toDrive({
    image: cls.unmask(255).toByte(),
    description: key,
    folder: 'GEE_VIZ',
    fileNamePrefix: key,
    region: aoi,
    scale: EXPORT_SCALE,
    crs: OUTPUT_CRS,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF',
    formatOptions: {cloudOptimized: true, noData: 255}
  });
});

print('To export: open Tasks and click Run on both tasks. Put the files in data/gee_outputs/.');
