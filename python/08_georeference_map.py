"""
08_georeference_map.py
======================
Georeference the c.1775 map (Period 1 basemap) from ground control points you
pick, and warp it onto the project grid (EPSG:32636, same extent as the DEM
and the satellite composites) so it can be tiled and draped on the terrain.

WHY IT IS NOT AUTOMATIC
-----------------------
A hand-drawn 1775 map has no coordinates and is not survey-accurate. The
shapes (the river, the meadow, the bays) are matched to a modern basemap by
YOU choosing the same landmarks on both. This script then fits the warp,
tells you honestly how well it fits, and applies it.

STEP 1  see the map with a pixel grid, to read pixel positions
    python python/08_georeference_map.py --image <map.png> --grid grid.png
    python python/08_georeference_map.py --image <map.png> --grid zoom.png --crop 6000 0 10742 2500
  Pixel (0,0) is the top-left corner; y increases DOWNWARD. (You can also
  read pixel positions in QGIS or any image viewer.)

STEP 2  make a CSV of control points, one row per landmark:
    name,px,py,lon,lat
    konka_mouth,9421,1980,35.2011,47.4533
  Good landmarks are things that have not moved in 250 years: river
  confluences, sharp bends and island tips, the Konka/Dnipro junction, the
  shape of long bays at the old river mouths. AVOID shorelines and anything
  that the reservoir or ploughing could have changed. Read lon/lat from a
  modern basemap of the pre-reservoir river where you can (e.g. the JRC
  water occurrence layer or old Landsat). Use 8-15 points spread across the
  WHOLE map, including the corners.

STEP 3  check the fit, then warp
    python python/08_georeference_map.py --image <map.png> --gcps gcps.csv --report
    python python/08_georeference_map.py --image <map.png> --gcps gcps.csv \
        --out data/gee_outputs/period1_map_1775.tif

The report gives the error in metres for a 1st- and 2nd-order polynomial fit
(and a leave-one-out error, which is the honest one). The warp uses
--order 2 by default (smooth), or --order tps (thin plate spline: matches
every point exactly but bends the map in between; needs 10+ good points).

HONESTY
-------
The result is a RECONSTRUCTION: label it "c.1775 map, georeferenced; positions
approximate" in the viewer. Residual errors of several hundred metres to a
few km are normal for maps of this age.

Needs only numpy and Pillow, plus GDAL command line tools (gdal_translate,
gdalwarp) from the conda env, for the warp.
"""
import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

# Same extent as the GEE scripts: lon 33.1-35.4, lat 46.25-48.0
AOI = (33.1, 46.25, 35.4, 48.0)


# ---------------------------------------------------------------- projection
def ll_to_utm36(lon, lat):
    """WGS84 lon/lat (deg) -> UTM zone 36N (EPSG:32636) metres. Krueger series."""
    a, f = 6378137.0, 1 / 298.257223563
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    al = [n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16,
          13 * n ** 2 / 48 - 3 * n ** 3 / 5,
          61 * n ** 3 / 240]
    k0, lon0 = 0.9996, math.radians(33.0)
    phi, dl = math.radians(lat), math.radians(lon) - lon0
    c = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi = math.atan2(t, math.cos(dl))
    eta = math.atanh(math.sin(dl) / math.sqrt(1 + t * t))
    E = 500000 + k0 * A * (eta + sum(al[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta)
                                     for j in range(3)))
    N = k0 * A * (xi + sum(al[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta)
                           for j in range(3)))
    return E, N


