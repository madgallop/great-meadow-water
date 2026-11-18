#!/usr/bin/env python3
"""
06_merge_status.py
==================
Copy the GEE satellite measurements (script 04 exports) onto the viewer's
canal and station files, so Period 3 can show what was observed.

Reads   data/gee_outputs/canal_segment_status.geojson   (key seg_id)
        data/gee_outputs/pump_status.geojson            (key uid)
Updates viewer/data/canals_manual.geojson  (+ seg_status, measurable, wet_after)
        viewer/data/pumps_manual.geojson   (+ pump_status, d_now)

Run it after every GEE export. Safe to repeat: it overwrites its own fields.
Run script 05 first if you change the traced layers (05 rebuilds these files
without the status; run this again after).

USAGE (from EarthEngines/viz)
    python python/06_merge_status.py
"""
import argparse
import json
from collections import Counter
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def merge(viewer_path, gee_path, key, fields):
    viewer, gee = load(viewer_path), load(gee_path)
    by_key = {f["properties"][key]: f["properties"] for f in gee["features"]}
    hit = 0
    for f in viewer["features"]:
        src = by_key.get(f["properties"].get(key))
        if src is None:
            continue
        hit += 1
        for k in fields:
            v = src.get(k)
            f["properties"][k] = round(v, 3) if isinstance(v, float) else v
    Path(viewer_path).write_text(json.dumps(viewer), encoding="utf-8")
    return viewer, hit, len(viewer["features"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gee-dir", default="data/gee_outputs")
    ap.add_argument("--viewer-dir", default="viewer/data")
    a = ap.parse_args()
    g, v = Path(a.gee_dir), Path(a.viewer_dir)

    cv, hit, n = merge(v / "canals_manual.geojson", g / "canal_segment_status.geojson",
                       "seg_id", ["seg_status", "measurable", "wet_after"])
    print(f"canals: matched {hit}/{n} segments")
    c = Counter((f["properties"].get("measurable"), f["properties"].get("seg_status"))
                for f in cv["features"])
    for (m, s), k in sorted(c.items(), key=lambda t: str(t)):
        print(f"   measurable={m}  {s}: {k}")

    pv, hit, n = merge(v / "pumps_manual.geojson", g / "pump_status.geojson",
                       "uid", ["pump_status", "d_now", "d_before"])
    # A station is MEASURABLE only if water was visible within 500 m of it in
    # May 2023. Otherwise (an intake on a canal too narrow to see) 'no water
    # nearby' after tells us nothing about it.
    for f in pv["features"]:
        d = f["properties"].get("d_before")
        f["properties"]["measurable"] = 1 if d is not None and d <= 500 else 0
    Path(v / "pumps_manual.geojson").write_text(json.dumps(pv), encoding="utf-8")
    print(f"stations: matched {hit}/{n}")
    for k, c in sorted(Counter((f["properties"]["measurable"], f["properties"].get("pump_status"))
                               for f in pv["features"]).items(), key=str):
        print(f"   measurable={k[0]}  {k[1]}: {c}")
    if hit < n or cv is None:
        print("WARNING: some features did not match. Are the GEE exports from the "
              "current assets? (seg_id / uid must agree with script 05's output.)")


if __name__ == "__main__":
    main()
