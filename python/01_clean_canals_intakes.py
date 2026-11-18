#!/usr/bin/env python3
"""
01_clean_canals_intakes.py
==========================
Clean the OpenStreetMap canal and "intake station" exports in
EarthEngines/IntakesAndCanals into analysis-ready layers for Earth Engine and
the web viewer. Standard library only (no geopandas needed).

WHY THIS EXISTS
---------------
  * intake_stations.geojson holds 54 points, but only 9 are pumping stations.
    The other 45 are the CENTRE POINTS OF CANAL SEGMENTS (waterway=canal,
    @geometry=center), so "stranded intake" results were mostly measuring
    canal midpoints.
  * canals.geojson mixes canals (560), drains (123) and ditches (103), plus
    2 water polygons, culverts/tunnels no satellite can see, and segments
    with no name, width or construction date.

WHAT IT DOES (nothing is invented; every added field is derived or flagged)
---------------------------------------------------------------------------
  1. Normalises tags (kind, names, width, tunnel -> observable).
  2. Drops degenerate (<5 m) and duplicate geometries, logged in
     rejected_features.csv.
  3. Splits into layers: canals / drains+ditches / waterbodies / pumping
     stations / intakes+discharges.
  4. Classifies canals into a qualitative hierarchy (`phase` 1-3) from NAMING
     PATTERNS ONLY. This is a display sequence, NOT a construction chronology.
  5. Computes `reach_km` (distance from the water source) and `ext_order`
     (0-1) used to animate canals "extending outward". Source = pre-breach
     reservoir edge if you pass --reservoir-dist-csv (from GEE script 04),
     otherwise a FLAGGED fallback: the dam point. Network connectivity is
     only reported (the OSM network is badly fragmented: ~200 components),
     so it is not used for ordering.
  6. Merges real construction dates from a canal_dates.csv when you have one.

DATES (optional)
----------------
canal_dates.csv columns: system,start_year,end_year,date_confidence,source_url,note
`system` = the Ukrainian canal name as in canals_clean.geojson. Until the file
exists every date field is null and date_basis = "none".

USAGE
-----
    python 01_clean_canals_intakes.py \\
        --in-dir  "/Users/madeleinegallop/Documents/GSAPP/EarthEngines/IntakesAndCanals" \\
        --out-dir "/Users/madeleinegallop/Documents/GSAPP/EarthEngines/viz/data/clean"
"""
import argparse
import csv
import json
import math
import os
import re
from collections import defaultdict, Counter

DAM_LONLAT = (33.3700, 46.7781)   # Kakhovka HPP (same as the class night-lights script)
SNAP_TOL_M = 50.0                 # endpoints closer than this count as joined (QA only)
MIN_LEN_M = 5.0

# Meaning of the "Р-n" / "n-МК" codes is not verified; used only to separate
# named trunks from numbered branches. Edit as you learn more.
TRUNK_PATTERNS = [r"магістральн", r"north crimean", r"північно-кримськ",
                  r"дніпро-кривий", r"dnipro-kryvyi"]
BRANCH_PATTERNS = [r"(^|\s)р-\d", r"\d+-мк", r"\d+-мкр"]


# ----------------------------------------------------------------- geometry
def to_xy(lon, lat, lat0=47.0):
    """Local equirectangular metres (fine over this ~200 km area)."""
    return (lon * 111320.0 * math.cos(math.radians(lat0)), lat * 110540.0)


