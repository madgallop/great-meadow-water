// Great Meadow viewer prototype.
// Data flow: GEE exports -> python/02_build_tiles.py -> static tiles read here.
// No Earth Engine at view time.

const CAMERAS = {            // same extent for every period, per the brief
  1: {center: [34.2, 47.3], zoom: 8.6, pitch: 55, bearing: 0},
  2: {center: [34.2, 47.3], zoom: 8.6, pitch: 55, bearing: 0},
  3: {center: [34.2, 47.3], zoom: 8.6, pitch: 55, bearing: 0}
};

// Period imagery tiles (made by python/02_build_tiles.py). A period whose
// meta.json is missing is skipped, so the page works before any tiles exist.
// The satellite tiles carry the c.1775-map colours baked in by gee/11 (relief included).
const IMAGERY = 'tiles/styled/mappalette';
const BG_COLOUR = '#e3e2cc';
// Bump this whenever tiles are rebuilt or recoloured. It is added to every tile URL
// so the browser cannot serve stale cached tiles (e.g. the old bright-blue frames).
const TILE_VERSION = '4';
const P1_OPACITY = 0.88;   // the 1775 map; the rest lets muted modern imagery show faintly through
function periodBase(p) {
  if (p === 1) return 'tiles/period1_map1775';   // the georeferenced c.1775 map (not satellite imagery)
  return `${IMAGERY}/period${p}`;
}
// Observation sets for Period 3 (python/03_build_frame_tiles.py; SWOT via 04).
const FRAME_SETS = {
  s2:   {base: 'tiles/frames', name: 'Sentinel-2, two-month composites (Jul 2023 to Dec 2025)',
         note: 'Water classes from Sentinel-2, drawn over summer 2026 imagery. Each position is an observation; the fade between observations is visual only.'},
  june: {base: 'tiles/june', name: 'Landsat + Sentinel-2, by acquisition date (June 2023)',
         note: 'Each frame is one acquisition date. Coverage is partial: blank areas were not observed (cloud or outside the pass), not dry. Landsat is 30 m and classified with thresholds tuned on Sentinel-2. The fade between dates is visual only.'},
  // SWOT daily passes hidden for now (tiles are still built in tiles/swot; uncomment to bring back).
  // swot: {base: 'tiles/swot', name: 'SWOT, daily passes (3 to 23 Jun 2023)',
  //        note: 'SWOT radar sees through cloud but each day covers only a swath. Unobserved areas are blank, not dry. 100 m pixels. The fade between days is visual only.'}
};
const frameMetas = {};                 // key -> {frames, bounds, ...}
let activeSet = null;
let frameMeta = null;                 // meta of the active set
const loadedPeriods = new Set();
const FRAME_LIFT = {min: 0.3, sat: 0.4};   // brighten Period 3 water: raise min (0..1) for lighter, sat for more colour
const LAND = {ok: false};            // true when tiles/styled/mappalette/period2_land is loaded (python/13)
// The merged terrain (python/14) is OFF: its data ends at the study-area edge, and both the 3D surface and
// the shading draw a dark cliff along that edge. Turn on only after it is padded out to the full map.
const USE_DEM3 = false;
const USE_DEM3_TERRAIN = false;      // keep false until the merged DEM is padded out to the full map (see README)
const TERRAIN_EX3 = 2.5;             // terrain exaggeration in Period 3, where the drained bed has modelled relief
const PALE = {ok: false};            // true when tiles/styled/mappalette/period2_pale is loaded
const PERIOD_BOUNDS = {};            // period -> [w, s, e, n] from its tiles' meta.json
// Camera that fits the whole tile set for a period (keeps the tilted 3D view, leaves room
// for the left panel). Falls back to the fixed camera if the tiles are not loaded.
function viewFor(p) {
  const b = PERIOD_BOUNDS[p];
  if (!b) return CAMERAS[p];
  const pitch = CAMERAS[p].pitch, bearing = CAMERAS[p].bearing;
  const cam = map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]],
    {padding: {top: 30, bottom: 30, left: 330, right: 30}, pitch, bearing});
  return cam ? {center: cam.center, zoom: cam.zoom, pitch, bearing} : CAMERAS[p];
}
let currentPeriod = 1;

const map = new maplibregl.Map({
  container: 'map',
  center: CAMERAS[2].center, zoom: CAMERAS[2].zoom,
  pitch: CAMERAS[2].pitch, bearing: CAMERAS[2].bearing, maxPitch: 80,
  style: {
    version: 8,
    sources: {
      imagery: {
        type: 'raster', tileSize: 256, maxzoom: 17,
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        attribution: 'Imagery © Esri (placeholder)'
      },
      dem: {
        type: 'raster-dem', url: 'https://tiles.mapterhorn.com/tilejson.json'
      }
    },
    layers: [
      {id: 'bg', type: 'background', paint: {'background-color': '#efe6cf'}},
      {id: 'imagery', type: 'raster', source: 'imagery'}
    ]
  }
});
map.addControl(new maplibregl.NavigationControl({visualizePitch: true}));

