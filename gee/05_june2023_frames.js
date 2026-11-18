// ============================================================
// 05 · JUNE 2023 FRAMES (fills the gap between f00 and f01)
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Script 03 jumps from "6 June 2023" to "Jul-Aug 2023", so most of the
// drainage is missing. SWOT in the project folder only covers the area BELOW
// the dam, so this script uses optical imagery instead:
//   Sentinel-2 SR (10 m, ~5 day revisit) + Landsat 8/9 Collection 2 L2 (30 m)
//
// ONE FRAME PER ACQUISITION DATE (not a composite): the point is to see how
// the reservoir changed day to day. A frame is the mosaic of everything
// acquired that day (Sentinel-2 drawn over Landsat where both exist).
//
// Frames have PARTIAL coverage: a single day rarely covers the whole area
// and cloud removes more. Uncovered pixels are exported as 255 = no
// observation, never as dry. The CSV lists the footprint share each date
// actually observed so you can drop weak frames.
//
// Classes match script 03 (0 none, 1 weak, 2 shallow, 3 open water). The
// thresholds were tuned on Sentinel-2. Landsat is rescaled to the same
// reflectance units, but its 30 m pixels and different bands mean the
// classes are comparable, not identical. The label says which sensors each
// frame used.
//
// OUTPUTS
//   june2023_water_frames   byte GeoTIFF, bands f00..fNN (255 = no observation)
//   june2023_frames_table   CSV: frame, date, sensors, label, observed %
// Then:  python python/03_build_frame_tiles.py \
//          --src data/gee_outputs/june2023_water_frames.tif \
//          --footprint data/gee_outputs/Reservoir_footprint.tif \
//          --labels data/gee_outputs/june2023_frames_table.csv \
//          --zmin 7 --zmax 11 --out viewer/tiles/june
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
var area = ee.Geometry.Rectangle([33.2, 46.5, 35.3, 47.9]);
var RESERVOIR_BOX = ee.Geometry.Rectangle([33.2, 46.76, 35.3, 47.86]);
var OUTPUT_CRS = 'EPSG:32636';
var EXPORT_SCALE = 30;

var START = '2023-06-01';
var END = '2023-07-01';          // end exclusive; July is covered by f01
var MIN_OBSERVED_PCT = 5;        // drop dates that see less than this of the footprint

Map.centerObject(area, 9);
Map.setOptions('SATELLITE');

// ------------------------------------------------------------
// 2. SENTINEL-2 (class convention: CloudScore+ and SCL)
// ------------------------------------------------------------
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(area)
  .filterDate(START, END)
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
      .set({
        date: image.date().format('YYYY-MM-dd'),
        sensor: 'Sentinel-2',
        prio: 1                                   // drawn on top
      });
  });

// ------------------------------------------------------------
// 3. LANDSAT 8 + 9 COLLECTION 2 LEVEL 2
// ------------------------------------------------------------
function landsat(id, name) {
  return ee.ImageCollection(id)
    .filterBounds(area)
    .filterDate(START, END)
    .map(function(image) {
      // QA_PIXEL bits 1 dilated cloud, 2 cirrus, 3 cloud, 4 shadow, 5 snow.
      var qa = image.select('QA_PIXEL');
      var clear = qa.bitwiseAnd(62).eq(0);          // 62 = bits 1-5
      // Surface reflectance scale, then x10000 to match Sentinel-2 SR units.
      var sr = image.select(['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6'],
                            ['B2', 'B3', 'B4', 'B8', 'B11'])
        .multiply(0.0000275).add(-0.2).multiply(10000);
      return sr.updateMask(clear).toFloat().set({
        date: image.date().format('YYYY-MM-dd'),
        sensor: name,
        prio: 0
      });
    });
}
var l8 = landsat('LANDSAT/LC08/C02/T1_L2', 'Landsat 8');
var l9 = landsat('LANDSAT/LC09/C02/T1_L2', 'Landsat 9');

var all = s2.merge(l8).merge(l9);

