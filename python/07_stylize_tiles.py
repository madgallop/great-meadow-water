#!/usr/bin/env python3
"""
07_stylize_tiles.py
===================
Turn a true-colour period composite + its water-class raster into STYLISED
map tiles (bright land, picked-out agriculture, luminous water with a
highlighter rim). The look lives in python/water_style.py.

INPUTS  (all from GEE; same grid, so they line up pixel for pixel)
  --rgb    period2_reservoir_truecolour.tif     (script 02)
  --water  period2_water.tif                    (script 06, water classes)

STEP 1: PREVIEW first (seconds, nothing is tiled). Writes one PNG per palette.
    python python/07_stylize_tiles.py --preview \\
        --rgb data/gee_outputs/period2_reservoir_truecolour.tif \\
        --water data/gee_outputs/period2_water.tif \\
        --palettes paper,citrus,night --out viewer/tiles/styled/previews
  The run prints the image's "greenness" (ExG) percentiles. If farmland is not
  picked out, or bare land turns green, move --veg-lo / --veg-hi.

STEP 2: build tiles for the palette(s) you like.
    python python/07_stylize_tiles.py \\
        --rgb ...period2... --water ...period2_water.tif \\
        --palettes paper --out viewer/tiles/styled --period period2
  writes viewer/tiles/styled/paper/period2/{z}/{x}/{y}.png + meta.json
  (run again with the Period 3 files and --period period3)

The shapes come from the data; colour, glow and streaks are styling only.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent))
import water_style as ws  # noqa: E402

BLOCK, HALO = 1536, 96      # smaller blocks keep memory modest on a laptop


def find_gdal2tiles():
    script = Path(sys.prefix) / "bin" / "gdal2tiles.py"
    if script.exists():
        return [sys.executable, str(script)]
    return [sys.executable, "-m", "osgeo_utils.gdal2tiles"]


def stats(rgb_ds):
    """Luminance and greenness percentiles from a decimated copy."""
    f = 8
    a = rgb_ds.read([1, 2, 3], out_shape=(3, max(1, rgb_ds.height // f), max(1, rgb_ds.width // f)),
                    resampling=Resampling.average).astype("float32") / 255
    valid = (a.sum(axis=0) > 0)
    L = (0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2])[valid]
    exg = (2 * a[1] - a[0] - a[2])[valid]
    lo, hi = np.percentile(L, [2, 98])
    return float(lo), float(hi), np.percentile(exg, [5, 25, 50, 75, 95])


def save_png(rgba, bg_hex, path):
    """Flatten onto the palette background and save a PNG preview."""
    bg = ws.hex_rgb(bg_hex)
    a = rgba[3][..., None] / 255.0
    img = np.moveaxis(rgba[:3], 0, -1) / 255.0 * a + bg * (1 - a)
    img = (img * 255).astype("uint8")
    try:
        from PIL import Image
        Image.fromarray(img).save(path)
    except ImportError:
        with rasterio.open(path.replace(".png", ".tif"), "w", driver="GTiff", height=img.shape[0],
                           width=img.shape[1], count=3, dtype="uint8") as d:
            d.write(np.moveaxis(img, -1, 0))
        print(f"  (no Pillow: wrote {path.replace('.png', '.tif')} instead)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rgb", required=True)
    ap.add_argument("--water", required=True)
    ap.add_argument("--palettes", default="paper")
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", default="period2", help="folder name under <out>/<palette>/")
    ap.add_argument("--preview", action="store_true", help="fast low-res PNG per palette, no tiles")
    ap.add_argument("--preview-factor", type=int, default=6)
    ap.add_argument("--veg-lo", type=float, default=0.03)
    ap.add_argument("--veg-hi", type=float, default=0.12)
    ap.add_argument("--zmin", type=int, default=7)
    ap.add_argument("--zmax", type=int, default=11)
    ap.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    names = [n.strip() for n in a.palettes.split(",")]
    for n in names:
        if n not in ws.PALETTES:
            sys.exit(f"unknown palette '{n}'. Choose from {', '.join(ws.PALETTES)}")

    with rasterio.open(a.rgb) as rgb_ds, rasterio.open(a.water) as wat_ds:
        if (rgb_ds.width, rgb_ds.height) != (wat_ds.width, wat_ds.height) or rgb_ds.crs != wat_ds.crs \
                or rgb_ds.transform != wat_ds.transform:
            sys.exit("--rgb and --water are not on the same grid. Export both from GEE with "
                     "the same region, scale (20 m) and CRS (scripts 02 and 06 do).")
        if rgb_ds.count < 3:
            sys.exit("--rgb needs 3 bands (R,G,B)")
        H, W, px_m = rgb_ds.height, rgb_ds.width, abs(rgb_ds.res[0])
        lo, hi, exg_p = stats(rgb_ds)
        print(f"{W}x{H} px at {px_m:g} m. Luminance stretch {lo:.3f}..{hi:.3f}")
        print("Greenness (ExG) percentiles 5/25/50/75/95: "
              + "  ".join(f"{v:.3f}" for v in exg_p))
        print(f"Agriculture picked out where ExG rises from {a.veg_lo} to {a.veg_hi}")

        out = Path(a.out)
        if a.preview:
            f = a.preview_factor
            h, w = H // f, W // f
            rgb = rgb_ds.read([1, 2, 3], out_shape=(3, h, w), resampling=Resampling.average)
            cls = wat_ds.read(1, out_shape=(h, w), resampling=Resampling.nearest)
            out.mkdir(parents=True, exist_ok=True)
            noise = ws.Noise(h, w, sx=150.0 / f, sy=45.0 / f).sample(0, 0, h, w)
            for n in names:
                rgba = ws.grade(rgb, cls, ws.PALETTES[n], noise, px_m * f, lo, hi, a.veg_lo, a.veg_hi)
                p = str(out / f"preview_{n}.png")
                save_png(rgba, ws.PALETTES[n]["background"], p)
                print("wrote", p)
            return

        noise_gen = ws.Noise(H, W)
        bounds = [round(v, 5) for v in transform_bounds(rgb_ds.crs, "EPSG:4326", *rgb_ds.bounds)]
        for n in names:
            pal = ws.PALETTES[n]
            dest = out / n / a.period
            dest.mkdir(parents=True, exist_ok=True)
            tif = dest / "_styled.tif"
            prof = dict(driver="GTiff", height=H, width=W, count=4, dtype="uint8", crs=rgb_ds.crs,
                        transform=rgb_ds.transform, tiled=True, blockxsize=512, blockysize=512,
                        compress="deflate", BIGTIFF="IF_SAFER")
            print(f"[{n}] grading {a.period} ...")
            with rasterio.open(tif, "w", **prof) as dst:
                dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
                for r0 in range(0, H, BLOCK):
                    for c0 in range(0, W, BLOCK):
                        h, w = min(BLOCK, H - r0), min(BLOCK, W - c0)
                        win = Window(c0 - HALO, r0 - HALO, w + 2 * HALO, h + 2 * HALO)
                        rgb = rgb_ds.read([1, 2, 3], window=win, boundless=True, fill_value=0)
                        cls = wat_ds.read(1, window=win, boundless=True, fill_value=255)
                        nz = noise_gen.sample(r0 - HALO, c0 - HALO, h + 2 * HALO, w + 2 * HALO)
                        rgba = ws.grade(rgb, cls, pal, nz, px_m, lo, hi, a.veg_lo, a.veg_hi)
                        dst.write(rgba[:, HALO:HALO + h, HALO:HALO + w], window=Window(c0, r0, w, h))
                print(f"[{n}] block rows done to {min(r0 + BLOCK, H)}/{H}", end="\r")
            print()
            cmd = find_gdal2tiles() + ["--xyz", "-z", f"{a.zmin}-{a.zmax}", "--processes", str(a.processes),
                                       "-r", "average", "-w", "none", str(tif), str(dest)]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            (dest / "meta.json").write_text(json.dumps(
                {"bounds": bounds, "minzoom": a.zmin, "maxzoom": a.zmax, "palette": n}, indent=2))
            tif.unlink()
            tiles = list(dest.rglob("*.png"))
            print(f"[{n}] {len(tiles)} tiles, {sum(t.stat().st_size for t in tiles) / 1e6:.0f} MB in {dest}")


if __name__ == "__main__":
    main()