const T = {value: 1};        // 0..1 position along the canal sequence (Period 2: extend, then fill)
const T3 = {value: 1};       // Period 3 canal drain: 0 = still wet (end of Period 2), 1 = drained
// Seconds the reservoir takes to fill in Period 2. The canals then take 4 s to extend and 4 s to fill.
const RESV_SECONDS = 4;
// Fraction of the modelled fill to skip at the start (0 = begin dry, 0.15 = begin with the channel wet).
// Raise it to start later, lower it to start earlier.
const RESV_START = 0.15;
const RESV = {n: 0};         // number of fill frames in data/reservoir_levels.geojson (0 = no data)

// Master timeline t (0..1), Period 2 only: the reservoir fills over the modelled bed (RESV_SECONDS),
// then the canals EXTEND outward as dry beds (4 s), then water FILLS them in the same order (4 s).
// Canal order is distance from the reservoir (display sequence, not dates).
// In Period 3 the beds are shown complete and dry.
function applyTime() {
  if (!map.getLayer('canals')) return;
  const t = T.value;
  const p2 = currentPeriod === 2;
  // Period 2 timeline: reservoir fill first, then canals extend, then canals fill.
  // Without reservoir data the canals just extend and fill in halves.
  const R = RESV.n ? RESV_SECONDS / (RESV_SECONDS + 8) : 0, half = (1 - R) / 2;
  const resv = !p2 ? 1 : (R ? Math.min(1, t / R) : 1);
  const te = (t - R) / half, tf = (t - R - half) / half;
  const ext = !p2 ? 1 : (te <= 0 && R ? -1 : Math.min(1, Math.max(0, te)));
  const fill = !p2 ? -1 : (tf <= 0 ? -1 : Math.min(1, tf));
  if (map.getLayer('resv-dry')) {
    // Modelled fill: a pale dry-valley tint covers the not-yet-flooded part of the footprint and
    // shrinks frame by frame, revealing the real 2022 water underneath. The last frame has no tint.
    // Skip the first RESV_START of the sequence so it opens once the old channel is already wet.
    const lvl = Math.min(RESV.n - 1, Math.floor((RESV_START + resv * (1 - RESV_START)) * RESV.n));
    map.setFilter('resv-dry', ['all', ['==', ['get', 'kind'], 'dry'], ['==', ['get', 'lvl'], lvl]]);
  }
  // Dark-green crop plots: pale until the canals fill, then the normal layer fades in over it.
  if (map.getLayer('period2')) {
    map.setPaintProperty('period2', 'raster-opacity', (p2 && PALE.ok) ? Math.max(0, fill) : 1);
  }
  map.setFilter('canals', ['<=', ['get', 'ext_order'], ext]);
  map.setPaintProperty('canals', 'line-opacity',
    ['interpolate', ['linear'], ['-', ext, ['get', 'ext_order']], 0, 0.2, 0.08, 0.95]);
  // Period 3: colour beds by what satellites saw (merged by python/06).
  // Brown = observed no water; pale tan = not observable, assumed dry from
  // the premise that the inlets were cut; blues = water detected.
  map.setPaintProperty('canals', 'line-color', p2 ? '#857f74' :
    ['case',
      ['==', ['get', 'seg_status'], 'Water detected'], '#3e7a84',
      ['==', ['get', 'seg_status'], 'Some water detected'], '#59acaa',
      ['all', ['==', ['get', 'measurable'], 1],
             ['==', ['get', 'seg_status'], 'No water detected']], '#5c564b',
      '#ac9e8d']);
  // Period 3 drains in the REVERSE of the Period 2 fill order: the far ends lose
  // water first and the water retreats toward the reservoir, at the same pace the
  // canals filled. Only segments OBSERVED dry drain. Segments with water detected
  // stay wet, and 'assumed dry' segments (not measurable) stay beige and are never
  // drawn as water, pending a manual check. This is a display sequence: only the
  // end state (summer 2026) is measured.
  const d3 = T3.value;
  const stays = ['any',
    ['==', ['get', 'seg_status'], 'Water detected'],
    ['==', ['get', 'seg_status'], 'Some water detected']];
  const waterFilter = p2 ? ['<=', ['get', 'ext_order'], fill] :
    d3 >= 1 ? stays :
    ['any', stays,
      ['all', ['==', ['get', 'measurable'], 1],
              ['==', ['get', 'seg_status'], 'No water detected'],
              ['<=', ['get', 'ext_order'], 1 - d3]]];
  // Dark-green 2022 farmland dissolves into the 2026 imagery as the canals drain.
  if (map.getLayer('period2land')) {
    map.setPaintProperty('period2land', 'raster-opacity', currentPeriod === 3 ? Math.max(0, 1 - d3) : 0);
  }
  map.setFilter('canals-water', waterFilter);
  map.setFilter('canals-casing', waterFilter);
  map.setPaintProperty('canals-water', 'line-color', p2 ? '#59acaa' :
    ['case', ['==', ['get', 'seg_status'], 'Some water detected'], '#94ceba', '#59acaa']);
  map.setFilter('pumps', ['<=', ['get', 'ext_order'], ext]);
  // Period 3 intakes: ALL start in the active colour and change at the same moment
  // the canals do, as the retreating water passes each one (its nearest segment's
  // order). Still-connected stay blue; disconnected fade to a greyed teal; the
  // ones that cannot be measured turn beige = needs verification.
  const cut = 1 - d3 * 1.08;      // slightly past 1 so everything has faded by the end
  map.setPaintProperty('pumps', 'circle-color', p2 ?
    ['case', ['<=', ['get', 'ext_order'], fill], '#94ceba', '#857f74'] :
    ['case',
      ['==', ['get', 'measurable'], 0],
        ['interpolate', ['linear'], ['-', ['get', 'ext_order'], cut],
          0, '#94ceba', 0.08, '#cfc7ae'],
      ['==', ['get', 'pump_status'], 'Water within 500 m'], '#94ceba',
      ['==', ['get', 'pump_status'], 'No water within 500 m'],
        ['interpolate', ['linear'], ['-', ['get', 'ext_order'], cut],
          0, '#94ceba', 0.08, '#b9c9c3'],
      '#857f74']);
  const el = document.getElementById('status');
  if (el) el.textContent = p2 ?
    (resv < 1 ? 'Reservoir filling (modelled water level): ' + Math.round(resv * 100) + '%' :
     fill < 0 ? 'Canals extending: ' + Math.round(Math.max(0, ext) * 100) + '%' :
                'Canals filling: ' + Math.round(fill * 100) + '%') :
    (d3 >= 1 ? 'Drained: end state, summer 2026' :
               'Canals draining: ' + Math.round(d3 * 100) + '%');
}

