// ============================================================
// 03 · DRAINAGE WATER FRAMES (Transition 3: reservoir -> drained)
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Reuses the class animation script's classification and masking:
//   class 1 weak wet signal, 2 secondary/shallow water, 3 open water
// and exports ONE multi-band byte image: band f00 = pre-breach reference,
// f01..f15 = the 15 two-month observations July 2023 - Dec 2025.
// Python (02_build_frames.py, next step) turns each band into a tile
// layer; the viewer crossfades between them on a time slider.
//
// EVERY frame is observed (a median of clear Sentinel-2 scenes). Nothing is
// interpolated here. The viewer's crossfade between frames is a visual
// transition only, and the UI should say so.
// NOTE: June 2023 is the breach month. Its dense frames come from a
// separate script (a gap flagged earlier); the class animation starts in July.
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
var area = ee.Geometry.Rectangle([33.2, 46.5, 35.3, 47.9]);
var OUTPUT_CRS = 'EPSG:32636';
var EXPORT_SCALE = 30;

var firstDate = ee.Date('2023-07-01');
var periods = ee.List.sequence(0, 14);   // 15 two-month observations

Map.centerObject(area, 9);
Map.setOptions('SATELLITE');

// ------------------------------------------------------------
// 2. SENTINEL-2 WITH CLOUD AND SHADOW MASKING (class convention)
// ------------------------------------------------------------
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(area)
  .filterDate('2023-05-01', '2026-01-01')
  .linkCollection(
    ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'),
    ['cs_cdf']
  )
  .map(function(image) {
    var scl = image.select('SCL');
    var clear = image.select('cs_cdf').gte(0.50)
      .and(scl.neq(3))
      .and(scl.neq(8))
      .and(scl.neq(9))
      .and(scl.neq(10))
      .and(scl.neq(11));
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
    .clip(area);
}

// Restrict weaker signals to the historic water corridor.
var corridor = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('max_extent')
  .gt(0)
  .focal_max({radius: 500, units: 'meters'})
  .clip(area);

// ------------------------------------------------------------
// 3. CLASSIFY (same thresholds as the class animation script)
// ------------------------------------------------------------
function classify(image) {
  var mndwi = image.normalizedDifference(['B3', 'B11']);
  var ndvi = image.normalizedDifference(['B8', 'B4']);
  var nir = image.select('B8');

  var weak = mndwi.gt(-0.25).and(ndvi.lt(0.40)).and(corridor);
  var secondary = mndwi.gt(-0.05).and(nir.lt(2200)).and(corridor);
  var open = mndwi.gt(0.15).and(nir.lt(1800));

  // Later conditions overwrite earlier ones. Pixels with no clear
  // observation are masked, then exported as 255 = "no data".
  return ee.Image.constant(0)
    .where(weak, 1)
    .where(secondary, 2)
    .where(open, 3)
    .updateMask(image.select('B3').mask())
    .clip(area)
    .rename('waterClass')
    .toByte();
}

// ------------------------------------------------------------
// 4. FRAMES
// ------------------------------------------------------------
// f00: last clear window before the breach (6 June 2023).
var preBreach = classify(composite('2023-05-01', '2023-06-07'));

var frameList = periods.map(function(n) {
  n = ee.Number(n);
  var start = firstDate.advance(n.multiply(2), 'month');
  var end = start.advance(2, 'month');
  return classify(composite(start, end)).set({
    periodIndex: n,
    period: start.format('YYYY-MM')
      .cat(' to ')
      .cat(end.advance(-1, 'day').format('YYYY-MM-dd'))
  });
});

print('Frame periods',
  ee.List(frameList).map(function(i) { return ee.Image(i).get('period'); }));
print('Scenes per period', periods.map(function(n) {
  n = ee.Number(n);
  var start = firstDate.advance(n.multiply(2), 'month');
  return s2.filterDate(start, start.advance(2, 'month')).size();
}));

var names = ee.List.sequence(0, 15).map(function(i) {
  return ee.String('f').cat(ee.Number(i).format('%02d'));
});
var stack = ee.ImageCollection.fromImages(
  ee.List([preBreach]).cat(frameList)
).toBands().rename(names);

// ------------------------------------------------------------
// 5. DISPLAY
// ------------------------------------------------------------
var palette = ['000000', 'F2D45C', '54CEE0', '1267CF'];
Map.addLayer(stack.select('f00').selfMask(), {min: 0, max: 3, palette: palette},
  'Pre-breach (May - 6 Jun 2023)', true);
Map.addLayer(stack.select('f15').selfMask(), {min: 0, max: 3, palette: palette},
  'Latest frame (Nov-Dec 2025)', true);

// ------------------------------------------------------------
// 6. EXPORT
// ------------------------------------------------------------
Export.image.toDrive({
  image: stack.unmask(255).toByte(),
  description: 'drainage_water_frames',
  folder: 'GEE_VIZ',
  fileNamePrefix: 'drainage_water_frames',
  region: area,
  scale: EXPORT_SCALE,
  crs: OUTPUT_CRS,
  maxPixels: 1e13,
  fileFormat: 'GeoTIFF',
  formatOptions: {cloudOptimized: true, noData: 255}
});

print('To export: open Tasks and click Run.');
