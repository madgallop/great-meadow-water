#!/usr/bin/env python3
"""
04_swot_frames.py
=================
Daily SWOT L2 HR Raster (100 m, UTM 36N) -> the June 2023 drainage frames.

Fills the gap in the Sentinel-2 frames: f00 is 6 June, f01 is July-August.
SWOT observed the reservoir almost every day of 3-23 June 2023, through the
cloud, and also measures water SURFACE ELEVATION (wse), so it shows the drop
in metres as well as the shrinking footprint.

WHAT SWOT CAN AND CANNOT DO (state this in the viewer)
------------------------------------------------------
* One day covers only a swath (two strips either side of the satellite track),
  NOT the whole reservoir. Pixels outside the swath are "no observation",
  never "dry". Each frame reports how much of the footprint it observed.
* Pixels are 100 m. Narrow channels and canals are not resolved.
* Each pass is a snapshot. The viewer must not blend days as if it were a
  continuous record; days are observations.

STEP 1: look at one file, so variable names are verified, not assumed
    python python/04_swot_frames.py --inspect \\
        --in-dir ../data_for_water_model/swot_data/SWOT_L2_100m_ras

STEP 2: build the stack, per-day table and tile-ready GeoTIFFs
    python python/04_swot_frames.py \\
        --in-dir ../data_for_water_model/swot_data/SWOT_L2_100m_ras \\
        --footprint data/gee_outputs/Reservoir_footprint.tif \\
        --out-dir data/swot

STEP 3: tile it (reuses script 03; frame labels come from the labels file)
    python python/03_build_frame_tiles.py \\
        --src data/swot/swot_water_frames.tif \\
        --footprint data/gee_outputs/Reservoir_footprint.tif \\
        --labels data/swot/swot_labels.json \\
        --zmin 7 --zmax 10 --out viewer/tiles/swot

OUTPUT CLASSES (byte; 255 = not observed that day)
    0 no water   2 partly water (water_frac 0.10-0.50)   3 water (>= 0.50)
Class 1 is unused so the viewer palette matches the Sentinel-2 frames.

DUPLICATES: some dates have two files (PGC0 and PIA1 processing versions, or
_01/_02). One is kept per date: PGC0 over PIA1, then the higher version
number. The choice is written to swot_days.csv so you can change it.
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

NAME_RE = re.compile(r"_(\d{8})T(\d{6})_\d{8}T\d{6}_([A-Z0-9]{4})_(\d{2})\.nc$")
CRS = "EPSG:32636"


def read_nc(path):
    """Return dict var -> masked array, and the x/y coordinate vectors."""
    try:
        import netCDF4
        ds = netCDF4.Dataset(path)
        names = list(ds.variables)
        get = lambda v: np.ma.masked_invalid(ds.variables[v][:])
        return ds, names, get
    except ImportError:
        import xarray as xr
        ds = xr.open_dataset(path, mask_and_scale=True)
        names = list(ds.variables)
        get = lambda v: np.ma.masked_invalid(ds[v].values)
        return ds, names, get


def pick_files(in_dir):
    """One file per date: PGC0 over PIA1, then higher version."""
    by_date = {}
    for p in sorted(Path(in_dir).glob("*.nc")):
        m = NAME_RE.search(p.name)
        if not m:
            print(f"  skip (unrecognised name): {p.name}")
            continue
        date, hhmmss, proc, ver = m.groups()
        by_date.setdefault(date, []).append((proc == "PGC0", int(ver), hhmmss, proc, p))
    chosen = []
    for date, items in sorted(by_date.items()):
        items.sort(key=lambda t: (t[0], t[1]))
        best = items[-1]
        chosen.append({"date": date, "time": best[2], "proc": best[3],
                       "path": best[4],
                       "alternatives": [i[4].name for i in items[:-1]]})
    return chosen


def inspect(in_dir):
    files = pick_files(in_dir)
    if not files:
        sys.exit("no SWOT .nc files found")
    p = files[0]["path"]
    ds, names, get = read_nc(p)
    print(f"{p.name}\n")
    for v in names:
        try:
            arr = get(v)
            print(f"  {v:<28} shape={tuple(arr.shape)}  "
                  f"min={arr.min() if arr.size else '-'}  max={arr.max() if arr.size else '-'}")
        except Exception as e:
            print(f"  {v:<28} (could not read: {e})")
    print("\nThe script expects x, y, water_frac and (optionally) wse, wse_qual.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--footprint")
    ap.add_argument("--out-dir")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--water-var", default="water_frac")
    ap.add_argument("--wse-var", default="wse")
    ap.add_argument("--wse-qual-var", default="wse_qual")
    ap.add_argument("--area-qual-var", default="water_area_qual")
    ap.add_argument("--max-area-qual", type=int, default=2,
                    help="keep water_frac where water_area_qual <= this (0 good .. 3 bad)")
    ap.add_argument("--max-wse-qual", type=int, default=1,
                    help="keep wse where quality flag <= this (0 good, 1 suspect)")
    a = ap.parse_args()

    if a.inspect:
        return inspect(a.in_dir)
    if not (a.footprint and a.out_dir):
        sys.exit("--footprint and --out-dir are required (or use --inspect)")

    files = pick_files(a.in_dir)
    print(f"{len(files)} dates")
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- pass 1: common grid covering every file ----
    grids = []
    for f in files:
        ds, names, get = read_nc(f["path"])
        for need in ("x", "y", a.water_var):
            if need not in names:
                sys.exit(f"{f['path'].name} has no variable '{need}'. "
                         f"Found: {names}. Run --inspect and pass --water-var.")
        x = np.asarray(get("x")).astype(float)
        y = np.asarray(get("y")).astype(float)
        grids.append((x, y))
    res = float(np.median([abs(np.diff(g[0])).min() for g in grids]))
    xmin = min(g[0].min() for g in grids) - res / 2
    xmax = max(g[0].max() for g in grids) + res / 2
    ymin = min(g[1].min() for g in grids) - res / 2
    ymax = max(g[1].max() for g in grids) + res / 2
    W, H = int(round((xmax - xmin) / res)), int(round((ymax - ymin) / res))
    transform = from_origin(xmin, ymax, res, res)
    print(f"grid {W}x{H} at {res:g} m, bounds x {xmin:.0f}..{xmax:.0f} y {ymin:.0f}..{ymax:.0f}")

    # footprint on this grid: fraction of each 100 m cell inside the reservoir
    fpa = np.zeros((H, W), dtype="float32")
    with rasterio.open(a.footprint) as fp:
        reproject(rasterio.band(fp, 1), fpa, dst_transform=transform, dst_crs=CRS,
                  resampling=Resampling.average)
    fp_mask = fpa >= 0.5
    cell_km2 = res * res / 1e6
    fp_km2 = fp_mask.sum() * cell_km2
    with rasterio.open(a.footprint) as fp:
        total_km2 = float((fp.read(1) == 1).sum()) * abs(fp.res[0] * fp.res[1]) / 1e6
    print(f"reservoir footprint inside the SWOT scene: {fp_km2:.0f} km2 "
          f"= {100 * fp_km2 / total_km2:.0f}% of the full {total_km2:.0f} km2")
    if fp_km2 < 0.5 * total_km2:
        print("  WARNING: these SWOT scenes cover only part of the reservoir. "
              "Percentages below are of that part only. Neighbouring scenes are "
              "needed for the rest.")

    classes = np.full((len(files), H, W), 255, dtype="uint8")
    wse_stack = np.full((len(files), H, W), np.nan, dtype="float32")
    rows, labels = [], []

    for i, f in enumerate(files):
        ds, names, get = read_nc(f["path"])
        x = np.asarray(get("x")).astype(float)
        y = np.asarray(get("y")).astype(float)
        wf = np.ma.filled(get(a.water_var).astype("float32"), np.nan)
        if wf.ndim == 3:
            wf = wf[0]
        # Raw water_frac holds noise (-0.6 to 35). Drop bad-quality pixels,
        # then clip to the physical range 0..1.
        if a.area_qual_var in names:
            aq = np.ma.filled(get(a.area_qual_var).astype("float32"), 99)
            if aq.ndim == 3:
                aq = aq[0]
            wf = np.where(aq <= a.max_area_qual, wf, np.nan)
        wf = np.clip(wf, 0, 1)
        # place into the common grid by cell index (rows run north to south)
        cols = np.round((x - xmin - res / 2) / res).astype(int)
        rws = np.round((ymax - y - res / 2) / res).astype(int)
        cc, rr = np.meshgrid(cols, rws)
        ok = (cc >= 0) & (cc < W) & (rr >= 0) & (rr < H)

        cls = np.full(wf.shape, 255, dtype="uint8")
        seen = np.isfinite(wf)
        cls[seen] = 0
        cls[seen & (wf >= 0.10)] = 2
        cls[seen & (wf >= 0.50)] = 3
        classes[i][rr[ok], cc[ok]] = cls[ok]

        if a.wse_var in names:
            wse = np.ma.filled(get(a.wse_var).astype("float32"), np.nan)
            if wse.ndim == 3:
                wse = wse[0]
            if a.wse_qual_var in names:
                q = np.ma.filled(get(a.wse_qual_var).astype("float32"), 99)
                if q.ndim == 3:
                    q = q[0]
                wse = np.where(q <= a.max_wse_qual, wse, np.nan)
            wse_stack[i][rr[ok], cc[ok]] = wse[ok]

        obs = classes[i] != 255
        in_fp = fp_mask & obs
        water = in_fp & (classes[i] == 3)
        w = wse_stack[i][fp_mask & np.isfinite(wse_stack[i]) & (classes[i] == 3)]
        d = datetime.strptime(f["date"], "%Y%m%d")
        label = f"{d.day} {d.strftime('%b %Y')} (SWOT pass, {f['time'][:2]}:{f['time'][2:4]} UTC)"
        labels.append({"id": f"f{i:02d}", "label": label})
        rows.append({
            "frame": f"f{i:02d}", "date": d.date().isoformat(), "file": f["path"].name,
            "processing": f["proc"], "dropped_alternatives": ";".join(f["alternatives"]),
            "footprint_observed_pct": round(100 * in_fp.sum() / fp_mask.sum(), 1),
            "water_km2_in_observed_footprint": round(water.sum() * cell_km2, 1),
            "water_pct_of_observed_footprint":
                round(100 * water.sum() / in_fp.sum(), 1) if in_fp.sum() else None,
            "wse_median_m": round(float(np.median(w)), 2) if w.size else None,
            "wse_p10_m": round(float(np.percentile(w, 10)), 2) if w.size else None,
            "wse_p90_m": round(float(np.percentile(w, 90)), 2) if w.size else None,
            "wse_pixels": int(w.size),
        })
        r = rows[-1]
        print(f"  {r['frame']} {r['date']} {r['processing']}  observed {r['footprint_observed_pct']:>5}%"
              f"  water {r['water_pct_of_observed_footprint']}% of observed  wse median {r['wse_median_m']}")

    prof = dict(driver="GTiff", height=H, width=W, crs=CRS, transform=transform,
                compress="deflate")
    with rasterio.open(out / "swot_water_frames.tif", "w", count=len(files),
                       dtype="uint8", nodata=255, **prof) as dst:
        for i in range(len(files)):
            dst.write(classes[i], i + 1)
            dst.set_band_description(i + 1, f"f{i:02d}")
    with rasterio.open(out / "swot_wse_frames.tif", "w", count=len(files),
                       dtype="float32", nodata=np.nan, **prof) as dst:
        for i in range(len(files)):
            dst.write(wse_stack[i], i + 1)
            dst.set_band_description(i + 1, f"f{i:02d}")

    with open(out / "swot_days.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    (out / "swot_labels.json").write_text(json.dumps(labels, indent=2))
    print(f"\nwrote swot_water_frames.tif, swot_wse_frames.tif, swot_days.csv, swot_labels.json in {out}")
    print("Check swot_days.csv: frames with low footprint_observed_pct are partial snapshots.")


if __name__ == "__main__":
    main()
