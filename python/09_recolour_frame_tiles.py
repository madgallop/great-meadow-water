#!/usr/bin/env python3
"""
09_recolour_frame_tiles.py
==========================
Recolour drainage-frame tiles that are ALREADY BUILT, from the old
yellow / cyan / blue class colours to the c.1775-map water ramp, without
re-running GDAL or the GEE exports.

    class 1 weak signal   yellow  -> #94ceba (light teal)
    class 2 shallow       cyan    -> #59acaa (mid teal)
    class 3 open water    blue    -> #3e7a84 (dark teal)

Tiles were cut with nearest-neighbour resampling, so each class has an exact
colour and the swap is lossless. Pixels that do not match an old colour are
left alone, so running this twice is safe.

USAGE (from EarthEngines/viz, greatmeadow env)
    python python/09_recolour_frame_tiles.py                 # tiles/frames, tiles/june, tiles/swot
    python python/09_recolour_frame_tiles.py --dirs viewer/tiles/frames
    python python/09_recolour_frame_tiles.py --dry-run       # count only
Hard-refresh the browser afterwards (cmd+shift+R) so it drops cached tiles.
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image

OLD_TO_NEW = {   # RGBA -> RGBA (must match python/03_build_frame_tiles.py)
    (242, 212, 92, 140): (148, 206, 186, 150),
    (84, 206, 224, 200): (89, 172, 170, 215),
    (18, 103, 207, 235): (62, 122, 132, 240),
}


def recolour(arr):
    """arr: (h, w, 4) uint8. Returns (new_array, number_of_pixels_changed)."""
    out = arr.copy()
    changed = 0
    for old, new in OLD_TO_NEW.items():
        m = np.all(arr == np.array(old, dtype=np.uint8), axis=-1)
        if m.any():
            out[m] = new
            changed += int(m.sum())
    return out, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", nargs="+",
                    default=["viewer/tiles/frames", "viewer/tiles/june", "viewer/tiles/swot"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    for d in a.dirs:
        root = Path(d)
        if not root.exists():
            print(f"skip {d} (not found)")
            continue
        files = list(root.rglob("*.png"))
        n_changed_files = n_px = 0
        bad = []
        for f in files:
            try:
                im = Image.open(f).convert("RGBA")
            except Exception:           # empty or half-written PNG: skip, report below
                bad.append(f)
                continue
            new, changed = recolour(np.asarray(im))
            if changed:
                n_changed_files += 1
                n_px += changed
                if not a.dry_run:
                    Image.fromarray(new, "RGBA").save(f, optimize=True)
        verb = "would recolour" if a.dry_run else "recoloured"
        print(f"{d}: {len(files)} tiles, {verb} {n_changed_files} ({n_px:,} pixels)")
        if bad:
            print(f"  skipped {len(bad)} unreadable tile(s), e.g. {bad[0]} ({bad[0].stat().st_size} bytes).")
            print("  Usually harmless (an interrupted earlier build). If they matter, re-run "
                  "python/03_build_frame_tiles.py for that set.")


if __name__ == "__main__":
    main()
