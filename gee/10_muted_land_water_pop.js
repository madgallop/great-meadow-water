// 10 Muted land + water pop preview (no export). Colour is styling only.
// Base = real false colour. Land saturation is reduced; water keeps its
// real colour (slightly boosted); a thin cyan rim marks the water edge.
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);

var LAND_SAT = 0.65;     // 1 = unchanged, lower = greyer land
var WATER_SAT = 1.25;    // >1 = richer water
var RIM_M = 40;          // rim width in metres (0 = off)
var RIM_OPACITY = 0.85;

var COMBOS = {
  'Howarth SWIR1-NIR-Green': {bands: ['B11', 'B8', 'B3'], min: [300, 300, 200], max: [4500, 5000, 2500], gamma: 1.1},
  'Agriculture':             {bands: ['B11', 'B8', 'B2'], min: [300, 300, 150], max: [4500, 5000, 2200], gamma: 1.1}
};
var PERIODS = {
  'P2 2022': ['2022-06-01', '2022-10-01'],
  'P3 2026': ['2026-06-01', '2026-10-01']
};

Map.centerObject(ee.Geometry.Point([34.0, 47.2]), 10);
Map.setOptions('SATELLITE');

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
      .select(['B2', 'B3', 'B4', 'B8', 'B11', 'B12'])
      .toFloat();
  });

function styled(img, combo) {
  var rgb = img.visualize(combo).divide(255);
  var water = img.normalizedDifference(['B3', 'B11']).gt(0.15)
    .and(img.select('B8').lt(1800)).unmask(0);

  var hsv = rgb.rgbToHsv();
  var sat = hsv.select('saturation');
  var newSat = sat.multiply(ee.Image(LAND_SAT).where(water, WATER_SAT)).clamp(0, 1);
  var out = ee.Image.cat([hsv.select('hue'), newSat, hsv.select('value')]).hsvToRgb();

  if (RIM_M > 0) {
    var rim = water.focal_max({radius: RIM_M, units: 'meters'}).and(water.not());
    var cyan = ee.Image.constant([0.56, 0.96, 1.0]).rename(['red', 'green', 'blue']);
    out = out.where(rim, out.multiply(1 - RIM_OPACITY).add(cyan.multiply(RIM_OPACITY)));
  }
  return out.multiply(255).clamp(0, 255).toUint8();
}

var first = true;
Object.keys(PERIODS).forEach(function(pk) {
  var img = s2.filterDate(PERIODS[pk][0], PERIODS[pk][1]).median().clip(aoi);
  Object.keys(COMBOS).forEach(function(ck) {
    Map.addLayer(styled(img, COMBOS[ck]), {min: 0, max: 255}, pk + '  ' + ck + ' (muted + rim)', first);
    first = false;
    Map.addLayer(img, COMBOS[ck], pk + '  ' + ck + ' (raw)', false);
  });
});

print('Tune LAND_SAT, WATER_SAT, RIM_M at the top. Compare each styled layer with its raw one.');
