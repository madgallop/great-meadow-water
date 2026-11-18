"""
water_style.py
==============
Stylised grading for the Great Meadow viewer. Pure numpy/scipy, no raster I/O,
so it can be tested on its own. Used by python/07_stylize_tiles.py.

WHAT IT DOES (and does not do)
------------------------------
Recolours satellite imagery so the WATER is the brightest thing on the map:
  * land: luminance run through a palette ramp (brighter, less "true colour")
  * agriculture/vegetation: picked out from the image's own greenness
  * water: shallow-to-deep colour ramp, soft turbidity streaks, a bright
    highlighter rim at the shoreline and a soft glow just outside it
The SHAPES come straight from the data (the water-class raster). Colour and
glow are styling only: brightness never encodes a measurement, and the
turbidity streaks are decorative, not turbidity data.
"""
import numpy as np
from scipy import ndimage as ndi


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype="float32") / 255.0


PALETTES = {
    # light, bright land; water in electric blue/cyan with a white rim
    "paper": {
        "land": ["#8fb0ad", "#e9dfc2", "#fffaf0"],
        "veg": ["#2f9d6e", "#b9e87c"],
        "shallow": "#5ff0ff", "deep": "#1560ff", "rim": "#ffffff", "glow": "#8ff6ff",
        "background": "#efe6cf",
    },
    # warm, saturated land; turquoise-white water
    "citrus": {
        "land": ["#c9631f", "#f5ac45", "#fff0b8"],
        "veg": ["#16875a", "#86e68f"],
        "shallow": "#6ffff0", "deep": "#1a72ee", "rim": "#ffffff", "glow": "#a8fff6",
        "background": "#f0b45a",
    },
    # dark land, luminous water
    "night": {
        "land": ["#070d26", "#1f3068", "#5874b8"],
        "veg": ["#0c5a49", "#43e8a8"],
        "shallow": "#8afcff", "deep": "#3593ff", "rim": "#ffffff", "glow": "#5ff2ff",
        "background": "#142048",
    },
}


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def ramp(stops_hex, t):
    stops = [hex_rgb(s) for s in stops_hex]
    xs = np.linspace(0, 1, len(stops))
    return np.stack([np.interp(t, xs, [s[c] for s in stops]) for c in range(3)], axis=-1)


class Noise:
    """Smooth, GLOBAL noise sampled by pixel position, so tiles of one image
    never show seams. Stretched along x so it reads as wispy W-E streaks."""

    def __init__(self, height, width, seed=7, sx=150.0, sy=45.0):
        rng = np.random.default_rng(seed)
        self.sx, self.sy = sx, sy
        self.a = ndi.gaussian_filter(
            rng.random((int(height / sy) + 6, int(width / sx) + 6)).astype("float32"), 1.0)
        self.b = ndi.gaussian_filter(
            rng.random((int(height / (sy * 3)) + 6, int(width / (sx * 3)) + 6)).astype("float32"), 1.0)
        for name in ("a", "b"):                      # normalise each octave to 0..1
            arr = getattr(self, name)
            setattr(self, name, (arr - arr.min()) / (arr.max() - arr.min() + 1e-9))

    def sample(self, r0, c0, h, w):
        yy, xx = np.meshgrid(np.arange(r0, r0 + h, dtype="float32"),
                             np.arange(c0, c0 + w, dtype="float32"), indexing="ij")
        na = ndi.map_coordinates(self.a, [yy / self.sy, xx / self.sx], order=1, mode="nearest")
        nb = ndi.map_coordinates(self.b, [yy / (self.sy * 3), xx / (self.sx * 3)], order=1, mode="nearest")
        return np.clip(0.65 * na + 0.35 * nb, 0, 1)


def grade(rgb, cls, pal, noise, px_m, lo, hi, veg_lo=0.03, veg_hi=0.12):
    """
    rgb   (3,h,w) uint8 true-colour; (0,0,0) = no data
    cls   (h,w) uint8 water class: 3 open water, 2 shallow, anything else = land
    pal   palette dict (see PALETTES)
    noise (h,w) float 0..1
    px_m  pixel size in metres
    lo,hi luminance stretch (percentiles of the whole image)
    returns (4,h,w) uint8 RGBA
    """
    p = pal
    f = rgb.astype("float32") / 255.0
    valid = rgb.any(axis=0)
    R, G, B = f[0], f[1], f[2]
    L = 0.2126 * R + 0.7152 * G + 0.0722 * B
    Lp = np.clip((L - lo) / max(hi - lo, 1e-6), 0, 1) ** 0.8          # brighten

    # ---- land and agriculture -------------------------------------------
    land = ramp(p["land"], Lp)
    exg = 2 * G - R - B
    veg = smoothstep(veg_lo, veg_hi, exg)[..., None]
    land = land * (1 - veg) + ramp(p["veg"], Lp) * veg

    # ---- water -------------------------------------------------------------
    open_ = cls == 3
    shallow = cls == 2
    wet = open_ | shallow
    w = np.clip(open_.astype("float32") + 0.7 * shallow, 0, 1)
    w = ndi.gaussian_filter(w, 0.9)                                  # soft shoreline

    dist_in = ndi.distance_transform_edt(wet) * px_m                 # metres from shore, inside
    depth = 1 - np.exp(-dist_in / 450.0)
    depth = np.clip(depth + (noise - 0.5) * 0.40, 0, 1)              # wispy variation
    wcol = np.asarray(hex_rgb(p["shallow"]))[None, None, :] * (1 - depth[..., None]) \
        + np.asarray(hex_rgb(p["deep"]))[None, None, :] * depth[..., None]
    # slow bright streaks, like light on moving water
    light = (np.clip((noise - 0.42) / 0.58, 0, 1) ** 1.3 * 0.55)[..., None]
    wcol = wcol * (1 - light) + np.asarray(hex_rgb(p["shallow"]))[None, None, :] * light * 0.6 \
        + np.asarray(hex_rgb(p["rim"]))[None, None, :] * light * 0.4
    shade = (np.clip((0.35 - noise) / 0.35, 0, 1) * 0.18)[..., None]     # slim darker wisps
    wcol = wcol * (1 - shade)
    # highlighter rim just inside the shoreline
    rim = np.exp(-np.maximum(dist_in - px_m, 0) / 38.0) * wet
    wcol = wcol * (1 - 0.85 * rim[..., None]) + np.asarray(hex_rgb(p["rim"]))[None, None, :] * (0.85 * rim[..., None])

    # glow on the land just outside the shoreline
    dist_out = ndi.distance_transform_edt(~wet) * px_m
    glow = np.exp(-dist_out / 160.0) * (dist_out < 800) * 0.55
    glow = glow * (0.75 + 0.5 * noise)
    land = land * (1 - glow[..., None]) + np.asarray(hex_rgb(p["glow"]))[None, None, :] * glow[..., None]

    out = land * (1 - w[..., None]) + wcol * w[..., None]
    rgba = np.zeros((4,) + L.shape, dtype="uint8")
    rgba[:3] = np.clip(np.moveaxis(out, -1, 0) * 255, 0, 255).astype("uint8")
    rgba[3] = np.where(valid | wet, 255, 0)
    return rgba