def line_length_m(coords):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        a, b = to_xy(x1, y1), to_xy(x2, y2)
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def midpoint_of_line(coords):
    half, run = line_length_m(coords) / 2.0, 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        a, b = to_xy(x1, y1), to_xy(x2, y2)
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if run + seg >= half and seg > 0:
            t = (half - run) / seg
            return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
        run += seg
    return coords[len(coords) // 2]


# --------------------------------------------------------------------- tags
def parse_width(value):
    if value is None:
        return None
    m = re.search(r"\d+(\.\d+)?", str(value))
    return float(m.group()) if m else None


def best_name(props):
    return (props.get("name:uk") or props.get("name")), props.get("name:en")


def matches(patterns, text):
    text = (text or "").lower()
    return any(re.search(p, text) for p in patterns)


def classify_canal(props):
    """Return (tier, phase). Phase is a DISPLAY ORDER, not a chronology."""
    uk, en = best_name(props)
    name = " ".join(filter(None, [uk, en]))
    if props.get("usage") == "spillway" or "скидання" in name.lower():
        return "discharge", 3
    if matches(TRUNK_PATTERNS, name):
        return "named_trunk", 1
    if matches(BRANCH_PATTERNS, name):
        return "numbered_branch", 2
    return "unnamed_canal", 3


# ------------------------------------------------------- connectivity (QA)
class SnapIndex:
    def __init__(self, tol):
        self.tol, self.cells, self.nodes = tol, defaultdict(list), []

    def node(self, lon, lat):
        x, y = to_xy(lon, lat)
        cx, cy = int(x // self.tol), int(y // self.tol)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in self.cells[(cx + dx, cy + dy)]:
                    if math.hypot(x - self.nodes[idx][2], y - self.nodes[idx][3]) <= self.tol:
                        return idx
        self.nodes.append((lon, lat, x, y))
        self.cells[(cx, cy)].append(len(self.nodes) - 1)
        return len(self.nodes) - 1


def connected_components(segments):
    """Adds component_id to each segment; returns component count."""
    index, adj = SnapIndex(SNAP_TOL_M), defaultdict(set)
    for s in segments:
        a, b = index.node(*s["coords"][0]), index.node(*s["coords"][-1])
        s["n_a"] = a
        adj[a].add(b)
        adj[b].add(a)
    comp, cid = {}, 0
    for start in list(adj):
        if start in comp:
            continue
        stack, comp[start] = [start], cid
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in comp:
                    comp[v] = cid
                    stack.append(v)
        cid += 1
    for s in segments:
        s["component_id"] = comp[s["n_a"]]
    return cid


# ---------------------------------------------------------------------- I/O
def read_geojson(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["features"]


def write_geojson(path, features):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features},
                  fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  wrote {os.path.relpath(path)}  ({len(features)} features)")


def feature(geom_type, coords, props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": geom_type, "coordinates": coords}}


def read_csv_rows(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# Shapefile-safe (<=10 char) field names for the Earth Engine table upload.
GEE_CANAL_FIELDS = {"seg_id": "seg_id", "osm_id": "osm_id", "kind": "kind",
                    "tier": "tier", "phase": "phase", "system": "system",
                    "width_m": "width_m", "length_m": "length_m",
                    "observable": "observ", "ext_order": "ext_order",
                    "reach_km": "reach_km"}
GEE_STATION_FIELDS = {"station_id": "station_id", "osm_id": "osm_id",
                      "kind": "kind", "name_uk": "name_uk"}


def slim(features, mapping):
    out = []
    for f in features:
        props = {}
        for old, new in mapping.items():
            v = f["properties"].get(old)
            props[new] = int(v) if isinstance(v, bool) else v
        out.append(feature(f["geometry"]["type"], f["geometry"]["coordinates"], props))
    return out


# --------------------------------------------------------------------- main
def clean(in_dir, out_dir, dates_csv, dist_csv):
    os.makedirs(out_dir, exist_ok=True)
    rejected, report = [], {}

    # ---------------- canals ------------------------------------------------
    raw = read_geojson(os.path.join(in_dir, "canals.geojson"))
    report["canals_raw_features"] = len(raw)
    segments, drains, waterbodies, seen = [], [], [], set()

    for f in raw:
        p, g = f["properties"], f["geometry"]
        osm_id = p.get("@id", "")
        if g is None:
            rejected.append((osm_id, "no geometry"))
            continue
        if g["type"] == "Polygon":
            waterbodies.append(feature("Polygon", g["coordinates"], {
                "osm_id": osm_id, "kind": p.get("waterway") or "waterbody",
                "water": p.get("water")}))
            continue
        if g["type"] != "LineString":
            rejected.append((osm_id, f"unsupported geometry {g['type']}"))
            continue
        coords = [(round(x, 6), round(y, 6)) for x, y in g["coordinates"]]
        length = line_length_m(coords)
        if length < MIN_LEN_M:
            rejected.append((osm_id, f"shorter than {MIN_LEN_M:g} m"))
            continue
        key = tuple(coords) if coords[0] <= coords[-1] else tuple(reversed(coords))
        if key in seen:
            rejected.append((osm_id, "duplicate geometry"))
            continue
        seen.add(key)

        uk, en = best_name(p)
        tunnel = p.get("tunnel")
        base = {
            "osm_id": osm_id, "kind": p.get("waterway"),
            "name_uk": uk, "name_en": en, "name_ru": p.get("name:ru"),
            "width_m": parse_width(p.get("width")),
            "width_source": "osm_tag" if p.get("width") else None,
            "length_m": round(length, 1),
            "tunnel": tunnel,
            "observable": tunnel is None,        # satellites can't see culverts
            "irrigation_tag": p.get("service") == "irrigation",
            "intermittent": p.get("intermittent") == "yes",
            "designation": p.get("designation"),
        }
        if base["kind"] == "canal":
            tier, phase = classify_canal(p)
            base.update(tier=tier, phase=phase, system=uk or "unnamed")
            segments.append({"props": base, "coords": coords, "length_m": length})
        else:
            base.update(tier="drain_or_ditch", phase=None, system=uk or "unnamed")
            drains.append(feature("LineString", [list(c) for c in coords], base))

    # ---------------- connectivity (QA only) --------------------------------
    n_comp = connected_components(segments)
    comp_len = Counter()
    for s in segments:
        comp_len[s["component_id"]] += s["length_m"]

    # ---------------- animation order ---------------------------------------
    dist_rows = {(r.get("osm_id") or r.get("seg_key")): float(r["dist_to_reservoir_m"])
                 for r in read_csv_rows(dist_csv)
                 if r.get("dist_to_reservoir_m") not in (None, "")}
    order_basis = "reservoir_distance_gee" if dist_rows else "dam_distance_fallback"
    by_phase = defaultdict(list)
    for s in segments:
        oid = s["props"]["osm_id"]
        if oid in dist_rows:
            reach = dist_rows[oid] / 1000.0
        else:
            reach = haversine_km(*midpoint_of_line(s["coords"]), *DAM_LONLAT)
        s["reach"] = reach
        by_phase[s["props"]["phase"]].append(reach)
    span = {ph: (min(v), max(v)) for ph, v in by_phase.items()}
    for s in segments:
        pr = s["props"]
        lo, hi = span[pr["phase"]]
        frac = 0.0 if hi == lo else (s["reach"] - lo) / (hi - lo)
        pr["reach_km"] = round(s["reach"], 2)
        pr["ext_order"] = round(((pr["phase"] - 1) + min(max(frac, 0), 1)) / 3.0, 4)
        pr["component_id"] = s["component_id"]
        pr["component_len_km"] = round(comp_len[s["component_id"]] / 1000, 1)
        pr["order_basis"] = order_basis
        pr["order_note"] = ("display order by hierarchy then distance from the "
                            "reservoir; NOT a construction chronology")

    # ---------------- optional real dates -----------------------------------
    dates = {r["system"]: r for r in read_csv_rows(dates_csv)}
    for s in segments:
        r = dates.get(s["props"]["system"])
        s["props"].update(
            start_year=int(r["start_year"]) if r and r.get("start_year") else None,
            end_year=int(r["end_year"]) if r and r.get("end_year") else None,
            date_confidence=r.get("date_confidence") if r else None,
            date_source=r.get("source_url") if r else None,
            date_basis="documented" if r and r.get("start_year") else "none")

    # Stable IDs by numeric OSM id, so they don't change when ordering changes.
    segments.sort(key=lambda s: int(re.sub(r"\D", "", s["props"]["osm_id"]) or 0))
    out_canals = []
    for i, s in enumerate(segments, 1):
        s["props"]["seg_id"] = f"C{i:04d}"
        s["props"]["seg_key"] = s["props"]["osm_id"]
        out_canals.append(feature("LineString", [list(c) for c in s["coords"]], s["props"]))

    # ---------------- stations, intakes, discharges -------------------------
    pumps, intakes = [], []
    for f in read_geojson(os.path.join(in_dir, "intake_stations.geojson")):
        p, g = f["properties"], f["geometry"]
        if p.get("man_made") == "pumping_station":
            uk, en = best_name(p)
            pumps.append(feature("Point", [round(c, 6) for c in g["coordinates"]], {
                "osm_id": p.get("@id"), "kind": "pumping_station",
                "name_uk": uk, "name_en": en, "named": bool(uk)}))
        else:
            rejected.append((p.get("@id", ""),
                             "intake_stations point is a canal segment centre, not a station"))
    for s in segments:
        pr = s["props"]
        text = " ".join(filter(None, [pr["name_uk"], pr["designation"]])).lower()
        kind = ("water_intake" if ("забір води" in text or "забор воды" in text)
                else "discharge" if pr["tier"] == "discharge" else None)
        if kind:
            lon, lat = midpoint_of_line(s["coords"])
            intakes.append(feature("Point", [round(lon, 6), round(lat, 6)], {
                "osm_id": pr["osm_id"], "kind": kind, "name_uk": pr["name_uk"],
                "seg_id": pr["seg_id"]}))
    for i, ft in enumerate(pumps + intakes, 1):
        ft["properties"]["station_id"] = f"S{i:03d}"

    # ---------------- write -------------------------------------------------
    print("Writing cleaned layers:")
    write_geojson(os.path.join(out_dir, "canals_clean.geojson"), out_canals)
    write_geojson(os.path.join(out_dir, "drains_ditches.geojson"), drains)
    write_geojson(os.path.join(out_dir, "waterbodies.geojson"), waterbodies)
    write_geojson(os.path.join(out_dir, "pumping_stations.geojson"), pumps)
    write_geojson(os.path.join(out_dir, "intakes_discharges.geojson"), intakes)
    gee_dir = os.path.join(out_dir, "for_gee_upload")
    os.makedirs(gee_dir, exist_ok=True)
    write_geojson(os.path.join(gee_dir, "canals_clean.geojson"), slim(out_canals, GEE_CANAL_FIELDS))
    write_geojson(os.path.join(gee_dir, "pumping_stations.geojson"), slim(pumps, GEE_STATION_FIELDS))
    with open(os.path.join(out_dir, "rejected_features.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["osm_id", "reason"])
        w.writerows(rejected)

    report.update({
        "canal_segments_kept": len(segments),
        "canal_km": round(sum(s["length_m"] for s in segments) / 1000, 1),
        "canal_km_observable": round(sum(s["length_m"] for s in segments
                                         if s["props"]["observable"]) / 1000, 1),
        "tiers": dict(Counter(s["props"]["tier"] for s in segments)),
        "drains_ditches": len(drains),
        "waterbody_polygons": len(waterbodies),
        "pumping_stations": len(pumps),
        "intakes_and_discharges": len(intakes),
        "network_components": n_comp,
        "network_note": "OSM network is fragmented; components are QA only",
        "largest_component_km": round(max(comp_len.values()) / 1000, 1) if comp_len else 0,
        "segments_with_width_tag": sum(s["props"]["width_m"] is not None for s in segments),
        "segments_with_documented_dates": sum(s["props"]["date_basis"] == "documented"
                                              for s in segments),
        "order_basis": order_basis,
        "rejected": dict(Counter(r for _, r in rejected)),
    })
    with open(os.path.join(out_dir, "cleaning_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", required=True,
                    help="folder with canals.geojson and intake_stations.geojson")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dates-csv", default=None, help="optional canal_dates.csv")
    ap.add_argument("--reservoir-dist-csv", default=None,
                    help="optional CSV from GEE script 04: osm_id,dist_to_reservoir_m")
    a = ap.parse_args()
    clean(a.in_dir, a.out_dir, a.dates_csv, a.reservoir_dist_csv)
