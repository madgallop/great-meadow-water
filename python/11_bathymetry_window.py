#!/usr/bin/env python3
"""
11_bathymetry_window.py
=======================
STEP 1 of a realistic reservoir fill: look inside the model bathymetry grids.

The four grids in data_for_water_model/model_data/basemaps are ~1.5 GB ASCII rasters (30 m cells,
Web Mercator metres, 18553 x 10849). They were made for a GeoClaw dam-break model, so they are
modelled / adjusted surfaces, NOT surveyed bed. This script
  1. cuts a window around the reservoir out of each grid (streaming the text, so memory stays small),
  2. saves it as a compressed GeoTIFF (EPSG:3395, the grids' own Mercator) in data/bathymetry/,
  3. prints elevation statistics INSIDE the reservoir footprint, so we can tell which grid holds the
     reservoir bed and what vertical datum it uses (the reservoir surface should sit near 16 m).
Nothing is shown in the viewer yet.

USAGE (from EarthEngines/viz, greatmeadow env; takes a few minutes per grid)
    python python/11_bathymetry_window.py
    python python/11_bathymetry_window.py --grids dniep_topo_bathyadj      # one grid only
Paste the printed statistics back so the fill model can be set up.
"""
import argparse
import itertools
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

R = 6378137.0
GRID_CRS = "EPSG:3395"   # World Mercator (ellipsoidal), matches the grids' .prj


def merc(lon, lat):
    x = R * np.radians(lon)
    y = R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    return x, y


def read_header(path):
    h = {}
    with open(path) as f:
        for _ in range(6):
            k, v = f.readline().split()
            h[k.lower()] = float(v)
    return h


def window(path, h, x0, y0, x1, y1):
    """Rows/cols covering the Mercator box. ASC rows run north to south."""
    cs = h["cellsize"]
    ncols, nrows = int(h["ncols"]), int(h["nrows"])
    ytop = h["yllcorner"] + nrows * cs
    c0 = max(0, int((x0 - h["xllcorner"]) // cs))
    c1 = min(ncols, int((x1 - h["xllcorner"]) // cs) + 1)
    r0 = max(0, int((ytop - y1) // cs))
    r1 = min(nrows, int((ytop - y0) // cs) + 1)
    out = np.empty((r1 - r0, c1 - c0), dtype="float32")
    with open(path) as f:
        rows = itertools.islice(f, 6 + r0, 6 + r1)
        for i, line in enumerate(rows):
            out[i] = np.fromstring(line, sep=" ", dtype="float32")[c0:c1]
    out[out <= -9998] = np.nan
    transform = from_origin(h["xllcorner"] + c0 * cs, ytop - r0 * cs, cs, cs)
    return out, transform


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--basemaps", default="../data_for_water_model/model_data/basemaps")
    ap.add_argument("--grids", nargs="*", default=None, help="file stems; default = all .asc in basemaps")
    ap.add_argument("--footprint", default="data/gee_outputs/Reservoir_footprint.tif")
    ap.add_argument("--out", default="data/bathymetry")
    a = ap.parse_args()

    base = Path(a.basemaps)
    stems = a.grids or sorted(p.stem for p in base.glob("*.asc"))
    Path(a.out).mkdir(parents=True, exist_ok=True)

    # reservoir footprint -> Web Mercator geometry
    with rasterio.open(a.footprint) as ds:
        band = ds.read(1)
        polys = [shape(g) for g, v in features.shapes((band == 1).astype("uint8"), mask=band == 1,
                                                      transform=ds.transform)]
        crs = ds.crs
    polys = [p for p in polys if p.area >= 2e6]
    foot = unary_union(polys)
    # The grids' .prj says MERCATOR on the WGS84 SPHEROID = ellipsoidal Mercator (EPSG:3395),
    # NOT Web Mercator (3857). They differ by ~30 km in y at this latitude.
    tf = Transformer.from_crs(crs, GRID_CRS, always_xy=True).transform
    foot_m = shp_transform(tf, foot)
    x0, y0, x1, y1 = foot_m.bounds
    pad = 3000
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    print(f"footprint box (EPSG:3395 m): x {x0:,.0f} to {x1:,.0f}, y {y0:,.0f} to {y1:,.0f}")

    for stem in stems:
        path = base / f"{stem}.asc"
        if not path.exists():
            print(f"skip {stem}: not found")
            continue
        h = read_header(path)
        ytop = h["yllcorner"] + h["nrows"] * h["cellsize"]
        if y1 > ytop:
            print(f"note: {stem} stops at y={ytop:,.0f}; the reservoir's northern tip is cut off")
        print(f"reading {stem} ...")
        arr, transform = window(path, h, x0, y0, x1, y1)
        out_tif = Path(a.out) / f"{stem}_window.tif"
        with rasterio.open(out_tif, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                           dtype="float32", crs=GRID_CRS, transform=transform, nodata=np.nan,
                           compress="deflate", predictor=3) as dst:
            dst.write(arr, 1)
        inside = features.rasterize([(foot_m, 1)], out_shape=arr.shape, transform=transform,
                                    fill=0, dtype="uint8").astype(bool)
        v = arr[inside & np.isfinite(arr)]
        print(f"  saved {out_tif}  ({arr.shape[1]} x {arr.shape[0]} cells)")
        if v.size == 0:
            print("  no data inside the footprint")
            continue
        pct = np.percentile(v, [0, 1, 5, 25, 50, 75, 95, 99, 100])
        print("  elevation inside footprint (m): min/1/5/25/50/75/95/99/max = " +
              " / ".join(f"{p:.1f}" for p in pct))
        for lvl in (0, 10, 16, 17.42):
            print(f"  share of footprint cells below {lvl:>5} m: {100 * (v < lvl).mean():5.1f}%")


if __name__ == "__main__":
    main()
