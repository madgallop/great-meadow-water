#!/usr/bin/env python3
"""
03_build_frame_tiles.py
=======================
Turn drainage_water_frames.tif (16 bands from gee/03_drainage_water_frames.js)
into one transparent tile set per frame, plus a per-frame water table.

INPUT BANDS
-----------
  f00            pre-breach reference (May - 6 June 2023)
  f01 .. f15     two-month observations, Jul-Aug 2023 ... Nov-Dec 2025
Pixel values: 0 not water, 1 weak wet signal, 2 secondary/shallow water,
3 open water, 255 = no clear observation (cloud/shadow/no data).

WHAT IT WRITES
--------------
  <out>/fNN/{z}/{x}/{y}.png   RGBA tiles (class 0 and 255 are transparent)
  <out>/meta.json             bounds, zoom range, frame ids and labels
  <out>/frame_stats.csv       per-frame water inside the pre-breach reservoir
                              footprint, and how much of it was observed

Every frame is an OBSERVATION. The viewer's crossfade between frames is a
visual transition only; nothing is interpolated here.

USAGE (from EarthEngines/viz, greatmeadow env active)
-----
    python python/03_build_frame_tiles.py \\
        --src data/gee_outputs/drainage_water_frames.tif \\
        --footprint data/gee_outputs/Reservoir_footprint.tif \\
        --out viewer/tiles/frames

    add --stats-only to just write frame_stats.csv (fast, no tiling)
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject, transform_bounds

# RGBA per class, in the c.1775-map water ramp: weak #94ceba, shallow #59acaa,
# open #3e7a84. (python/09_recolour_frame_tiles.py recolours tiles already built
# with the earlier yellow/cyan/blue palette.)
PALETTE = {1: (148, 206, 186, 150), 2: (89, 172, 170, 215), 3: (62, 122, 132, 240)}
NODATA = 255
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


LABELS = None   # set from --labels (e.g. SWOT daily frames)


def frame_label(i):
    """f00 = pre-breach; f01.. are two-month windows starting Jul 2023.
    With --labels, labels come from that JSON list instead."""
    if LABELS:
        return LABELS[i]["label"]
    if i == 0:
        return "Before the breach (May to 6 Jun 2023)"
    m0 = 6 + (i - 1) * 2                 # months since Jan 2023, July = 6
    m1 = m0 + 1
    y0, y1 = 2023 + m0 // 12, 2023 + m1 // 12
    a, b = MONTHS[m0 % 12], MONTHS[m1 % 12]
    return f"{a} to {b} {y1}" if y0 == y1 else f"{a} {y0} to {b} {y1}"


def find_gdal2tiles():
    """Use this environment's gdal2tiles, never whatever is first on PATH."""
    script = Path(sys.prefix) / "bin" / "gdal2tiles.py"
    if script.exists():
        return [sys.executable, str(script)]
    return [sys.executable, "-m", "osgeo_utils.gdal2tiles"]


def colourise(band):
    rgba = np.zeros((4,) + band.shape, dtype=np.uint8)
    for cls, (r, g, b, a) in PALETTE.items():
        m = band == cls
        rgba[0][m], rgba[1][m], rgba[2][m], rgba[3][m] = r, g, b, a
    return rgba


def footprint_on_grid(footprint_path, ds):
    """Resample the reservoir footprint onto the frames' grid (nearest)."""
    out = np.zeros((ds.height, ds.width), dtype=np.uint8)
    with rasterio.open(footprint_path) as fp:
        reproject(source=rasterio.band(fp, 1), destination=out,
                  dst_transform=ds.transform, dst_crs=ds.crs,
                  resampling=Resampling.nearest)
    return out == 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True)
    ap.add_argument("--footprint", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--zmin", type=int, default=7)
    ap.add_argument("--zmax", type=int, default=12)
    ap.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--stats-only", action="store_true")
    ap.add_argument("--labels", help="JSON list [{id,label}] overriding the Sentinel-2 labels")
    a = ap.parse_args()
    global LABELS
    if a.labels:
        if a.labels.lower().endswith(".csv"):      # GEE table with a 'label' column
            with open(a.labels, newline="", encoding="utf-8") as fh:
                rows_ = sorted(csv.DictReader(fh), key=lambda r: r.get("frame", ""))
            LABELS = [{"id": r.get("frame"), "label": r["label"]} for r in rows_]
        else:
            LABELS = json.loads(Path(a.labels).read_text())

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    with rasterio.open(a.src) as ds:
        n = ds.count
        print(f"{n} bands, {ds.width}x{ds.height}, CRS {ds.crs}, "
              f"pixel {abs(ds.res[0]):g} m")
        if ds.dtypes[0] != "uint8":
            sys.exit(f"expected uint8 bands, found {ds.dtypes[0]}")
        px_km2 = abs(ds.res[0] * ds.res[1]) / 1e6
        fp = footprint_on_grid(a.footprint, ds)
        fp_km2 = fp.sum() * px_km2
        print(f"Reservoir footprint on this grid: {fp_km2:.0f} km2")
        bounds = [round(v, 5) for v in transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)]

        rows, frames = [], []
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(n):
                fid = f"f{i:02d}"
                band = ds.read(i + 1)
                valid = (band != NODATA) & fp
                observed = valid.sum() * px_km2
                open_w = ((band == 3) & fp).sum() * px_km2
                any_w = (((band >= 2) & (band <= 3)) & fp).sum() * px_km2
                obs_pix = valid.sum()
                rows.append({
                    "frame": fid, "label": frame_label(i),
                    "observed_km2": round(observed, 1),
                    "observed_pct_of_footprint": round(100 * observed / fp_km2, 1),
                    "open_water_km2": round(open_w, 1),
                    "open_plus_shallow_km2": round(any_w, 1),
                    # share of OBSERVED footprint pixels holding open water:
                    # robust to cloud gaps, unlike a raw area
                    "open_water_pct_of_observed":
                        round(100 * ((band == 3) & fp).sum() / obs_pix, 1) if obs_pix else None,
                })
                frames.append({"id": fid, "label": frame_label(i)})
                print(f"  {fid}  {frame_label(i):<30} observed {rows[-1]['observed_pct_of_footprint']:>5}%"
                      f"  open water {rows[-1]['open_water_pct_of_observed']}% of observed")

                if a.stats_only:
                    continue
                tmp_tif = os.path.join(tmp, f"{fid}.tif")
                with rasterio.open(tmp_tif, "w", driver="GTiff", height=ds.height,
                                   width=ds.width, count=4, dtype="uint8",
                                   crs=ds.crs, transform=ds.transform,
                                   compress="deflate") as dst:
                    dst.write(colourise(band))
                cmd = find_gdal2tiles() + [
                    "--xyz", "-z", f"{a.zmin}-{a.zmax}",
                    "--processes", str(a.processes),
                    "-r", "near",            # keep class edges crisp
                    "-w", "none", tmp_tif, str(out / fid)]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

    with open(out / "frame_stats.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out / 'frame_stats.csv'}")

    if a.stats_only:
        return
    (out / "meta.json").write_text(json.dumps(
        {"bounds": bounds, "minzoom": a.zmin, "maxzoom": a.zmax, "frames": frames},
        indent=2, ensure_ascii=False))
    tiles = list(out.rglob("*.png"))
    print(f"Done: {len(tiles)} tiles, {sum(t.stat().st_size for t in tiles) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
