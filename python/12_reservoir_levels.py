#!/usr/bin/env python3
"""
12_reservoir_levels.py
======================
STEP 2 of a realistic reservoir fill. Builds the "dry valley" masks the viewer uses to REVEAL the real
2022 reservoir as the water spreads over the modelled bed.

HOW IT LOOKS
------------
The real summer-2022 imagery (your styled tiles) stays underneath. Over it sits a pale dry-valley tint covering
the part of the reservoir not flooded yet. Frame by frame the tint retreats along the modelled fill and the
real water appears behind it. The last frame has no tint, so there is no fade and no colour jump.

WHAT THIS IS (and is not)
-------------------------
- A priority flood over a MODELLED bed (GLOBathy statistical bathymetry by default), not a survey and not
  the real 1956-58 filling chronology. Water starts at the dam and always takes the lowest reachable cell
  next; cells that open at the same level (behind a sill) spread outward from where the water entered. That
  spreading front is a display ordering. Playback pace is a display choice, not a time scale.
- Limited to the reservoir footprint from gee/01 (summer-2022 shoreline). Footprint cells the flood cannot
  reach are revealed last, lowest bed first.

INPUT   data/bathymetry/<grid>_window.tif   (python/11_bathymetry_window.py, EPSG:3395)
        data/gee_outputs/Reservoir_footprint.tif
OUTPUT  viewer/data/reservoir_levels.geojson
          kind 'dry'  one polygon per frame = footprint minus flooded area; properties lvl (0..N-2), level_m
          (the last frame, lvl N-1, has nothing left to cover, so it is not written)
          top-level "meta": {"levels": N, "level_m": [...], "full_m": 17.42, "grid": "<name>", "mode": "dry"}

USAGE (from EarthEngines/viz, greatmeadow env)
    python python/12_reservoir_levels.py
    python python/12_reservoir_levels.py --grid dniep_topo_ndh_bathyadj_low_wide --levels 60
Needs rasterio, shapely, pyproj, scipy (all in the greatmeadow env).
"""
import argparse
import heapq
import json
import warnings
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage as ndi
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

warnings.filterwarnings("ignore", category=DeprecationWarning)


def round_coords(obj, nd=5):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), nd) for v in obj]
        return [round_coords(o, nd) for o in obj]
    return obj


