// ============================================================
// 04 · CANAL AND INTAKE STATUS (hand-traced network)
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Measures what satellites can actually see on YOUR traced network, before
// vs after the breach. The viewer works without this (it animates the
// traced geometry); this adds measured evidence per segment and station.
//
// INPUTS: run python/05_prepare_manual_network.py first, then upload as
// table assets (Assets -> New -> Table upload -> Shape files, select every
// file with the same name):
//   projects/earthenginesgallop/assets/canal_segments_manual
//       canal_segments_manual.shp  (1 km segments: seg_id, canal_id, ext_order)
//   projects/earthenginesgallop/assets/pumps_manual
//       pumps_manual.shp           (68 stations: uid, canal_id, ext_order)
//
// OUTPUTS
//   canal_segment_status   GeoJSON, per 1 km segment
//   pump_status            GeoJSON, per station
//
// PREMISE (yours, not measured)
//   Every traced canal and intake was OPERATIONAL before the breach (intakes
//   = pumps active in 2019 per the World Bank). The script does not test
//   that. It measures only what is visible AFTER (summer 2026).
//
// WHAT THIS CAN AND CANNOT SAY
//   Sentinel-2 pixels are 10 m. A canal narrower than ~30 m is sub-pixel, so
//   'No water detected' can mean dry OR too narrow to see. To keep that
//   honest, each segment carries 'measurable' = 1 when the corridor showed a
//   clear water signal in May 2023 (so a missing signal after really means
//   something) and 0 when it did not (absence after proves nothing).
//   Stations: distance to the nearest water after. Water within 500 m does
//   not mean the intake works; the reservoir fell by metres, so an intake can
//   sit high and dry beside the remaining water.
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);
var RESERVOIR_BOX = ee.Geometry.Rectangle([33.2, 46.76, 35.3, 47.86]);
var OUTPUT_CRS = 'EPSG:32636';

var BEFORE = ['2023-05-01', '2023-06-07'];   // end exclusive
var AFTER = ['2026-06-01', '2026-09-01'];    // summer 2026

var CORRIDOR_M = 45;         // half-width searched around the traced line
var MIN_SIGNAL = 0.15;       // wet fraction BEFORE at/above which a segment is 'measurable'
var WET_AFTER = 0.5;         // wet fraction after: 'Water detected'
var SOME_AFTER = 0.15;       // wet fraction after: 'Some water detected'
var STRANDED_M = 500;        // station farther than this from water = stranded

Map.centerObject(aoi, 9);
Map.setOptions('HYBRID');

// ------------------------------------------------------------
// 2. WATER MASKS (class cloud masking + MNDWI)
// ------------------------------------------------------------
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(aoi)
  .linkCollection(
    ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'),
    ['cs_cdf']
  )
  .map(function(image) {
    var scl = image.select('SCL');
    var clear = image.select('cs_cdf').gte(0.50)
      .and(scl.neq(3)).and(scl.neq(8)).and(scl.neq(9))
      .and(scl.neq(10)).and(scl.neq(11));
    return image.updateMask(clear).select(['B3', 'B11']).toFloat();
  });

function waterMask(range) {
  var img = s2.filterDate(range[0], range[1]).median().clip(aoi);
  return img.normalizedDifference(['B3', 'B11']).gt(0).rename('water');
}
var before = waterMask(BEFORE);
var after = waterMask(AFTER);

// ------------------------------------------------------------
// 3. TRACED NETWORK
// ------------------------------------------------------------
var segs = ee.FeatureCollection('projects/earthenginesgallop/assets/canal_segments_manual');
var pumps = ee.FeatureCollection('projects/earthenginesgallop/assets/pumps_manual');
print('Canal segments', segs.size());
print('Stations', pumps.size());

// Null handling: reduceRegions leaves a property off when a corridor has no
// clear pixels (cloud). Number methods fail on null, so test explicitly and
// give every feature a status. A real value of 0 must stay 0.
function isNull(v) {
  return ee.Number(ee.Algorithms.If(ee.Algorithms.IsEqual(v, null), 1, 0));
}
function numOr(v, fallback) {
  return ee.Number(ee.Algorithms.If(isNull(v), fallback, v));
}

// ------------------------------------------------------------
// 4. SEGMENT STATUS: wet fraction in the corridor, before vs after
// ------------------------------------------------------------
var corridors = segs.map(function(f) {
  return f.setGeometry(f.geometry().buffer(CORRIDOR_M));
});
var stack = before.rename('wet_before').addBands(after.rename('wet_after'));

