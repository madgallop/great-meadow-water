#!/usr/bin/env python3
"""
05_prepare_manual_network.py
============================
Prepare the hand-traced canals and intake stations for the viewer and GEE.
Replaces the OSM cleaner (01) for this project: no tiers, no OSM attributes.

INPUT  (your shapefiles, UTM 36N)
  canals_manual.shp              6 canals, attribute id
  pumping_stations_manual.shp    68 stations, attribute id

WHAT IT DOES
  1. Splits each canal into short segments (default 1 km), each oriented so
     it runs AWAY from the reservoir.
  2. Gives every segment a DISPLAY ORDER ext_order in 0..1: distance from the
     reservoir along the canal, scaled so the farthest end is 1. This is a
     display sequence, NOT a construction chronology or a flow time.
  3. Matches each station to its nearest canal segment and gives it that
     segment's order, so it pops up when the canal reaches it. A station
     more than 2 km from any canal is placed by its distance to the reservoir.
  4. Gives stations a unique uid. (The input repeats id 44 and skips 45; the
     original is kept as id_orig.)

OUTPUT
  <viewer-dir>/canals_manual.geojson    segments, WGS84, for the web viewer
  <viewer-dir>/pumps_manual.geojson     stations, WGS84, for the web viewer
  <gee-dir>/canal_segments_manual.shp   same segments, for GEE upload
  <gee-dir>/pumps_manual.shp            same stations, for GEE upload
  <gee-dir>/network_report.json         counts, lengths, warnings

USAGE (from EarthEngines/viz, greatmeadow env)
  python python/05_prepare_manual_network.py \\
    --canals data/clean/for_gee_upload/canals_manual.shp \\
    --stations data/clean/for_gee_upload/pumping_stations_manual.shp \\
    --footprint data/gee_outputs/Reservoir_footprint.tif \\
    --viewer-dir viewer/data --gee-dir data/clean/for_gee_upload
"""
import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from rasterio import features
from shapely.geometry import Point, shape
from shapely.ops import linemerge, substring, unary_union

UTM = "EPSG:32636"
OFF_NETWORK_M = 2000      # stations farther than this are not placed on a canal


def reservoir_polygon(tif):
    with rasterio.open(tif) as ds:
        a = ds.read(1).astype("uint8")
        geoms = [shape(g) for g, _ in features.shapes(a, mask=(a == 1), transform=ds.transform)]
        crs = ds.crs
    poly = unary_union(geoms).simplify(60)
    return gpd.GeoSeries([poly], crs=crs).to_crs(UTM).iloc[0]


def line_parts(geom):
    geom = shapely.force_2d(geom)
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type == "MultiLineString":
        m = linemerge(geom)
        return list(m.geoms) if m.geom_type == "MultiLineString" else [m]
    return []