def feature(geom, props):
    g = mapping(geom)
    return {"type": "Feature", "properties": props,
            "geometry": {"type": g["type"], "coordinates": round_coords(g["coordinates"])}}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", default="dniep_topo_bathyadj")
    ap.add_argument("--bathy-dir", default="data/bathymetry")
    ap.add_argument("--footprint", default="data/gee_outputs/Reservoir_footprint.tif")
    ap.add_argument("--out", default="viewer/data/reservoir_levels.geojson")
    ap.add_argument("--full", type=float, default=17.42, help="full reservoir surface (m, grid datum)")
    ap.add_argument("--levels", type=int, default=48)
    ap.add_argument("--step", type=int, default=2, help="downsample factor (2 = 60 m cells)")
    ap.add_argument("--dam-lon", type=float, default=33.37, help="Kakhovka dam, for the seed")
    ap.add_argument("--dam-lat", type=float, default=46.78)
    ap.add_argument("--dam-radius", type=float, default=25.0, help="km around the dam to look for the seed")
    ap.add_argument("--dilate", type=int, default=3, help="allow water this many cells beyond the footprint")
    ap.add_argument("--smooth", type=float, default=2.0, help="gaussian sigma (cells) to soften the front")
    ap.add_argument("--simplify", type=float, default=100.0, help="metres")
    a = ap.parse_args()

    src = Path(a.bathy_dir) / f"{a.grid}_window.tif"
    with rasterio.open(src) as ds:
        bed = ds.read(1)[::a.step, ::a.step]
        t = ds.transform
        grid_crs = ds.crs
    tr = Affine(t.a * a.step, t.b, t.c, t.d, t.e * a.step, t.f)
    cell = abs(tr.a)
    G = np.cos(np.radians(a.dam_lat)) ** 2     # Mercator area -> ground area at this latitude
    print(f"bed grid {bed.shape[1]} x {bed.shape[0]} cells of {cell:.0f} m")

    # footprint in the grid's CRS
    with rasterio.open(a.footprint) as fp:
        band = fp.read(1)
        polys = [shape(g) for g, v in features.shapes((band == 1).astype("uint8"), mask=band == 1,
                                                      transform=fp.transform)]
        fcrs = fp.crs
    polys = [p for p in polys if p.area >= 2e6]
    foot = unary_union(polys)
    foot_g = shp_transform(Transformer.from_crs(fcrs, grid_crs, always_xy=True).transform, foot)
    inside = features.rasterize([(foot_g, 1)], out_shape=bed.shape, transform=tr, fill=0,
                                dtype="uint8").astype(bool)
    allow = ndi.binary_dilation(inside, iterations=a.dilate) if a.dilate else inside
    valid = np.isfinite(bed)

    # SEED = the lowest bed cell near the dam, inside the MAIN connected body of floodable cells.
    ok = allow & valid & (bed <= a.full)
    lab, ncomp = ndi.label(ok)
    csize = np.bincount(lab.ravel())[1:]
    big = 1 + int(np.argmax(csize))
    print(f"floodable pieces: {ncomp}; largest = {csize.max() * cell * cell * G / 1e6:,.0f} km2 "
          f"(all pieces {csize.sum() * cell * cell * G / 1e6:,.0f} km2)")
    comp = lab == big
    nr, nc = bed.shape
    xs = tr.c + (np.arange(nc) + 0.5) * tr.a
    ys = tr.f + (np.arange(nr) + 0.5) * tr.e
    dx, dy = Transformer.from_crs("EPSG:4326", grid_crs, always_xy=True).transform(a.dam_lon, a.dam_lat)
    # Seed INSIDE the reservoir footprint (upstream of the dam), not on the downstream river side:
    # the dam crest in the model would otherwise be the first thing the water has to climb.
    rad = a.dam_radius * 1000 / np.cos(np.radians(a.dam_lat))     # km on the ground -> Mercator m
    near = comp & inside & (((xs[None, :] - dx) ** 2 + (ys[:, None] - dy) ** 2) < rad ** 2)
    if not near.any():
        print("warning: no floodable footprint cells near the dam coordinates; using the lowest footprint cell")
        near = comp & inside
    sr, sc = np.unravel_index(np.argmin(np.where(near, bed, np.inf)), bed.shape)
    sx, sy = xs[sc], ys[sr]
    slon, slat = Transformer.from_crs(grid_crs, "EPSG:4326", always_xy=True).transform(sx, sy)
    print(f"seed at {slat:.3f}N {slon:.3f}E, bed height {bed[sr, sc]:.1f} m; full level {a.full} m")

    # PRIORITY FLOOD from the seed (lowest reachable cell next; ties spread FIFO).
    order = np.full(bed.shape, -1, dtype=np.int32)
    key = np.full(bed.shape, np.nan, dtype=np.float32)
    seen = np.zeros(bed.shape, dtype=bool)
    heap = [(float(bed[sr, sc]), 0, sr, sc)]
    seen[sr, sc] = True
    counter, n_done, level_now = 1, 0, -1e9
    while heap:
        k0, _, r, c = heapq.heappop(heap)
        level_now = max(level_now, k0)
        order[r, c] = n_done
        key[r, c] = level_now
        n_done += 1
        for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < nr and 0 <= cc < nc and ok[rr, cc] and not seen[rr, cc]:
                seen[rr, cc] = True
                heapq.heappush(heap, (max(float(bed[rr, cc]), level_now), counter, rr, cc))
                counter += 1
    # footprint cells the flood did not reach: revealed last, lowest bed first
    left = np.flatnonzero((inside & (order < 0)).ravel())
    if left.size:
        b = bed.ravel()[left]
        b = np.where(np.isfinite(b), b, np.inf)
        order.flat[left[np.argsort(b, kind="stable")]] = n_done + np.arange(left.size, dtype=np.int32)
    total = n_done + left.size
    print(f"flooded from the dam: {n_done * cell * cell * G / 1e6:,.0f} km2; "
          f"revealed last (not reachable): {left.size * cell * cell * G / 1e6:,.0f} km2; "
          f"footprint {foot.area / 1e6:,.0f} km2")
    counts = np.linspace(total / a.levels, total, a.levels).astype(int)
    levels = []
    for cn in counts:
        kk = key[order == (cn - 1)]
        levels.append(float(kk[0]) if kk.size and np.isfinite(kk[0]) else a.full)

    to_ll = Transformer.from_crs(grid_crs, "EPSG:4326", always_xy=True).transform
    feats = []
    for i in range(a.levels - 1):          # the last frame has nothing left to cover
        m = (order >= 0) & (order < counts[i])
        if a.smooth > 0:
            m = ndi.gaussian_filter(m.astype("float32"), a.smooth) > 0.5
        water = unary_union([shape(g) for g, v in features.shapes(m.astype("uint8"), mask=m, transform=tr)])
        water = water.buffer(cell)         # close hairline gaps at the shore
        dry = foot_g.difference(water).simplify(a.simplify, preserve_topology=True)
        if not dry.is_empty:
            feats.append(feature(shp_transform(to_ll, dry),
                                 {"kind": "dry", "lvl": i, "level_m": round(levels[i], 2)}))
        print(f"  frame {i + 1:>2}/{a.levels}: level {levels[i]:6.2f} m   dry left {dry.area * G / 1e6:7.0f} km2")

    out = {"type": "FeatureCollection",
           "meta": {"levels": a.levels, "level_m": [round(l, 2) for l in levels],
                    "full_m": a.full, "grid": a.grid, "mode": "dry"},
           "features": feats}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {a.out}  ({Path(a.out).stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