# ------------------------------------------------------------------- GCP I/O
def read_gcps(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            try:
                rows.append({"name": (r.get("name") or f"pt{i - 1}").strip(),
                             "px": float(r["px"]), "py": float(r["py"]),
                             "lon": float(r["lon"]), "lat": float(r["lat"])})
            except (KeyError, TypeError, ValueError):
                sys.exit(f"{path}: row {i} needs numeric px, py, lon, lat columns.")
    if len(rows) < 4:
        sys.exit(f"Need at least 4 control points, found {len(rows)} (8-15 is better).")
    for r in rows:
        r["E"], r["N"] = ll_to_utm36(r["lon"], r["lat"])
    return rows


# --------------------------------------------------------------------- fits
def design(px, py, order):
    x, y = np.asarray(px, float), np.asarray(py, float)
    cols = [np.ones_like(x), x, y]
    if order >= 2:
        cols += [x * x, x * y, y * y]
    return np.stack(cols, axis=1)


def fit_poly(pts, order):
    """Least-squares polynomial pixel -> UTM. Pixel coords are centred and
    scaled first so the fit is well conditioned."""
    px = np.array([p["px"] for p in pts]); py = np.array([p["py"] for p in pts])
    E = np.array([p["E"] for p in pts]); N = np.array([p["N"] for p in pts])
    cx, cy, s = px.mean(), py.mean(), max(np.ptp(px), np.ptp(py), 1.0)
    X = design((px - cx) / s, (py - cy) / s, order)
    cE, *_ = np.linalg.lstsq(X, E, rcond=None)
    cN, *_ = np.linalg.lstsq(X, N, rcond=None)

    def predict(qx, qy):
        Q = design((np.asarray(qx, float) - cx) / s, (np.asarray(qy, float) - cy) / s, order)
        return Q @ cE, Q @ cN
    return predict


def fit_report(pts, order):
    n_par = 3 if order == 1 else 6
    if len(pts) < n_par + 1:
        return None
    pred = fit_poly(pts, order)
    pe, pn = pred([p["px"] for p in pts], [p["py"] for p in pts])
    res = np.hypot(pe - [p["E"] for p in pts], pn - [p["N"] for p in pts])
    loo = []
    for i in range(len(pts)):
        rest = pts[:i] + pts[i + 1:]
        if len(rest) < n_par:
            return {"order": order, "rmse_m": float(np.sqrt((res ** 2).mean())),
                    "max_m": float(res.max()), "loo_rmse_m": None, "residuals_m": res.tolist()}
        q = fit_poly(rest, order)
        e, nn = q([pts[i]["px"]], [pts[i]["py"]])
        loo.append(math.hypot(e[0] - pts[i]["E"], nn[0] - pts[i]["N"]))
    return {"order": order, "rmse_m": float(np.sqrt((res ** 2).mean())),
            "max_m": float(res.max()),
            "loo_rmse_m": float(np.sqrt((np.array(loo) ** 2).mean())),
            "residuals_m": res.tolist()}


def print_report(pts):
    print(f"{len(pts)} control points\n")
    best = None
    for order in (1, 2):
        r = fit_report(pts, order)
        if r is None:
            print(f"order {order}: not enough points")
            continue
        loo = "n/a" if r["loo_rmse_m"] is None else f"{r['loo_rmse_m']:.0f} m"
        print(f"order {order}: RMSE {r['rmse_m']:.0f} m | worst {r['max_m']:.0f} m | leave-one-out {loo}")
        if best is None or (r["loo_rmse_m"] or 1e12) < (best["loo_rmse_m"] or 1e12):
            best = r
    if best:
        print(f"\nResiduals for order {best['order']} (look for the worst points and recheck them):")
        for p, d in sorted(zip(pts, best["residuals_m"]), key=lambda t: -t[1]):
            print(f"  {p['name']:<24} {d:7.0f} m")
        loo = best["loo_rmse_m"] if best["loo_rmse_m"] is not None else best["rmse_m"]
        print("\nRule of thumb for a 1775 hand-drawn map: under ~1 km is good, "
              "1-3 km is usable as a labelled reconstruction, over 3 km: recheck points.")
        if loo > 3000:
            print("WARNING: large error. A mis-matched landmark or a swapped lon/lat is the usual cause.")
    return best


# ---------------------------------------------------------------- grid helper
def write_grid(image_path, out_png, step, crop, max_w=2400):
    from PIL import Image, ImageDraw
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    x0, y0, x1, y1 = crop if crop else (0, 0, W, H)
    im = im.crop((x0, y0, x1, y1))
    sc = min(1.0, max_w / im.width)
    im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    gx = (x0 // step + 1) * step
    while gx < x1:
        X = (gx - x0) * sc
        d.line([(X, 0), (X, im.height)], fill=(220, 40, 40), width=1)
        d.text((X + 3, 3), str(gx), fill=(200, 0, 0))
        gx += step
    gy = (y0 // step + 1) * step
    while gy < y1:
        Y = (gy - y0) * sc
        d.line([(0, Y), (im.width, Y)], fill=(40, 40, 220), width=1)
        d.text((3, Y + 3), str(gy), fill=(0, 0, 200))
        gy += step
    im.save(out_png)
    print(f"wrote {out_png}  (source image {W}x{H}px; grid every {step}px; red = x, blue = y)")


# ---------------------------------------------------------------------- warp
def warp(image, gcps, out, order, res, bounds):
    for tool in ("gdal_translate", "gdalwarp"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. Activate the conda env that has GDAL (greatmeadow).")
    lon0, lat0, lon1, lat1 = bounds
    corners = [ll_to_utm36(lo, la) for lo in (lon0, lon1) for la in (lat0, lat1)]
    xmin, xmax = min(c[0] for c in corners), max(c[0] for c in corners)
    ymin, ymax = min(c[1] for c in corners), max(c[1] for c in corners)

    with tempfile.TemporaryDirectory() as tmp:
        vrt = str(Path(tmp) / "src.vrt")
        cmd = ["gdal_translate", "-of", "VRT", "-a_srs", "EPSG:4326"]
        for p in gcps:
            cmd += ["-gcp", str(p["px"]), str(p["py"]), str(p["lon"]), str(p["lat"])]
        cmd += [str(image), vrt]
        subprocess.run(cmd, check=True)

        Path(out).parent.mkdir(parents=True, exist_ok=True)
        wcmd = ["gdalwarp", "-overwrite", "-t_srs", "EPSG:32636",
                "-te", f"{xmin:.1f}", f"{ymin:.1f}", f"{xmax:.1f}", f"{ymax:.1f}",
                "-tr", str(res), str(res), "-r", "bilinear", "-dstalpha",
                "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES"]
        wcmd += ["-tps"] if order == "tps" else ["-order", str(order)]
        wcmd += [vrt, str(out)]
        subprocess.run(wcmd, check=True)
    print(f"wrote {out}  (RGBA, EPSG:32636, {res} m; transparent outside the map)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="the scanned map (PNG/JPG/TIF)")
    ap.add_argument("--grid", help="write a pixel-grid helper PNG here and exit")
    ap.add_argument("--grid-step", type=int, default=500)
    ap.add_argument("--crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="with --grid: zoom into this pixel box")
    ap.add_argument("--gcps", help="CSV with columns name,px,py,lon,lat")
    ap.add_argument("--report", action="store_true", help="print the fit report and exit")
    ap.add_argument("--out", help="output GeoTIFF (EPSG:32636, RGBA)")
    ap.add_argument("--order", default="2", choices=["1", "2", "3", "tps"])
    ap.add_argument("--res", type=float, default=30.0, help="output pixel size, metres")
    ap.add_argument("--bounds", type=float, nargs=4, metavar=("LON0", "LAT0", "LON1", "LAT1"),
                    default=AOI)
    a = ap.parse_args()

    if a.grid:
        write_grid(a.image, a.grid, a.grid_step, a.crop)
        return
    if not a.gcps:
        sys.exit("Give --grid to make a helper image, or --gcps to fit and warp.")
    gcps = read_gcps(a.gcps)
    print_report(gcps)
    if a.report or not a.out:
        return
    order = a.order if a.order == "tps" else int(a.order)
    if order == "tps" and len(gcps) < 10:
        print("\nNote: thin plate spline with fewer than 10 points can fold the map; use --order 2.")
    warp(a.image, gcps, a.out, order, a.res, a.bounds)
    json.dump({"gcps": gcps, "order": a.order, "res_m": a.res},
              open(Path(a.out).with_suffix(".gcps.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
