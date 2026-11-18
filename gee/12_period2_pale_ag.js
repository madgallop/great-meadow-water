// 12 Period 2 PALE-AG version of script 11 (export only the 2022 reservoir era).
// Identical to script 11 except:
//   - AG_DARK_NDVI = [2, 3]  -> no pixel reaches it, so crops stay the lighter agGreen (#88b18d)
//   - only the P2 2022 period
//   - export name ends in _mappalette_pale
// The viewer fades the normal (dark-ag) Period 2 tiles in over these as the canals fill.
// Colour is styling only.
var aoi = ee.Geometry.Rectangle([33.1, 46.25, 35.4, 48.0]);

var RELIEF = 0.6;      // hillshade strength for B and C (0 = none)
var VERT_EXAG = 3;
var SUN_AZ = 315, SUN_ELEV = 35;
var LAND_SAT = 0.6;    // option C land saturation
var OUTLINE = '#123344';      // dark blue shoreline line, like the map
var OUTLINE_M = 30;           // line width in metres (0 = off)
var OUTLINE_OPACITY = 0.9;
// Water shade follows how bright the water is in GREEN (B3, reflectance x10000).
var WATER_B3_MIN = 300;
var WATER_B3_MAX = 1200;
// Dark-green crop class switched OFF (script 11 uses [0.62, 0.85]).
var AG_DARK_NDVI = [2, 3];

var PAL = {
  paper: '#e3e2cc', meadow: '#c0d3b8', agGreen: '#88b18d',
  sepia: '#ac9e8d', sepiaDark: '#857f74',
  agDark: '#345f4d',
  waterLight: '#94ceba', waterMid: '#59acaa', waterDark: '#3e7a84'
};
var PERIODS = {
  'P2 2022': ['2022-06-01', '2022-10-01']
};

var FILES = {'P2 2022': 'period2_reservoir'};

Map.centerObject(ee.Geometry.Point([34.0, 47.2]), 10);
Map.setOptions('SATELLITE');

function rgb(hex) {
  return ee.Image.constant([parseInt(hex.substr(1, 2), 16) / 255,
                            parseInt(hex.substr(3, 2), 16) / 255,
                            parseInt(hex.substr(5, 2), 16) / 255]);
}
function mix(a, b, t) { return a.multiply(ee.Image(1).subtract(t)).add(b.multiply(t)); }
function sstep(x, a, b) {
  var t = x.subtract(a).divide(b - a).clamp(0, 1);
  return t.multiply(t).multiply(ee.Image(3).subtract(t.multiply(2)));
}

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

// Relief factor (1 = flat ground), from the Copernicus DEM.
var demCol = ee.ImageCollection('COPERNICUS/DEM/GLO30_2024_1').filterBounds(aoi).select('DEM');
var dem = demCol.mosaic().setDefaultProjection(ee.Image(demCol.first()).projection())
  .resample('bicubic');
var hs = ee.Terrain.hillshade(dem.multiply(VERT_EXAG), SUN_AZ, SUN_ELEV);
var flat = Math.sin(SUN_ELEV * Math.PI / 180) * 255;
var relief = hs.divide(flat).subtract(1);            // 0 on flat ground

function waterMask(img) {
  return img.normalizedDifference(['B3', 'B11']).gt(0.15)
    .and(img.select('B8').lt(1800)).unmask(0);
}

function mapPalette(img, withRelief) {
  var B2 = img.select('B2'), B3 = img.select('B3'), B4 = img.select('B4');
  var B8 = img.select('B8'), B11 = img.select('B11');
  var ndvi = img.normalizedDifference(['B8', 'B4']);
  var L = B4.multiply(0.2126).add(B3.multiply(0.7152)).add(B2.multiply(0.0722)).divide(10000);
  var Lp = L.subtract(0.03).divide(0.25 - 0.03).clamp(0, 1);

  var base = mix(rgb(PAL.sepiaDark), mix(rgb(PAL.sepia), rgb(PAL.paper), Lp), Lp.multiply(1.4).clamp(0, 1));
  var veg = sstep(ndvi, 0.20, 0.55);
  var crop = sstep(ndvi, 0.45, 0.75);   // dense, healthy vegetation = ag green
  var land = mix(base, mix(rgb(PAL.meadow).multiply(0.88), rgb(PAL.meadow), Lp), veg);
  land = mix(land, mix(rgb(PAL.agGreen).multiply(0.9), rgb(PAL.agGreen), Lp), crop);
  var dense = sstep(ndvi, AG_DARK_NDVI[0], AG_DARK_NDVI[1]);   // always 0 here
  land = mix(land, mix(rgb(PAL.agDark).multiply(0.9), rgb(PAL.agDark), Lp), dense);

  if (withRelief) {
    land = land.multiply(ee.Image(1).add(relief.multiply(RELIEF))).clamp(0, 1);
  }
  // 0 = darkest water, 1 = lightest: light -> mid -> dark teal, three stops
  var lt = B3.subtract(WATER_B3_MIN).divide(WATER_B3_MAX - WATER_B3_MIN).clamp(0, 1);
  var waterCol = mix(mix(rgb(PAL.waterDark), rgb(PAL.waterMid), sstep(lt, 0, 0.5)),
                     rgb(PAL.waterLight), sstep(lt, 0.5, 1));
  var wm = waterMask(img);
  var out = mix(land, waterCol, wm);
  if (OUTLINE_M > 0) {
    // line sits just inside the water edge, from the real water mask
    var edge = wm.and(wm.focal_min({radius: OUTLINE_M, units: 'meters'}).not());
    out = mix(out, rgb(OUTLINE), edge.multiply(OUTLINE_OPACITY));
  }
  return out.rename(['red', 'green', 'blue']).multiply(255).clamp(0, 255).toUint8();
}

Object.keys(PERIODS).forEach(function(pk) {
  var img = s2.filterDate(PERIODS[pk][0], PERIODS[pk][1]).median().clip(aoi);
  var vis = {min: 0, max: 255};
  Map.addLayer(mapPalette(img, true), vis, pk + '  pale ag + relief', true);

  // EXPORT: map palette + relief with pale ag, same 20 m grid as script 11.
  // Nothing runs until you click Run on the task in the Tasks tab.
  Export.image.toDrive({
    image: mapPalette(img, true),
    description: FILES[pk] + '_mappalette_pale',
    folder: 'GEE_VIZ',
    fileNamePrefix: FILES[pk] + '_mappalette_pale',
    region: aoi,
    scale: 20,
    crs: 'EPSG:32636',
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF',
    formatOptions: {cloudOptimized: true}
  });
});

print('Pale-ag Period 2. Click Run on the task in the Tasks tab, then save the tif to data/gee_outputs.');
