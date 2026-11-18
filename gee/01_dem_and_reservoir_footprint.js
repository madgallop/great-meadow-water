// ============================================================
// 01 · DEM AND RESERVOIR FOOTPRINT
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Follows the in-class DEM script: SETTINGS -> LOAD -> DISPLAY -> EXPORT.
//
// OUTPUTS (Tasks tab -> Run)
//   Copernicus_DEM_UTM36   30 m terrain GeoTIFF. NOT the reservoir bed: the
//                          reservoir is a flat water surface in this DEM.
//   Reservoir_footprint    byte raster, 1 = reservoir (frequently-water
//                          pixels). Python uses it to swap the flat surface
//                          for the modelled bed (model_data bathymetry).
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
// Context extent: reservoir + the whole canal network.
// (To use a drawn polygon instead: var aoi = geometry;)
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);

// Keeps the footprint off the downstream river, the coast and any other
// reservoir. Check the result and tighten if it grabs anything else.
var RESERVOIR_BOX = ee.Geometry.Rectangle([33.2, 46.76, 35.3, 47.86]);

var PIXEL_SIZE = 30;
var NODATA = -9999;
// UTM 36N covers 30-36 E, which contains this whole area. (The class DEM
// script defaults to EPSG:32635, which is 24-30 E.)
var OUTPUT_CRS = 'EPSG:32636';

var OCCURRENCE_MIN = 50;      // JRC: % of observations with water
var OPENING_RADIUS_M = 150;   // opening removes canals and ponds < ~300 m wide

Map.setOptions('TERRAIN');
Map.centerObject(aoi, 9);

// ------------------------------------------------------------
// 2. LOAD COPERNICUS DEM
// ------------------------------------------------------------
var dem = ee.ImageCollection('COPERNICUS/DEM/GLO30_2024_1')
  .filterBounds(aoi)
  .select('DEM')
  .mosaic()
  .rename('elevation_m')
  .toFloat()
  .clip(aoi);

// ------------------------------------------------------------
// 3. RESERVOIR FOOTPRINT (JRC Global Surface Water)
// ------------------------------------------------------------
var footprint = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('occurrence')
  .unmask(0)
  .gt(OCCURRENCE_MIN)
  .focal_min({radius: OPENING_RADIUS_M, units: 'meters'})
  .focal_max({radius: OPENING_RADIUS_M, units: 'meters'})
  .clip(RESERVOIR_BOX)
  .rename('reservoir')
  .toByte();

// Sanity check: compare with published reservoir area figures. A very
// different number means the box or threshold needs work.
var areaM2 = footprint.selfMask()
  .multiply(ee.Image.pixelArea())
  .reduceRegion({
    reducer: ee.Reducer.sum(),
    geometry: RESERVOIR_BOX,
    scale: 60,
    maxPixels: 1e10,
    tileScale: 4
  }).get('reservoir');
ee.Number(areaM2).divide(1e6).evaluate(function(v) {
  print('Reservoir footprint area (km2):', v);
});

// ------------------------------------------------------------
// 4. DISPLAY
// ------------------------------------------------------------
var vis = {
  min: 0,
  max: 200,
  palette: ['213b6b', '4e9b77', 'd9cf8b', '93684b', 'ffffff']
};
var demLayer = ui.Map.Layer(dem, vis, 'Copernicus DEM - coloured elevation', true);
Map.layers().add(demLayer);

// Stretch colours to the AOI (display only), as in the class script.
var range = dem.reduceRegion({
  reducer: ee.Reducer.percentile([2, 98]),
  geometry: aoi,
  crs: OUTPUT_CRS,
  scale: 150,
  bestEffort: true,
  maxPixels: 1e8,
  tileScale: 4
});
range.evaluate(function(values, error) {
  if (error || !values ||
      typeof values.elevation_m_p2 !== 'number' ||
      typeof values.elevation_m_p98 !== 'number') {
    print('Could not calculate display range:', error);
    return;
  }
  vis.min = values.elevation_m_p2;
  vis.max = Math.max(values.elevation_m_p98, vis.min + 1);
  demLayer.setVisParams(vis);
  print('Colour range in metres:', vis.min, vis.max);
});

Map.addLayer(footprint.selfMask(), {palette: ['1267CF']},
  'Reservoir footprint (JRC occurrence > ' + OCCURRENCE_MIN + ')', true, 0.6);

// ------------------------------------------------------------
// 5. EXPORTS
// ------------------------------------------------------------
Export.image.toDrive({
  image: dem.unmask({value: NODATA, sameFootprint: false}),
  description: 'Copernicus_DEM_UTM36',
  folder: 'GEE_DEM',
  fileNamePrefix: 'Copernicus_DEM_UTM36',
  region: aoi,
  scale: PIXEL_SIZE,
  crs: OUTPUT_CRS,
  maxPixels: 1e13,
  fileFormat: 'GeoTIFF',
  formatOptions: {cloudOptimized: true, noData: NODATA}
});

Export.image.toDrive({
  image: footprint.unmask(0),
  description: 'Reservoir_footprint',
  folder: 'GEE_DEM',
  fileNamePrefix: 'Reservoir_footprint',
  region: aoi,
  scale: PIXEL_SIZE,
  crs: OUTPUT_CRS,
  maxPixels: 1e13,
  fileFormat: 'GeoTIFF',
  formatOptions: {cloudOptimized: true}
});

print('To export: open Tasks and click Run on both tasks.');