def fix_crs(g, name, override, warnings):
    """The traced layers carry a UTM 36N .prj but their numbers are Web Mercator
    metres (x ~ 3.7e6; real UTM eastings never exceed ~0.85e6). Detect that and
    re-label, or apply --src-crs. Without this every feature lands ~3,300 km
    east of the reservoir."""
    if override:
        g = g.set_crs(override, allow_override=True)
        warnings.append(f"{name}: CRS forced to {override} by --src-crs")
        return g
    minx, miny, maxx, maxy = g.total_bounds
    if g.crs is not None and g.crs.is_projected and abs(maxx) > 2_000_000 and "UTM" in g.crs.name.upper():
        warnings.append(f"{name}: .prj says '{g.crs.name}' but coordinates (x up to {maxx:,.0f}) are "
                        "Web Mercator metres; re-labelled as EPSG:3857")
        print(f"NOTE: {warnings[-1]}")
        g = g.set_crs("EPSG:3857", allow_override=True)
    return g


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canals", required=True)
    ap.add_argument("--stations", required=True)
    ap.add_argument("--footprint", required=True)
    ap.add_argument("--viewer-dir", required=True)
    ap.add_argument("--gee-dir", required=True)
    ap.add_argument("--seg-m", type=float, default=1000)
    ap.add_argument("--src-crs", help="force the CRS of both input layers (e.g. EPSG:3857)")
    a = ap.parse_args()

    res = reservoir_polygon(a.footprint)
    canals = gpd.read_file(a.canals)
    stations = gpd.read_file(a.stations)
    for name, g in (("canals", canals), ("stations", stations)):
        if g.crs is None:
            raise SystemExit(f"{name} has no CRS (.prj missing)")
    warnings = []
    canals = fix_crs(canals, "canals", a.src_crs, warnings)
    stations = fix_crs(stations, "stations", a.src_crs, warnings)
    # Rows with no geometry (cleared or merged features) cannot be drawn.
    for name, g in ("canals", canals), ("stations", stations):
        bad = g.geometry.isna() | g.geometry.is_empty
        if bad.any():
            ids = g.loc[bad, "id"].tolist() if "id" in g.columns else g.index[bad].tolist()
            warnings.append(f"{name}: {int(bad.sum())} row(s) have no geometry and were skipped (id {ids})")
            print(f"WARNING: {warnings[-1]}")
    canals = canals[~(canals.geometry.isna() | canals.geometry.is_empty)].to_crs(UTM)
    stations = stations[~(stations.geometry.isna() | stations.geometry.is_empty)].to_crs(UTM)
    print(f"{len(canals)} canal rows, {len(stations)} stations with geometry")

    # ---- 1-2. oriented parts and segments --------------------------------
    rows = []
    part_end = []                     # d0 + length of each part, for scaling
    for _, c in canals.iterrows():
        for pi, part in enumerate(line_parts(c.geometry)):
            if part.length < 50:
                continue
            c0, c1 = Point(part.coords[0]), Point(part.coords[-1])
            if res.distance(c0) > res.distance(c1):          # run away from reservoir
                part = shapely.reverse(part)
            d0 = res.distance(Point(part.coords[0]))
            if d0 > 3_000:
                warnings.append(f"canal {c['id']} part {pi} starts {d0/1000:.1f} km from the reservoir")
            L = part.length
            n = max(1, int(round(L / a.seg_m)))
            edges = np.linspace(0, L, n + 1)
            for k in range(n):
                rows.append({
                    "canal_id": int(c["id"]), "part": pi, "seg": k,
                    "len_m": float(edges[k + 1] - edges[k]),
                    "raw": d0 + (edges[k] + edges[k + 1]) / 2,
                    "geometry": substring(part, edges[k], edges[k + 1])})
            part_end.append(d0 + L)
    segs = gpd.GeoDataFrame(rows, crs=UTM)
    D = max(part_end)
    segs["ext_order"] = (segs["raw"] / D).clip(0, 1).round(4)
    segs["fill_order"] = segs["ext_order"]       # water follows the same sequence
    segs["len_m"] = segs["len_m"].round(0)
    segs = segs.drop(columns="raw")
    segs.insert(0, "seg_id", range(1, len(segs) + 1))

    # ---- 3-4. stations -----------------------------------------------------
    st = stations.copy().reset_index(drop=True)
    st["id_orig"] = st["id"]
    st["uid"] = range(1, len(st) + 1)
    if st["id"].duplicated().any():
        warnings.append("duplicate station ids in the input: "
                        f"{sorted(st.loc[st['id'].duplicated(keep=False), 'id'].unique().tolist())}"
                        " (uid is unique; id_orig keeps your number)")
    near = gpd.sjoin_nearest(st, segs[["seg_id", "canal_id", "ext_order", "geometry"]],
                             how="left", distance_col="dist_canal")
    near = near[~near.index.duplicated(keep="first")]
    st["canal_id"] = near["canal_id"].astype("Int64")
    st["seg_id"] = near["seg_id"].astype("Int64")
    st["dist_canal"] = near["dist_canal"].round(0)
    d_res = st.geometry.apply(res.distance)
    st["dist_res"] = d_res.round(0)
    on_net = st["dist_canal"] <= OFF_NETWORK_M
    st["ext_order"] = np.where(on_net, near["ext_order"], (d_res / D).clip(0, 1)).round(4)
    st["on_canal"] = on_net.astype(int)
    st["fill_order"] = st["ext_order"]
    if (~on_net).any():
        warnings.append(f"{int((~on_net).sum())} stations are >{OFF_NETWORK_M} m from any canal "
                        "(placed by distance to the reservoir): uid "
                        f"{st.loc[~on_net, 'uid'].tolist()}")
    st = st[["uid", "id_orig", "canal_id", "seg_id", "dist_canal", "dist_res",
             "on_canal", "ext_order", "fill_order", "geometry"]]
    st = st.rename(columns={"dist_canal": "dist_cnl", "dist_res": "dist_res_m"})

    # ---- write -------------------------------------------------------------
    vd, gd = Path(a.viewer_dir), Path(a.gee_dir)
    vd.mkdir(parents=True, exist_ok=True)
    gd.mkdir(parents=True, exist_ok=True)
    segs4326, st4326 = segs.to_crs(4326), st.to_crs(4326)
    segs4326.to_file(vd / "canals_manual.geojson", driver="GeoJSON")
    st4326.to_file(vd / "pumps_manual.geojson", driver="GeoJSON")
    segs4326.to_file(gd / "canal_segments_manual.shp")
    st4326.to_file(gd / "pumps_manual.shp")

    report = {
        "canals": int(canals.shape[0]), "segments": int(len(segs)),
        "network_length_km": round(float(segs["len_m"].sum()) / 1000, 1),
        "length_km_by_canal": {str(k): round(v / 1000, 1)
                               for k, v in segs.groupby("canal_id")["len_m"].sum().items()},
        "stations": int(len(st)), "stations_on_canal": int(on_net.sum()),
        "order_scale_km": round(D / 1000, 1), "warnings": warnings}
    (gd / "network_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print("\nNext: upload canal_segments_manual.shp and pumps_manual.shp (all files "
          "with the same name) to GEE as table assets, then run gee/04_canal_status.js.")


if __name__ == "__main__":
    main()