function showPeriod(p) {
  currentPeriod = p;
  if (map.getLayer('hillshade')) {   // the 1775 map draws its own relief, so keep live shading faint
    map.setPaintProperty('hillshade', 'hillshade-exaggeration',
      p === 1 ? 0.06 : 0.12);
  }
  [1, 2, 3].forEach(k => {
    if (map.getLayer('period' + k)) {
      map.setLayoutProperty('period' + k, 'visibility', k === p ? 'visible' : 'none');
    }
  });
  if (map.getLayer('period2pale')) {
    map.setLayoutProperty('period2pale', 'visibility', p === 2 ? 'visible' : 'none');
  }
  if (map.getLayer('period2land')) {      // Period 2 land only, fades out as the canals drain
    map.setLayoutProperty('period2land', 'visibility', p === 3 ? 'visible' : 'none');
  }
  // Period 3 terrain with the modelled bed in the reservoir (python/14); Period 2 keeps the plain DEM.
  const has3 = !!map.getSource('dem3');
  if (map.getLayer('hillshade3')) map.setLayoutProperty('hillshade3', 'visibility', (p === 3 && has3) ? 'visible' : 'none');
  if (map.getLayer('hillshade') && has3) map.setLayoutProperty('hillshade', 'visibility', p === 3 ? 'none' : 'visible');
  // The merged terrain only covers the study area; used as 3D terrain it leaves cliff walls at its edge
  // (no elevation outside). So the 3D surface stays the plain DEM in every period; dem3 only shades.
  map.setTerrain({source: (USE_DEM3_TERRAIN && p === 3 && has3) ? 'dem3' : 'dem',
                  exaggeration: (USE_DEM3_TERRAIN && p === 3 && has3) ? TERRAIN_EX3 : 2.0});
  // Infrastructure belongs to the reservoir era; hide it for other periods.
  document.getElementById('canalbox').classList.toggle('hidden', p !== 2 && p !== 3);
  document.getElementById('p3key').classList.toggle('hidden', p !== 3);
  document.getElementById('drainbox').classList.toggle('hidden', !(p === 3 && frameMeta));
  applyFrames();
  // Beds and stations show in Periods 2 and 3; water only in Period 2.
  const infra = (p === 2 || p === 3) ? 'visible' : 'none';
  ['canals', 'pumps'].forEach(id => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', infra);
  });
  ['canals-water', 'canals-casing'].forEach(id => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', (p === 2 || p === 3) ? 'visible' : 'none');
  });
  if (map.getLayer('resv-dry')) map.setLayoutProperty('resv-dry', 'visibility', p === 2 ? 'visible' : 'none');
  // The (hidden) canal slider means 'fill the reservoir, extend, then fill canals' in Period 2 and 'drain' in Period 3.
  stopTimers();
  const ts = document.getElementById('t');
  if (ts) ts.value = (p === 3 ? T3.value : T.value) * 100;
  const tl = document.getElementById('tlabel');
  if (tl) tl.innerHTML = p === 3
    ? '<b>Canals drain</b> (reverse of the fill order, same pace; a display sequence, not dates. Only the end state is measured.)'
    : '<b>Reservoir fills, then canals extend and fill</b> (display order by distance from the reservoir, not dates)';
  applyTime();
  const note = document.getElementById('periodnote');
  if (note) {
    note.textContent =
      p === 1 ? (loadedPeriods.has(1)
        ? ''   // c.1775 map caveat moved to README.md
        : 'Period 1 tiles not built yet (run python/02_build_tiles.py on the georeferenced map).') :
      !loadedPeriods.has(p) ? 'No tiles for this period yet (run python/02_build_tiles.py).' : '';
  }
}

