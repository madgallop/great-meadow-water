// ============================================================
// 07 · FALSE-COLOUR LOOK PREVIEW (no export; just look at the map)
// Great Meadow / Kakhovka 3D visualization (EarthEngines project)
//
// Tries the "not quite true colour" look with REAL band combinations instead
// of recolouring true colour. Toggle the layers in the Layers panel:
//   1 Agriculture  SWIR1-NIR-Blue   crops bright green, bare soil pink/magenta
//   2 SWIR         SWIR2-NIR2-Red   vegetation green, soil orange/brown
//   3 Colour IR    NIR-Red-Green    vegetation red, water near black
// then on top:
//   Water glow     halo around open water (decorative)
//   Water          open water in neon cyan
//   Context wash   white veil OUTSIDE the water corridor, so the valley is
//                  the saturated zone and the rest recedes
//
// Band combinations are standard remote-sensing recipes; the exact colours
// depend on the stretch below, so tune it if one looks flat or blown out.
// Colour here is styling. Brightness does not encode a measurement.
// ============================================================

// ------------------------------------------------------------
// 1. SETTINGS
// ------------------------------------------------------------
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);
var START = '2022-06-01';   // Period 2 reference; change to 2026-06-01 to try Period 3
var END = '2022-10-01';

// Display stretch per band (reflectance x 10000). Widen max to darken,
// raise min to deepen shadows.
var STRETCH = {
  agriculture: {bands: ['B11', 'B8', 'B2'], min: [300, 300, 150], max: [4500, 5000, 2200], gamma: 1.1},
  swir:        {bands: ['B12', 'B8A', 'B4'], min: [200, 300, 150], max: [3500, 5000, 3000], gamma: 1.1},
  colourIR:    {bands: ['B8', 'B4', 'B3'], min: [300, 200, 200], max: [5500, 3000, 2500], gamma: 1.1}
};

Map.centerObject(ee.Geometry.Point([34.0, 47.2]), 10);
Map.setOptions('SATELLITE');

// ------------------------------------------------------------
// 2. CLEAN SENTINEL-2 COMPOSITE (class cloud masking)
// ------------------------------------------------------------
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(aoi)
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
      .select(['B2', 'B3', 'B4', 'B8', 'B8A', 'B11', 'B12'])
      .toFloat();
  });
var img = s2.median().clip(aoi);

// ------------------------------------------------------------
// 3. WATER (same open-water rule as scripts 03 and 06)
// ------------------------------------------------------------
var mndwi = img.normalizedDifference(['B3', 'B11']);
var water = mndwi.gt(0.15).and(img.select('B8').lt(1800)).selfMask();

// Historic water corridor, widened, for the context wash.
var corridor = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('max_extent').gt(0)
  .focal_max({radius: 6000, units: 'meters'})
  .clip(aoi);

// ------------------------------------------------------------
// 4. LAYERS
// ------------------------------------------------------------
Map.addLayer(img, {bands: ['B4', 'B3', 'B2'], min: 150, max: 3200, gamma: 1.15},
  'True colour (reference)', false);
Map.addLayer(img, STRETCH.agriculture, '1 Agriculture (SWIR1-NIR-Blue)', true);
Map.addLayer(img, STRETCH.swir, '2 SWIR (SWIR2-NIR2-Red)', false);
Map.addLayer(img, STRETCH.colourIR, '3 Colour IR (NIR-Red-Green)', false);

// Halo first (under the water), then the water itself.
Map.addLayer(water.focal_max({radius: 120, units: 'meters'}).selfMask(),
  {palette: ['8ff6ff']}, 'Water glow', true, 0.35);
Map.addLayer(water, {palette: ['19e6ff']}, 'Water (neon cyan)', true, 0.95);

// White veil outside the corridor so the valley carries the colour.
Map.addLayer(ee.Image.constant(1).updateMask(corridor.unmask(0).not()).clip(aoi),
  {palette: ['ffffff']}, 'Context wash (outside corridor)', false, 0.55);

print('Zoom to the reservoir. Toggle layers 1-3 and the wash. Tell me which look is closest.');
