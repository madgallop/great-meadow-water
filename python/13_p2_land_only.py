#!/usr/bin/env python3
"""
13_p2_land_only.py
==================
Make a LAND-ONLY copy of the Period 2 (summer 2022) styled imagery: the reservoir is cut out (transparent), so
only the land around it remains, with its dark-green farmland.

The viewer lays this over the Period 3 (2026) imagery and fades it out as the canals drain, so the dark green
farmland dissolves into whatever 2026 shows, while the reservoir area keeps showing the 2026 drained bed and the
observed water frames. (A browser cannot cut a picture along an outline, so the cut-out is baked in here.)

INPUT   data/gee_outputs/period2_reservoir_mappalette.tif   (3-band RGB, from gee/11)
        data/gee_outputs/Reservoir_footprint.tif            (1 = reservoir, from gee/01)
OUTPUT  data/derived/period2_land_only.tif                  (RGBA; alpha 0 inside the reservoir)

THEN TILE IT (from EarthEngines/viz):
    python python/02_build_tiles.py --src data/derived/period2_land_only.tif \
        --out viewer/tiles/styled/mappalette/period2_land --zmin 7 --zmax 12 --keep-alpha

USAGE
    python python/13_p2_land_only.py
    python python/13_p2_land_only.py --grow 4          # cut a little wider around the shore
"""
import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.warp import reproject
from scipy import ndimage as ndi


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p2", default="data/gee_outputs/period2_reservoir_mappalette.tif")
    ap.add_argument("--footprint", default="data/gee_outputs/Reservoir_footprint.tif")
    ap.add_argument("--out", default="data/derived/period2_land_only.tif")
    ap.add_argument("--min-km2", type=float, default=2.0, help="ignore footprint blobs smaller than this")
    ap.add_argument("--grow", type=int, default=3, help="pixels to widen the cut so the shore outline goes too")
    a = ap.parse_args()

    with rasterio.open(a.p2) as ds:
        rgb = ds.read([1, 2, 3])
        prof = ds.profile
        transform, crs, shape = ds.transform, ds.crs, (ds.height, ds.width)
        pix_m2 = abs(ds.res[0] * ds.res[1])

    # footprint onto the Period 2 grid
    fp = np.zeros(shape, dtype="uint8")
    with rasterio.open(a.footprint) as fs:
        reproject(source=rasterio.band(fs, 1), destination=fp, src_transform=fs.transform,
                  src_crs=fs.crs, dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.nearest)
    water = fp == 1
    lab, n = ndi.label(water)
    if n:
        sizes = np.bincount(lab.ravel())
        keep = sizes * pix_m2 >= a.min_km2 * 1e6
        keep[0] = False
        water = keep[lab]
    cut = ndi.binary_dilation(water, iterations=a.grow) if a.grow else water
    nodata = rgb.max(axis=0) == 0                      # 'no clear observation' black from GEE
    alpha = np.where(cut | nodata, 0, 255).astype("uint8")
    print(f"cut out {100 * cut.mean():.1f}% of the image (reservoir + {a.grow} px margin); "
          f"{100 * nodata.mean():.1f}% was no-data")

    prof.pop("nodata", None)
    prof.update(count=4, dtype="uint8", compress="deflate", tiled=True, photometric="RGB",
                blockxsize=512, blockysize=512)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(a.out, "w", **prof) as dst:
        dst.write(rgb * (alpha > 0).astype("uint8"), indexes=[1, 2, 3])
        dst.write(alpha, 4)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