// ---- Period 3: observed drainage frames --------------------------------
// Each frame is an observed water-class layer. Position D (0..n-1, fractional)
// crossfades the two neighbouring frames. Only those two are made visible so
// the browser does not load all 16 tile sets at once.
const D = {value: 0};

function applyFrames() {
  if (!frameMeta) return;
  const n = frameMeta.frames.length;
  const show = currentPeriod === 3;
  const lo = Math.min(n - 1, Math.floor(D.value));
  const hi = Math.min(n - 1, lo + 1);
  const f = D.value - lo;
  frameMeta.frames.forEach((fr, i) => {
    const id = `frame_${activeSet}_${fr.id}`;
    if (!map.getLayer(id)) return;
    const on = show && (i === lo || i === hi);
    map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    if (!on) return;
    const op = lo === hi ? 1 : (i === lo ? 1 - f : f);
    map.setPaintProperty(id, 'raster-opacity', op);
    map.setPaintProperty(id, 'raster-fade-duration', 0);
    // Lift the observed-water colours toward the light end of the ramp so the water stands out.
    map.setPaintProperty(id, 'raster-brightness-min', FRAME_LIFT.min);
    map.setPaintProperty(id, 'raster-saturation', FRAME_LIFT.sat);
  });
  Object.keys(frameMetas).forEach(k => { if (k !== activeSet) frameMetas[k].frames.forEach(fr => {
    const id = `frame_${k}_${fr.id}`;
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
  }); });
  const near = frameMeta.frames[Math.round(D.value)];
  const lab = document.getElementById('dlabel');
  if (lab && near) {
    lab.textContent = (D.value % 1 === 0 ? 'Observation: ' : 'Between observations, nearest: ') + near.label;
  }
}

function chooseSet(key) {
  activeSet = key;
  frameMeta = frameMetas[key];
  const slider = document.getElementById('d');
  slider.max = (frameMeta.frames.length - 1) * 100;
  slider.value = 0;
  D.value = 0;
  document.getElementById('dnote').textContent = FRAME_SETS[key].note;
  applyFrames();
}

async function addFrameTiles() {
  for (const [key, set] of Object.entries(FRAME_SETS)) {
    try {
      const r = await fetch(set.base + '/meta.json');
      if (!r.ok) continue;
      frameMetas[key] = await r.json();
    } catch (e) { continue; }
    const meta = frameMetas[key];
    for (const fr of meta.frames) {
      const id = `frame_${key}_${fr.id}`;
      map.addSource(id, {
        type: 'raster', tileSize: 256, scheme: 'xyz',
        tiles: [`${set.base}/${fr.id}/{z}/{x}/{y}.png?v=${TILE_VERSION}`],
        bounds: meta.bounds, minzoom: meta.minzoom, maxzoom: meta.maxzoom
      });
      map.addLayer({id, type: 'raster', source: id,
                    layout: {visibility: 'none'}, paint: {'raster-opacity': 0}},
                   map.getLayer('canals') ? 'canals' : undefined);
    }
  }
  const keys = Object.keys(frameMetas);
  if (!keys.length) return;
  const sel = document.getElementById('dset');
  sel.innerHTML = keys.map(k => `<option value="${k}">${FRAME_SETS[k].name}</option>`).join('');
  sel.onchange = () => chooseSet(sel.value);
  chooseSet(keys[0]);
}

