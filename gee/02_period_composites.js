// ============================================================
// 02 · PERIOD COMPOSITES (true-colour imagery for the 3D drape)
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Builds one cloud-free Sentinel-2 true-colour composite per satellite era
// and exports it as an 8-bit RGB GeoTIFF for tiling in Python.
//
//   PERIOD 1  pre-dam (before 1956)   NO satellite imagery exists.
//             Handled in the viewer with the georeferenced 1853 / 1886 maps.
//   PERIOD 2  reservoir era           summer 2022 (same reference window
//                                     the class PCA script uses)
//   PERIOD 3  post-dam                summer 2026
//
// Cloud masking is the class convention: CloudScore+ (cs_cdf >= 0.5) plus
// the SCL classes for shadow, cloud, cirrus and snow.
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);
var OUTPUT_CRS = 'EPSG:32636';
// 20 m keeps the file manageable for the whole extent. Use 10 for a small
// close-up area only (the full AOI at 10 m is several GB).
var EXPORT_SCALE = 20;

var PERIODS = {
  period2_reservoir: {start: '2022-06-01', end: '2022-10-01',   // end exclusive
                      label: 'Reservoir era - summer 2022'},
  period3_postdam:   {start: '2026-06-01', end: '2026-10-01',
                      label: 'Post-dam - summer 2026'}
};

// Same display stretch as the class animation script.
var TRUE_COLOUR = {bands: ['B4', 'B3', 'B2'], min: 150, max: 3200, gamma: 1.15};

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

// Keeps the required bands if a period has no clear scene.
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

function trueColourByte(image) {
  // Pixels with no clear observation stay black (0,0,0); check the preview.
  return image.visualize(TRUE_COLOUR).unmask(0).toUint8();
}

// ------------------------------------------------------------
// 3. BUILD, PREVIEW, EXPORT
// ------------------------------------------------------------
Object.keys(PERIODS).forEach(function(key) {
  var p = PERIODS[key];
  var image = composite(p.start, p.end);
  print(p.label + ' - scenes used:', s2.filterDate(p.start, p.end).size());

  Map.addLayer(image, TRUE_COLOUR, p.label, key === 'period3_postdam');

  Export.image.toDrive({
    image: trueColourByte(image),
    description: key + '_truecolour',
    folder: 'GEE_VIZ',
    fileNamePrefix: key + '_truecolour',
    region: aoi,
    scale: EXPORT_SCALE,
    crs: OUTPUT_CRS,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF',
    formatOptions: {cloudOptimized: true}
  });
});

print('To export: open Tasks and click Run on each task.');
