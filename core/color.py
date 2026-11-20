"""Colour helpers: palettes, HSV, value noise. All arrays are float32 in 0..1."""
import numpy as np

PALETTES = {
    "rainbow":   ("Regenbogen", ["#ff0000", "#ffaa00", "#ffff00", "#00ff00", "#00ffff", "#0040ff", "#aa00ff", "#ff0080"]),
    "fire":      ("Feuer", ["#000000", "#5a0000", "#ff2000", "#ff8000", "#ffd040", "#fff4c0"]),
    "lava":      ("Lava", ["#200000", "#a00000", "#ff3000", "#ff7a00", "#a00000"]),
    "ocean":     ("Ozean", ["#001030", "#003080", "#0080c0", "#00d0d0", "#40ffe0", "#0050a0"]),
    "aurora":    ("Nordlicht", ["#00ff90", "#00c0a0", "#2050ff", "#8020ff", "#00ffb0"]),
    "forest":    ("Wald", ["#003000", "#10701a", "#50b020", "#a0d040", "#207020"]),
    "synthwave": ("Synthwave", ["#ff00a0", "#ff3060", "#8000ff", "#2020ff", "#00d0ff"]),
    "cyberpunk": ("Cyberpunk", ["#fcee0a", "#ff003c", "#00f0ff", "#ff003c"]),
    "sakura":    ("Sakura", ["#ffb7c5", "#ff6fa0", "#ffffff", "#ff9ec0", "#c060a0"]),
    "ice":       ("Eis", ["#ffffff", "#a0e8ff", "#40a0ff", "#0040ff", "#a0e8ff"]),
    "toxic":     ("Toxisch", ["#30ff00", "#a0ff00", "#00ff60", "#004000", "#80ff00"]),
    "sunset":    ("Sonnenuntergang", ["#ff5e00", "#ff2a68", "#a020a0", "#3a1070", "#ffa040"]),
    "vaporwave": ("Vaporwave", ["#ff71ce", "#01cdfe", "#05ffa1", "#b967ff", "#fffb96"]),
    "pastel":    ("Pastell", ["#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff", "#e0bbff"]),
    "gold":      ("Gold", ["#402000", "#c08000", "#ffd060", "#fff0c0", "#c08000"]),
    "royal":     ("Royal", ["#2000a0", "#6000ff", "#c040ff", "#ffd700", "#6000ff"]),
    "galaxy":    ("Galaxie", ["#05001a", "#2a0a6a", "#7a20c0", "#ff40c0", "#2040ff", "#05001a"]),
    "mint":      ("Minze", ["#00ffa0", "#00c0ff", "#a0ffe0", "#00ffa0"]),
    "blood":     ("Blutmond", ["#200000", "#ff0010", "#600000", "#ff4020"]),
    "matrix":    ("Matrix", ["#000000", "#003000", "#00a020", "#40ff60", "#d0ffd0"]),
    "police":    ("Blaulicht", ["#ff0000", "#0030ff"]),
    "heat":      ("Auslastung", ["#00ff40", "#a0ff00", "#ffd000", "#ff6000", "#ff0000"]),
    # white-build palettes: bright, low saturation – they bounce nicely off white parts
    "snow":      ("Schnee", ["#ffffff", "#e8f4ff", "#cfe6ff", "#ffffff"]),
    "arctic":    ("Arktis", ["#ffffff", "#bff3ff", "#5fd6ff", "#2a8cff", "#e6faff"]),
    "cotton":    ("Zuckerwatte", ["#ffd1ec", "#c9e7ff", "#ffffff", "#e6d4ff"]),
    "lavender":  ("Lavendel", ["#ffffff", "#e3d4ff", "#b89cff", "#f2ecff"]),
    "peach":     ("Pfirsich", ["#ffe0cc", "#ffb8a0", "#fff1e6", "#ffcfb8"]),
    "champagne": ("Champagner", ["#fff3dc", "#ffe2a8", "#ffffff", "#f5d28a"]),
    "pastel_aurora": ("Pastell-Polarlicht", ["#c8ffe9", "#bfe3ff", "#e2ccff", "#ffffff"]),
}


def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


def rgb_to_hex(c):
    c = np.clip(np.asarray(c) * 255 + 0.5, 0, 255).astype(int)
    return "#%02x%02x%02x" % tuple(c[:3])


def resolve_palette(value):
    """value may be a palette id or a list of hex colours."""
    if isinstance(value, str):
        value = PALETTES.get(value, PALETTES["rainbow"])[1]
    if not value:
        value = ["#ffffff"]
    return np.stack([hex_to_rgb(c) for c in value])


def sample(stops, t, wrap=True):
    """Linear interpolation through palette stops. t: array of 0..1."""
    t = np.asarray(t, dtype=np.float32)
    k = len(stops)
    if k == 1:
        return np.repeat(stops[:1], t.size, axis=0).reshape(t.shape + (3,))
    if wrap:
        x = (t % 1.0) * k
        i0 = np.floor(x).astype(int) % k
        i1 = (i0 + 1) % k
    else:
        x = np.clip(t, 0, 1) * (k - 1)
        i0 = np.clip(np.floor(x).astype(int), 0, k - 2)
        i1 = i0 + 1
    f = (x - np.floor(x))[..., None] if wrap else (x - i0)[..., None]
    return stops[i0] * (1 - f) + stops[i1] * f


def hsv(h, s, v):
    h = np.asarray(h, dtype=np.float32) % 1.0
    s = np.broadcast_to(np.asarray(s, dtype=np.float32), h.shape)
    v = np.broadcast_to(np.asarray(v, dtype=np.float32), h.shape)
    i = np.floor(h * 6).astype(int)
    f = h * 6 - i
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    i %= 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1).astype(np.float32)


def kelvin(k):
    """Approximate RGB of a black body at temperature k (1000..12000)."""
    t = k / 100.0
    if t <= 66:
        r = 255
        g = 99.47 * np.log(t) - 161.12
        b = 0 if t <= 19 else 138.52 * np.log(t - 10) - 305.04
    else:
        r = 329.70 * (t - 60) ** -0.1332
        g = 288.12 * (t - 60) ** -0.0755
        b = 255
    return np.clip(np.array([r, g, b], dtype=np.float32) / 255.0, 0, 1)


def _hash(ix, iy, seed):
    h = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / float(0xFFFFFF)


def noise2(x, y, seed=0):
    """Smooth 2D value noise in 0..1, vectorised."""
    x = np.asarray(x, dtype=np.float64)
    y = np.broadcast_to(np.asarray(y, dtype=np.float64), x.shape)
    ix, iy = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - ix, y - iy
    ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = _hash(ix, iy, seed)
    b = _hash(ix + 1, iy, seed)
    c = _hash(ix, iy + 1, seed)
    d = _hash(ix + 1, iy + 1, seed)
    return (a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy).astype(np.float32)


def fbm(x, y, octaves=3, seed=0):
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        total = total + noise2(x * (2 ** o), y * (2 ** o), seed + o) * amp
        norm += amp
        amp *= 0.5
    return total / norm