function applyBackdrop() {
  if (map.getLayer('imagery')) {
    // Satellite basemap for place context, muted and pale so it sits quietly under the
    // period layers (and under the 1775 map).
    map.setPaintProperty('imagery', 'raster-saturation', -0.7);
    map.setPaintProperty('imagery', 'raster-opacity', 0.5);
    map.setPaintProperty('imagery', 'raster-brightness-min', 0.2);
    map.setPaintProperty('imagery', 'raster-contrast', -0.2);
  }
  if (map.getLayer('bg')) map.setPaintProperty('bg', 'background-color', BG_COLOUR);
  // The imagery already has relief baked in; keep the live hillshade faint
  // so slopes are not shaded twice.
  if (map.getLayer('hillshade')) map.setPaintProperty('hillshade', 'hillshade-exaggeration', 0.12);
}

// (Re)load the period imagery for the current palette. Missing tiles are skipped.
async function addPeriodTiles() {
  for (const p of [1, 2, 3]) {
    const id = 'period' + p;
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
    loadedPeriods.delete(p);
    const base = periodBase(p);
    try {
      const r = await fetch(base + '/meta.json');
      if (!r.ok) continue;
      const meta = await r.json();
      if (meta.bounds) PERIOD_BOUNDS[p] = meta.bounds;
      map.addSource(id, {
        type: 'raster', tileSize: 256, scheme: 'xyz',
        tiles: [`${base}/{z}/{x}/{y}.png?v=${TILE_VERSION}`],
        bounds: meta.bounds, minzoom: meta.minzoom, maxzoom: meta.maxzoom
      });
      // Under the hillshade and the vector layers, so canals and pumps stay on top.
      const before = map.getLayer('resv-dry') ? 'resv-dry'
        : (map.getLayer('hillshade') ? 'hillshade' : (map.getLayer('canals') ? 'canals' : undefined));
      map.addLayer({id, type: 'raster', source: id, layout: {visibility: 'none'},
                    paint: {'raster-opacity': p === 1 ? P1_OPACITY : 1}}, before);
      loadedPeriods.add(p);
    } catch (e) { /* tiles not built yet */ }
  }
  // Optional pale-ag version of Period 2 (gee/11 with the dark-green crop class off).
  // Sits under the normal Period 2 layer, which fades in as the canals fill, so the
  // dense crop plots go from light green to dark green. Skipped if the tiles are absent.
  if (map.getLayer('period2pale')) map.removeLayer('period2pale');
  if (map.getSource('period2pale')) map.removeSource('period2pale');
  PALE.ok = false;
  if (map.getLayer('period2')) {
    const pbase = periodBase(2) + '_pale';
    try {
      const r = await fetch(pbase + '/meta.json');
      if (r.ok) {
        const meta = await r.json();
        map.addSource('period2pale', {
          type: 'raster', tileSize: 256, scheme: 'xyz',
          tiles: [`${pbase}/{z}/{x}/{y}.png?v=${TILE_VERSION}`],
          bounds: meta.bounds, minzoom: meta.minzoom, maxzoom: meta.maxzoom
        });
        map.addLayer({id: 'period2pale', type: 'raster', source: 'period2pale',
                      layout: {visibility: 'none'}, paint: {'raster-opacity': 1}}, 'period2');
        PALE.ok = true;
      }
    } catch (e) { /* pale tiles not built yet */ }
  }
  // Optional land-only Period 2 (python/13): fades out over the 2026 imagery during the Period 3 drain.
  if (map.getLayer('period2land')) map.removeLayer('period2land');
  if (map.getSource('period2land')) map.removeSource('period2land');
  LAND.ok = false;
  {
    const lbase = periodBase(2) + '_land';
    try {
      const r = await fetch(lbase + '/meta.json');
      if (r.ok) {
        const meta = await r.json();
        map.addSource('period2land', {
          type: 'raster', tileSize: 256, scheme: 'xyz',
          tiles: [`${lbase}/{z}/{x}/{y}.png?v=${TILE_VERSION}`],
          bounds: meta.bounds, minzoom: meta.minzoom, maxzoom: meta.maxzoom
        });
        const before = map.getLayer('resv-dry') ? 'resv-dry'
          : (map.getLayer('hillshade') ? 'hillshade' : undefined);
        map.addLayer({id: 'period2land', type: 'raster', source: 'period2land',
                      layout: {visibility: 'none'}, paint: {'raster-opacity': 0}}, before);
        LAND.ok = true;
      }
    } catch (e) { /* land-only tiles not built yet */ }
  }
}

