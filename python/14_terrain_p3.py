#!/usr/bin/env python3
"""
14_terrain_p3.py
================
Give the drained reservoir bed real relief in Period 3.

The Copernicus DEM shows the reservoir's flat WATER SURFACE, so a drained bed looks like a billiard table. This
script replaces the DEM inside the reservoir footprint with the modelled bathymetry (python/11 window), and writes
a terrain-RGB image ("terrarium" encoding) that the viewer uses as its Period 3 terrain and hillshade.

The modelled bed (GLOBathy / river-like grids from the Lehnigk et al. SWOT study) is a model, not a survey. Label it.

STEPS
 1. read the Copernicus DEM (EPSG:32636) and warp the bathymetry window onto the same grid
 2. estimate any height offset between the two on land just outside the footprint (printed, then applied)
 3. blend: DEM outside the footprint, bathymetry + offset inside, softened over a few pixels at the shore
 4. encode as terrarium RGB: height = (R*256 + G + B/256) - 32768

INPUT   data/gee_outputs/Copernicus_DEM_UTM36.tif
        data/bathymetry/<grid>_window.tif        (python/11_bathymetry_window.py, EPSG:3395)
        data/gee_outputs/Reservoir_footprint.tif
OUTPUT  data/derived/terrain_p3_terrarium.tif

THEN TILE IT (from EarthEngines/viz; nearest-neighbour so the encoding is not averaged):
    python python/02_build_tiles.py --src data/derived/terrain_p3_terrarium.tif \
        --out viewer/tiles/terrain_p3 --zmin 7 --zmax 12 --resampling near --keep-alpha

USAGE
    python python/14_terrain_p3.py
    python python/14_terrain_p3.py --grid dniep_topo_bathyadj
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
    ap.add_argument("--dem", default="data/gee_outputs/Copernicus_DEM_UTM36.tif")
    ap.add_argument("--grid", default="dniep_topo_ndh_bathyadj_low_wide")
    ap.add_argument("--bathy-dir", default="data/bathymetry")
    ap.add_argument("--footprint", default="data/gee_outputs/Reservoir_footprint.tif")
    ap.add_argument("--out", default="data/derived/terrain_p3_terrarium.tif")
    ap.add_argument("--feather", type=float, default=2.0, help="pixels of softening at the shore")
    ap.add_argument("--offset", type=float, default=None, help="force the height offset (m) instead of estimating")
    a = ap.parse_args()

    with rasterio.open(a.dem) as ds:
        cop = ds.read(1).astype("float32")
        if ds.nodata is not None:
            cop[cop == ds.nodata] = np.nan
        transform, crs, prof = ds.transform, ds.crs, ds.profile
    shape = cop.shape
    print(f"DEM {shape[1]} x {shape[0]} px, {abs(transform.a):.0f} m")

    bed = np.full(shape, np.nan, dtype="float32")
    with rasterio.open(Path(a.bathy_dir) / f"{a.grid}_window.tif") as bs:
        reproject(source=rasterio.band(bs, 1), destination=bed, src_transform=bs.transform,
                  src_crs=bs.crs, dst_transform=transform, dst_crs=crs,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)

    fp = np.zeros(shape, dtype="uint8")
    with rasterio.open(a.footprint) as fs:
        reproject(source=rasterio.band(fs, 1), destination=fp, src_transform=fs.transform,
                  src_crs=fs.crs, dst_transform=transform, dst_crs=crs, resampling=Resampling.nearest)
    water = fp == 1
    lab, n = ndi.label(water)
    if n:
        sizes = np.bincount(lab.ravel())
        keep = sizes * abs(transform.a * transform.e) >= 2e6
        keep[0] = False
        water = keep[lab]

    # height offset between the two surfaces, on land just outside the footprint
    near = ndi.binary_dilation(water, iterations=int(5000 / abs(transform.a)))
    far = ndi.binary_dilation(water, iterations=int(1500 / abs(transform.a)))
    ring = near & ~far & np.isfinite(cop) & np.isfinite(bed)
    if a.offset is not None:
        offset = a.offset
    elif ring.sum() > 1000:
        d = (cop - bed)[ring]
        offset = float(np.median(d))
        print(f"land just outside the footprint: DEM - bathymetry median {offset:.2f} m "
              f"(spread {np.percentile(d, 10):.2f} to {np.percentile(d, 90):.2f}, {ring.sum():,} px)")
    else:
        offset = 0.0
        print("could not estimate an offset (too few land pixels); using 0")
    print(f"offset applied: {offset:.2f} m")

    w = ndi.gaussian_filter(water.astype("float32"), a.feather) if a.feather > 0 else water.astype("float32")
    use = np.isfinite(bed)
    merged = np.where(use, cop * (1 - w) + (bed + offset) * w, cop)
    merged = np.where(np.isfinite(merged), merged, 0).astype("float32")
    ins = water & use
    print(f"inside the footprint: DEM {np.nanmin(cop[ins]):.1f} to {np.nanmax(cop[ins]):.1f} m  ->  "
          f"merged {merged[ins].min():.1f} to {merged[ins].max():.1f} m (relief {np.ptp(merged[ins]):.1f} m)")

    v = np.clip(merged + 32768.0, 0, 65535.99)
    R = np.floor(v / 256).astype("uint8")
    G = np.floor(v % 256).astype("uint8")
    B = np.floor((v - np.floor(v)) * 256).astype("uint8")

    prof.pop("nodata", None)
    prof.update(count=3, dtype="uint8", compress="deflate", tiled=True, photometric="RGB",
                blockxsize=512, blockysize=512)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(a.out, "w", **prof) as dst:
        dst.write(np.stack([R, G, B]))
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue]
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