var status = stack.reduceRegions({
  collection: corridors,
  reducer: ee.Reducer.mean(),
  scale: 10,
  tileScale: 4
}).map(function(f) {
  var rawB = f.get('wet_before');
  var rawN = f.get('wet_after');
  var hasAfter = isNull(rawN).not();
  var b = numOr(rawB, -1);
  var n = numOr(rawN, -1);
  // 1 when the corridor showed clear water before, so absence after counts.
  var measurable = ee.Number(ee.Algorithms.If(isNull(rawB), 0, b.gte(MIN_SIGNAL)));
  var cls = ee.Algorithms.If(hasAfter,
    ee.Algorithms.If(n.gte(WET_AFTER), 'Water detected',
      ee.Algorithms.If(n.gte(SOME_AFTER), 'Some water detected', 'No water detected')),
    'No clear data');
  return f.set({
    seg_status: cls,
    measurable: measurable,
    wet_before: ee.Algorithms.If(isNull(rawB), null, b),
    wet_after: ee.Algorithms.If(hasAfter, n, null)
  });
});

// ------------------------------------------------------------
// 5. STATION STATUS: distance to the nearest water, before vs after
// ------------------------------------------------------------
var proj30 = ee.Projection(OUTPUT_CRS).atScale(30);
var k = ee.Kernel.euclidean(5000, 'meters');
function distToWater(mask, name) {
  return mask.unmask(0).reproject(proj30).distance(k).unmask(5000).rename(name);
}
var pumpStatus = distToWater(before, 'd_before')
  .addBands(distToWater(after, 'd_now'))
  .reduceRegions({
    collection: pumps,
    reducer: ee.Reducer.first(),
    scale: 30,
    crs: proj30
  }).map(function(f) {
    var db = numOr(f.get('d_before'), 5000);
    var dn = numOr(f.get('d_now'), 5000);
    var cls = ee.Algorithms.If(dn.gt(STRANDED_M),
      'No water within 500 m', 'Water within 500 m');
    return f.set({pump_status: cls, d_before: db, d_now: dn});
  });

// ------------------------------------------------------------
// 6. DISPLAY
// ------------------------------------------------------------
Map.addLayer(before.selfMask(), {palette: ['#6ea8d9']}, 'Water May 2023', false, 0.6);
Map.addLayer(after.selfMask(), {palette: ['#08306b']}, 'Water summer 2026', false);

var segColours = {'No water detected': 'red', 'Some water detected': 'orange',
                  'Water detected': 'blue', 'No clear data': 'purple'};
Object.keys(segColours).forEach(function(c) {
  Map.addLayer(
    status.filter(ee.Filter.eq('seg_status', c)).style({color: segColours[c], width: 3}),
    {}, 'Canal segments: ' + c);
});
// Display only: the distance image is heavy, and re-running it for every map
// tile exceeds Earth Engine's memory. Evaluate the 68 stations once, then draw
// the static result. (The exports below use the live collection.)
var pumpColours = {'No water within 500 m': 'red', 'Water within 500 m': 'blue'};
pumpStatus.evaluate(function(fc) {
  var staticPumps = ee.FeatureCollection(fc);
  Object.keys(pumpColours).forEach(function(c) {
    Map.addLayer(
      staticPumps.filter(ee.Filter.eq('pump_status', c))
        .style({color: pumpColours[c], pointSize: 8}),
      {}, 'Stations: ' + c);
  });
});

status.aggregate_histogram('seg_status').evaluate(function(h) {
  print('Segments by status:', h);
});
print('Segments measurable:', status.filter(ee.Filter.eq('measurable', 1)).size());
print('Segments not measurable:', status.filter(ee.Filter.eq('measurable', 0)).size());
status.filter(ee.Filter.eq('measurable', 1)).aggregate_histogram('seg_status')
  .evaluate(function(h) { print('Measurable segments by status:', h); });
pumpStatus.aggregate_histogram('pump_status').evaluate(function(h) {
  print('Stations by status:', h);
});

// ------------------------------------------------------------
// 7. EXPORT
// ------------------------------------------------------------
Export.table.toDrive({
  collection: status,
  description: 'canal_segment_status',
  folder: 'GEE_VIZ',
  fileFormat: 'GeoJSON'
});
Export.table.toDrive({
  collection: pumpStatus,
  description: 'pump_status',
  folder: 'GEE_VIZ',
  fileFormat: 'GeoJSON'
});
print('To export: open Tasks and click Run on both tasks.');