map.on('load', async () => {
  map.setTerrain({source: 'dem', exaggeration: 2.0});   // a little; this landscape is flat
  // Period 3 terrain: Copernicus DEM with the modelled bed in the reservoir (python/14_terrain_p3.py).
  try {
    const r = USE_DEM3 ? await fetch('tiles/terrain_p3/meta.json') : {ok: false};
    if (r.ok) {
      const m = await r.json();
      map.addSource('dem3', {
        type: 'raster-dem', tileSize: 256, encoding: 'terrarium',
        tiles: [`tiles/terrain_p3/{z}/{x}/{y}.png?v=${TILE_VERSION}`],
        bounds: m.bounds, minzoom: m.minzoom, maxzoom: m.maxzoom
      });
    }
  } catch (e) { /* terrain tiles not built yet */ }

  // A missing vector file must not stop imagery and frames from loading.
  const EMPTY = {type: 'FeatureCollection', features: []};
  const loadJson = async url => {
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch (e) {
      console.warn('Could not load ' + url + ' (copy it into viewer/data/)');
      return EMPTY;
    }
  };
  const [canals, pumps, levelsData] = await Promise.all([
    loadJson('data/canals_manual.geojson'),
    loadJson('data/pumps_manual.geojson'),
    loadJson('data/reservoir_levels.geojson')    // python/12_reservoir_levels.py (reservoir fill frames)
  ]);
  map.addSource('canals-src', {type: 'geojson', data: canals});
  map.addSource('pumps-src', {type: 'geojson', data: pumps});

  const widthExpr = ['interpolate', ['linear'], ['zoom'], 7, 1.5, 11, 4];
  map.addLayer({             // dry canal bed
    id: 'canals', type: 'line', source: 'canals-src',
    layout: {'line-cap': 'round', 'line-join': 'round'},
    paint: {'line-color': '#857f74', 'line-width': widthExpr, 'line-opacity': 0.95}
  });
  map.addLayer({             // dark outline under the water, like the 1775 map's shoreline
    id: 'canals-casing', type: 'line', source: 'canals-src',
    layout: {'line-cap': 'round', 'line-join': 'round'},
    paint: {'line-color': '#123344',
            'line-width': ['interpolate', ['linear'], ['zoom'], 7, 2.6, 11, 5],
            'line-opacity': 0.9},
    filter: ['<=', ['get', 'ext_order'], -1]
  });
  map.addLayer({             // water filling the bed
    id: 'canals-water', type: 'line', source: 'canals-src',
    layout: {'line-cap': 'round', 'line-join': 'round'},
    paint: {'line-color': '#59acaa',
            'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 11, 2.8],
            'line-opacity': 0.95},
    filter: ['<=', ['get', 'ext_order'], -1]
  });
  map.addLayer({
    id: 'pumps', type: 'circle', source: 'pumps-src',
    paint: {'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 11, 6],
            'circle-color': '#857f74',
            'circle-stroke-color': '#123344', 'circle-stroke-width': 1.4}
  });

  const popup = new maplibregl.Popup({closeButton: false});
  map.on('click', 'canals', e => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(
      `<b>Canal ${p.canal_id}</b> (hand-traced)` +
      `<br><i>appearance order is a display sequence, not a date</i>`).addTo(map);
  });
  map.on('click', 'pumps', e => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(
      `<b>Intake station ${p.uid}</b> (hand-traced)` +
      (p.canal_id ? `<br>nearest canal ${p.canal_id}, ${p.dist_cnl} m away` : '')).addTo(map);
  });

  // Soft relief shading from the terrain data, over the imagery, under canals/frames.
  map.addLayer({
    id: 'hillshade', type: 'hillshade', source: 'dem',
    paint: {'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#2b2b5a',
            'hillshade-highlight-color': '#ffffff', 'hillshade-illumination-direction': 315}
  }, map.getLayer('canals') ? 'canals' : undefined);
  if (map.getSource('dem3')) {     // relief shading for Period 3 from the merged terrain
    map.addLayer({
      id: 'hillshade3', type: 'hillshade', source: 'dem3', layout: {visibility: 'none'},
      paint: {'hillshade-exaggeration': 0.3, 'hillshade-shadow-color': '#2b2b5a',
              'hillshade-highlight-color': '#ffffff', 'hillshade-illumination-direction': 315}
    }, map.getLayer('canals') ? 'canals' : undefined);
  }
  // Reservoir fill (Period 2): a pale dry-valley tint over the part of the reservoir not flooded yet.
  RESV.n = (levelsData && levelsData.meta && levelsData.meta.levels) || 0;
  if (RESV.n) {
    map.addSource('resv-src', {type: 'geojson', data: levelsData});
    map.addLayer({id: 'resv-dry', type: 'fill', source: 'resv-src',
      filter: ['==', ['get', 'kind'], 'dry'], layout: {visibility: 'none'},
      paint: {'fill-color': '#c9d4b8', 'fill-antialias': false}}, 'hillshade');
  }
  applyBackdrop();
  await addPeriodTiles();
  await addFrameTiles();
  map.jumpTo(viewFor(currentPeriod));
  showPeriod(currentPeriod);
  applyTime();
  showIntro('open');     // opening card first; Start then leads to the Period 1 card
});

