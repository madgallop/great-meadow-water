#!/usr/bin/env python3
"""
02_build_tiles.py
=================
Turn a GEE true-colour GeoTIFF (from gee/02_period_composites.js) into static
XYZ map tiles the viewer can load from any web host. No Earth Engine and no
Python are needed at view time.

HOW IT WORKS
------------
1. Preflight with rasterio: checks CRS, band count, dtype, bounds, and writes
   a small meta.json (lon/lat bounds + zoom range) next to the tiles.
2. Runs GDAL's gdal2tiles from THIS environment (not Homebrew's) to cut
   web-mercator PNG tiles in XYZ order. Pixels that are pure black
   (0,0,0) are the "no clear observation" fill from the GEE script and are
   made transparent, so the basemap shows through gaps.

USAGE (from EarthEngines/viz, with the greatmeadow env active)
-----
    python python/02_build_tiles.py \
        --src data/gee_outputs/period2_reservoir_truecolour.tif \
        --out viewer/tiles/period2 --zmin 7 --zmax 12

    python python/02_build_tiles.py --src ... --out ... --dry-run   # preflight only

ZOOM CHOICE
-----------
The export is 20 m/pixel. Web-mercator zoom 12 is ~28 m/pixel at this
latitude, so z12 is about the sharpest level that adds information; going to
z13 only upsamples. Tile count grows ~4x per zoom level.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds


def find_gdal2tiles():
    """Use this environment's gdal2tiles, never whatever is first on PATH."""
    script = Path(sys.prefix) / "bin" / "gdal2tiles.py"
    if script.exists():
        return [sys.executable, str(script)]
    return [sys.executable, "-m", "osgeo_utils.gdal2tiles"]


def preflight(src):
    with rasterio.open(src) as ds:
        info = {
            "crs": str(ds.crs),
            "bands": ds.count,
            "dtype": ds.dtypes[0],
            "width": ds.width,
            "height": ds.height,
            "res_m": [round(abs(ds.res[0]), 2), round(abs(ds.res[1]), 2)],
            "bounds_lonlat": [round(v, 5) for v in
                              transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)],
        }
    problems = []
    if info["bands"] not in (3, 4):
        problems.append(f"expected 3 (RGB) or 4 (RGBA) bands, found {info['bands']}")
    if info["dtype"] != "uint8":
        problems.append(f"expected uint8, found {info['dtype']}")
    if "32636" not in info["crs"]:
        problems.append(f"expected EPSG:32636, found {info['crs']} (tiling still "
                        "works, but check the GEE export settings)")
    return info, problems


def valid_mask(rgb, black_max=20, trim=3):
    """True where the map has content. The black margin around a warped scan
    is found as dark pixels CONNECTED TO THE IMAGE EDGE, so dark ink inside the
    map is never punched out. `trim` then shaves a few pixels off the edge to
    remove the blended dark fringe."""
    from scipy import ndimage as ndi
    dark = rgb.max(axis=0) <= black_max
    lab, _ = ndi.label(dark)
    edge = np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))
    edge = edge[edge > 0]
    ok = ~np.isin(lab, edge)
    if trim > 0:
        ok = ndi.binary_erosion(ok, iterations=trim, border_value=1)
    return ok


def clean_edges(src, tmp_dir, black_max, trim):
    """Write an RGBA copy of `src` with the outer black margin transparent."""
    from rasterio.enums import ColorInterp
    with rasterio.open(src) as ds:
        rgb = ds.read([1, 2, 3])
        prof = ds.profile
    ok = valid_mask(rgb, black_max, trim)
    print(f"clean-edges: {100 * (1 - ok.mean()):.1f}% of pixels made transparent")
    prof.pop("nodata", None)
    prof.update(count=4, dtype="uint8", compress="deflate", tiled=True,
                photometric="RGB", blockxsize=512, blockysize=512)
    out = str(Path(tmp_dir) / "clean_rgba.tif")
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(rgb * ok.astype("uint8"), indexes=[1, 2, 3])
        dst.write(ok.astype("uint8") * 255, 4)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="RGB GeoTIFF from GEE")
    ap.add_argument("--out", required=True, help="output tile folder")
    ap.add_argument("--zmin", type=int, default=7)
    ap.add_argument("--zmax", type=int, default=12)
    ap.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--dry-run", action="store_true", help="preflight only")
    ap.add_argument("--clean-edges", action="store_true",
                    help="make the black margin around a warped map transparent (needs scipy)")
    ap.add_argument("--keep-alpha", action="store_true",
                    help="source already has its own alpha band (e.g. land-only tiles): do not treat black as no-data")
    ap.add_argument("--resampling", default="average",
                    help="gdal2tiles resampling; use 'near' for terrain-RGB so the encoding is not averaged")
    ap.add_argument("--black-max", type=int, default=20,   # 60 suits the 1775 map: its paper is light
                    help="with --clean-edges: pixels this dark or darker count as margin")
    ap.add_argument("--trim", type=int, default=3,
                    help="with --clean-edges: pixels shaved off the edge to drop the dark fringe")
    a = ap.parse_args()

    info, problems = preflight(a.src)
    print(json.dumps(info, indent=2))
    for p in problems:
        print("WARNING:", p)
    if a.dry_run:
        return
    if info["bands"] not in (3, 4) or info["dtype"] != "uint8":
        sys.exit("Fix the issues above first (the GEE script exports 8-bit RGB).")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory()
    if a.clean_edges:
        src = clean_edges(a.src, tmp.name, a.black_max, a.trim)   # RGBA: alpha carries the edge
        nodata_args = []
    elif a.keep_alpha:
        src = a.src
        nodata_args = []                 # the file's own alpha band is used
    else:
        src = a.src
        nodata_args = ["-a", "0,0,0"]    # black = no data -> transparent
    cmd = find_gdal2tiles() + [
        "--xyz", "-z", f"{a.zmin}-{a.zmax}",
        "--processes", str(a.processes),
        "-r", a.resampling,
        "-w", "none",                 # no demo web page
    ] + nodata_args + [src, str(out)]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    tmp.cleanup()

    meta = {"bounds": info["bounds_lonlat"], "minzoom": a.zmin, "maxzoom": a.zmax,
            "source": os.path.basename(a.src)}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    n = sum(1 for _ in out.rglob("*.png"))
    size = sum(f.stat().st_size for f in out.rglob("*.png")) / 1e6
    print(f"Done: {n} tiles, {size:.1f} MB in {out}")


if __name__ == "__main__":
    main()