// ------------------------------------------------------------
// 4. CLASSIFY (same thresholds as script 03)
// ------------------------------------------------------------
var corridor = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('max_extent').gt(0)
  .focal_max({radius: 500, units: 'meters'})
  .clip(area);

var footprint = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('occurrence').unmask(0).gt(50)
  .focal_min({radius: 150, units: 'meters'})
  .focal_max({radius: 150, units: 'meters'})
  .clip(RESERVOIR_BOX);

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
    .clip(area)
    .rename('waterClass')
    .toByte();
}

// ------------------------------------------------------------
// 5. ONE FRAME PER DATE
// ------------------------------------------------------------
var dates = ee.List(all.aggregate_array('date')).distinct().sort();

// Mosaic one date; later images draw on top, so sort by priority first.
function dayFrame(d) {
  var day = all.filter(ee.Filter.eq('date', d)).sort('prio');
  var sensors = ee.List(day.aggregate_array('sensor')).distinct().join(' + ');
  var img = classify(day.mosaic());
  var obs = img.mask().updateMask(footprint)
    .reduceRegion({
      reducer: ee.Reducer.mean(),
      geometry: RESERVOIR_BOX, scale: 100, maxPixels: 1e10, tileScale: 4
    }).get('waterClass');
  return img.set({date: d, sensors: sensors, observed_pct: ee.Number(obs).multiply(100)});
}

var frames = ee.ImageCollection.fromImages(dates.map(dayFrame))
  .filter(ee.Filter.gte('observed_pct', MIN_OBSERVED_PCT));

var table = frames.map(function(img) {
  var d = ee.String(img.get('date'));
  var label = ee.Date(d).format('d MMM YYYY')
    .cat(' (').cat(ee.String(img.get('sensors'))).cat(')');
  return ee.Feature(null, {
    date: d,
    sensors: img.get('sensors'),
    label: label,
    observed_pct: ee.Number(img.get('observed_pct')).format('%.1f')
  });
});

// Frame numbers follow date order. Set after filtering so f00 is the first
// frame that survives.
var n = frames.size();
var names = ee.List.sequence(0, n.subtract(1)).map(function(i) {
  return ee.String('f').cat(ee.Number(i).format('%02d'));
});
var stack = frames.toBands().rename(names);
var tableIdx = ee.FeatureCollection(table.toList(n).zip(ee.List.sequence(0, n.subtract(1)))
  .map(function(p) {
    p = ee.List(p);
    return ee.Feature(p.get(0)).set('frame',
      ee.String('f').cat(ee.Number(p.get(1)).format('%02d')));
  }));

print('Frames kept:', n);
print('Frame table (date, sensors, % of footprint observed):', tableIdx);

// ------------------------------------------------------------
// 6. DISPLAY
// ------------------------------------------------------------
var palette = ['000000', 'F2D45C', '54CEE0', '1267CF'];
Map.addLayer(footprint.selfMask(), {palette: ['ffffff']}, 'Reservoir footprint', false, 0.4);
Map.addLayer(stack.select('f00').selfMask(), {min: 0, max: 3, palette: palette},
  'First frame', true);
Map.addLayer(stack.select(ee.String(names.get(n.subtract(1)))).selfMask(),
  {min: 0, max: 3, palette: palette}, 'Last frame', true);

// ------------------------------------------------------------
// 7. EXPORT
// ------------------------------------------------------------
Export.image.toDrive({
  image: stack.unmask(255).toByte(),
  description: 'june2023_water_frames',
  folder: 'GEE_VIZ',
  fileNamePrefix: 'june2023_water_frames',
  region: area,
  scale: EXPORT_SCALE,
  crs: OUTPUT_CRS,
  maxPixels: 1e13,
  fileFormat: 'GeoTIFF',
  formatOptions: {cloudOptimized: true, noData: 255}
});
Export.table.toDrive({
  collection: tableIdx,
  description: 'june2023_frames_table',
  folder: 'GEE_VIZ',
  fileNamePrefix: 'june2023_frames_table',
  fileFormat: 'CSV',
  selectors: ['frame', 'date', 'sensors', 'label', 'observed_pct']
});

print('To export: open Tasks and click Run on both tasks.');
