// 09 Shortwave false-colour preview (no export). Colour is styling only.
// Four band combinations x two periods. Toggle in the Layers panel.
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);

var COMBOS = {
  '1 Howarth SWIR1-NIR-Green': {bands: ['B11', 'B8', 'B3'], min: [300, 300, 200], max: [4500, 5000, 2500], gamma: 1.1},
  '2 SWIR2-NIR-Green':         {bands: ['B12', 'B8', 'B3'], min: [200, 300, 200], max: [3500, 5000, 2500], gamma: 1.1},
  '3 Veg-warm NIR-SWIR1-Green': {bands: ['B8', 'B11', 'B3'], min: [300, 300, 200], max: [5000, 4500, 2500], gamma: 1.1},
  '4 Agriculture (current)':   {bands: ['B11', 'B8', 'B2'], min: [300, 300, 150], max: [4500, 5000, 2200], gamma: 1.1}
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

var first = true;
Object.keys(PERIODS).forEach(function(pk) {
  var img = s2.filterDate(PERIODS[pk][0], PERIODS[pk][1]).median().clip(aoi);
  Object.keys(COMBOS).forEach(function(ck) {
    Map.addLayer(img, COMBOS[ck], pk + '  ' + ck, first);
    first = false;
  });
});

print('Zoom to the reservoir bed. Compare each combo in P2 and P3.');