document.querySelectorAll('#periods button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('#periods button').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    // The camera stays wherever you left it when switching periods (zoom, pan, tilt and rotation are kept).
    // To re-fit each period to its own map extent instead: map.flyTo({...viewFor(Number(b.dataset.p)), duration: 2500});
    showPeriod(Number(b.dataset.p));
    introEl.classList.remove('show'); introFor = null;
    autoplay(Number(b.dataset.p), true);   // each section plays when opened (after its story, if any)
  };
});

const slider = document.getElementById('t');
slider.oninput = () => {
  (currentPeriod === 3 ? T3 : T).value = slider.value / 100;
  applyTime();
};

const dslider = document.getElementById('d');
dslider.oninput = () => { D.value = dslider.value / 100; applyFrames(); };
let dtimer = null;       // observed-frames timer (Period 3)
let timer = null;        // reservoir / canal timeline timer
function stopTimers() {
  clearInterval(timer); clearInterval(dtimer);
  timer = null; dtimer = null;
}

// Period 2: the reservoir fills, then the canals extend and fill.
// Period 3: the canals drain. Pace: Period 2 is 300 ticks of 40 ms (12 s, three 4 s
// phases); the Period 3 canal drain is 100 ticks (4 s), the same pace as the canal fill.
function playTimeline() {
  clearInterval(timer);
  const S = currentPeriod === 3 ? T3 : T;
  const step = currentPeriod === 3 ? 0.01 : (RESV.n ? 1 / (25 * (RESV_SECONDS + 8)) : 0.005);
  S.value = 0; slider.value = 0; applyTime();
  timer = setInterval(() => {
    S.value = Math.min(1, S.value + step);
    slider.value = S.value * 100; applyTime();
    if (S.value >= 1) { clearInterval(timer); timer = null; }
  }, 40);
}

// Period 3: the observed water frames play alongside the canal drain.
function playFrames() {
  if (!frameMeta) return;
  clearInterval(dtimer);
  const max = frameMeta.frames.length - 1;
  D.value = 0; dslider.value = 0; applyFrames();
  dtimer = setInterval(() => {
    D.value = Math.min(max, D.value + 0.04);
    dslider.value = D.value * 100; applyFrames();
    if (D.value >= max) {
      clearInterval(dtimer); dtimer = null;
      if (outroArmed) {      // closing card after the last observed frame (once per play)
        outroArmed = false;
        setTimeout(() => { if (currentPeriod === 3 && introFor === null) showIntro('end'); }, 2500);
      }
    }
  }, 40);
}

