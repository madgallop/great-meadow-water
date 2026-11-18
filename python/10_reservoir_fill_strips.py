#!/usr/bin/env python3
"""
10_reservoir_fill_strips.py
===========================
Cut the reservoir footprint into thin west-to-east strips so the viewer can
play a "reservoir filling" animation in Period 2.

WHAT THE ANIMATION IS (and is not)
----------------------------------
The strips reveal the reservoir's summer-2022 shape from the WEST (the dam end)
to the EAST. It is a STYLISED SWEEP: not the real 1956 filling chronology, and
not a rising water level (that needs bathymetry, which is not processed yet).
No satellite imagery exists before the fill, so before the sweep the footprint
is drawn as a flat tint. When the sweep finishes, the viewer fades the overlay
out and the real 2022 imagery shows underneath.

INPUT   data/gee_outputs/Reservoir_footprint.tif   (gee/01; 1 = reservoir, EPSG:32636)
OUTPUT  viewer/data/reservoir_fill.geojson
          kind 'bed'   the whole footprint (one polygon feature)
          kind 'water' one polygon per strip, property strip = 0..N-1 (west to east)
          kind 'edge'  the shoreline line inside each strip (same strip index)
          top-level "meta": {"strips": N, "bounds": [...]}

USAGE (from EarthEngines/viz, greatmeadow env)
    python python/10_reservoir_fill_strips.py
    python python/10_reservoir_fill_strips.py --strips 240 --simplify 40
Needs rasterio, shapely and pyproj (all come with geopandas).
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import features
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union


def only(geom, kinds):
    """Keep just the parts of an intersection result that have the wanted
    geometry types (intersections can also return stray points or lines)."""
    if geom.is_empty:
        return None
    if geom.geom_type in kinds:
        return geom
    if hasattr(geom, "geoms"):
        parts = [g for g in geom.geoms if g.geom_type in kinds]
        if parts:
            return unary_union(parts)
    return None


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
    ap.add_argument("--footprint", default="data/gee_outputs/Reservoir_footprint.tif")
    ap.add_argument("--out", default="viewer/data/reservoir_fill.geojson")
    ap.add_argument("--strips", type=int, default=200, help="number of west-to-east strips")
    ap.add_argument("--simplify", type=float, default=60.0, help="metres; smooths the stair-steps")
    ap.add_argument("--min-km2", type=float, default=2.0, help="drop footprint blobs smaller than this")
    a = ap.parse_args()

    with rasterio.open(a.footprint) as ds:
        band = ds.read(1)
        transform, crs = ds.transform, ds.crs
    if crs is None or crs.is_geographic:
        raise SystemExit(f"expected a projected CRS (EPSG:32636), found {crs}")
    mask = band == 1
    print(f"footprint pixels: {int(mask.sum()):,}")
    polys = [shape(g) for g, v in features.shapes(mask.astype("uint8"), mask=mask,
                                                  transform=transform)]
    polys = [p for p in polys if p.area >= a.min_km2 * 1e6]
    if not polys:
        raise SystemExit("no footprint polygons found; check the footprint file")
    geom = unary_union(polys).simplify(a.simplify, preserve_topology=True)
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    geo = shp_transform(to_ll, geom)
    minx, miny, maxx, maxy = geo.bounds
    print(f"footprint: {geom.area / 1e6:,.0f} km2, lon {minx:.3f} to {maxx:.3f}, "
          f"lat {miny:.3f} to {maxy:.3f}")

    feats = [feature(geo, {"kind": "bed"})]
    edges = np.linspace(minx, maxx, a.strips + 1)
    boundary = geo.boundary
    n_water = n_edge = 0
    for i in range(a.strips):
        cell = box(edges[i], miny - 1, edges[i + 1], maxy + 1)
        part = only(geo.intersection(cell), ("Polygon", "MultiPolygon"))
        if part is not None:
            feats.append(feature(part, {"kind": "water", "strip": i}))
            n_water += 1
        line = only(boundary.intersection(cell), ("LineString", "MultiLineString"))
        if line is not None:
            feats.append(feature(line, {"kind": "edge", "strip": i}))
            n_edge += 1
    out = {"type": "FeatureCollection",
           "meta": {"strips": a.strips, "bounds": [round(minx, 5), round(miny, 5),
                                                    round(maxx, 5), round(maxy, 5)]},
           "features": feats}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, separators=(",", ":")))
    size = Path(a.out).stat().st_size / 1e6
    print(f"wrote {a.out}: {a.strips} strips ({n_water} with water, {n_edge} with shoreline), {size:.1f} MB")


if __name__ == "__main__":
    main()