// Story pop-ups shown before a section plays (text is editable here).
const INTROS = {
  open: {
    kicker: 'The Great Meadow',
    title: 'Water across time',
    body: 'Before 1956 the Great Meadow (Velykyi Luh) was a landscape of branching river channels, islands, wetlands and seasonal floods. ' +
          'The Kakhovka Dam submerged much of it beneath an industrial reservoir, and its destruction in June 2023 drained that reservoir and exposed the ground beneath. ' +
          'These changes are not simply the presence or absence of water: different water levels have produced different landscapes, ecologies and possibilities for human activity. ' +
          'This map follows the water through three periods, using historical records, satellite imagery and spatial analysis.',
    button: 'Start'
  },
  end: {
    kicker: 'Conclusion',
    title: 'Water as a changing condition',
    body: 'The Great Meadow’s history shows that water is not simply a boundary between land and river. It is a changing condition that shapes landscapes, ecosystems, infrastructure and human activity. ' +
          'Rather than proposing one fixed future, this research is a foundation for understanding how different water conditions, from reflooding to staying drained and everything between, might shape what comes next.',
    fine: 'Directions: navigation and accessibility · ecological and landscape conditions · adaptive spatial interventions · future water scenarios',
    button: 'Close'
  },
  about: {
    kicker: 'About this map',
    title: 'Four layers',
    body: 'The historical river: the original channels, islands, wetlands and seasonal flooding, and patterns of movement and occupation.\n\n' +
          'The reservoir and canal network: how the Kakhovka Dam turned the river into an engineered water system for agriculture, industry and infrastructure.\n\n' +
          'The re-emerging landscape: how drainage exposed terrain, altered shorelines and created new environmental conditions.\n\n' +
          'Changing water conditions: how different water levels might reshape navigation, ecosystems, vegetation, accessibility and human activity.',
    fine: 'The map shows the first three. The fourth is the direction of the wider research.',
    button: 'Close'
  },
  1: {
    kicker: 'Before the dam',
    title: 'The Great Meadow, before 1956',
    body: 'Before the Kakhovka Dam, the Great Meadow was a network of river channels, islands, wetlands and floodplains. ' +
          'It was neither entirely land nor entirely water: seasonal floods shifted the boundary between submerged and exposed ground, shaping navigation, settlement, vegetation and habitat. ' +
          'People adapted to this moving geography. Navigation depended on knowing the channels and shallow passages, while the floodplain supported varied ecological communities and human activity.',
    fine: 'No satellite imagery exists for this period. The map is a c.1775 historical map, georeferenced by hand; positions are approximate.'
  },
  3: {
    kicker: 'After the breach',
    title: 'Re-emergence, June 2023 to present',
    body: 'The Kakhovka Dam was breached in June 2023, and the reservoir drained, exposing terrain that had been underwater since 1956 and cutting off the canal network that depended on it. ' +
          'Old channels, mudflats, islands and floodplain surfaces began to re-emerge, and vegetation spread across parts of the exposed ground. ' +
          'The change was not a simple move from water to dry land. Some areas stayed wet, others alternated between flooded and exposed, producing a range of new environments. ' +
          'These differences matter for how the landscape evolves and for the ecological and human activities that depend on it.',
    fine: 'Water is shown from Sentinel-2 satellite composites, July 2023 to December 2025.'
  },
  2: {
    kicker: 'Reservoir era',
    title: 'The Kakhovka Dam, 1956',
    body: 'Between 1950 and 1956 the Soviet Union built the Kakhovka Dam and hydroelectric station on the lower Dnipro. ' +
          'Its reservoir drowned the Great Meadow and its network of channels, islands, reed beds and forests. ' +
          'It submerged villages and riverways, ending the natural spring floods that fed the meadow. ' +
          'In exchange came a ‘sea’ for shipping, hydropower and fishing, with a canal network carrying water to farmland, industry and towns across the dry south.',
    fine: 'The filling shown is modelled over a reconstructed bed, not the 1956 chronology.'
  }
};
const introEl = document.getElementById('intro');
let introFor = null;
const introSeen = new Set();    // period cards already shown this visit; they do not pop up again
function showIntro(p) {
  const c = INTROS[p];
  introFor = p;
  if (typeof p === 'number') introSeen.add(p);
  document.getElementById('intro-kicker').textContent = c.kicker;
  document.getElementById('intro-title').textContent = c.title;
  document.getElementById('intro-body').textContent = c.body;
  document.getElementById('intro-fine').textContent = c.fine || '';
  if (p === 2) { T.value = 0; applyTime(); }     // hold the empty start while the text is read
  if (p === 3) { T3.value = 0; applyTime(); D.value = 0; if (frameMeta) applyFrames(); }
  introEl.classList.add('show');
  document.getElementById('intro-go').focus({preventScroll: true});
}
function closeIntro() {
  if (introFor === null) return;
  const p = introFor;
  introFor = null;
  introEl.classList.remove('show');
  if (p === 'open') { showIntro(1); return; }       // opening card, then the Period 1 card
  if (typeof p !== 'number') return;                // 'end' and 'about' just close
  if (p === 3) outroArmed = true;                   // closing card follows once the observed frames finish
  if (currentPeriod === p) autoplay(p);       // intro already shown, so this plays straight away
}
let outroArmed = false;
document.getElementById('about').onclick = () => { stopTimers(); showIntro('about'); };
document.getElementById('intro-go').onclick = closeIntro;
document.addEventListener('keydown', e => {
  if (introFor !== null && (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape')) { e.preventDefault(); closeIntro(); }
});

// Every section plays when opened; the Replay buttons restart it from the beginning.
// withIntro = true (opening a section) shows its story pop-up first, if it has one.
function autoplay(p, withIntro) {
  stopTimers();
  if (withIntro && INTROS[p] && !introSeen.has(p)) { showIntro(p); return; }
  if (p === 2) playTimeline();
  if (p === 3) { playTimeline(); playFrames(); }
}
document.getElementById('dplay').onclick = () => autoplay(currentPeriod);

document.getElementById('play').onclick = () => autoplay(currentPeriod);
