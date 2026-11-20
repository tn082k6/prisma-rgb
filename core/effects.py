"""Effect library. Every effect renders an (n, 3) float array for a group of LEDs.

Coordinates supplied in ctx:
  pos   0..1 along the whole chain (all zones of the group concatenated)
  angle 0..1 around each fan ring (equals pos inside non-fan zones)
  fan   index of the fan / segment each LED belongs to
  u     the coordinate moving effects should travel along (chain or per-fan, see 'flow')
"""
import math
import time
import numpy as np
from .color import resolve_palette, sample, hsv, hex_to_rgb, noise2, fbm, kelvin

EFFECTS = {}


# ---------------------------------------------------------------- parameter specs
def P_color(key, label, default):
    return {"key": key, "label": label, "type": "color", "default": default}


def P_palette(key, label, default):
    return {"key": key, "label": label, "type": "palette", "default": default}


def P_range(key, label, default, lo, hi, step=1, unit=""):
    return {"key": key, "label": label, "type": "range", "default": default, "min": lo, "max": hi, "step": step, "unit": unit}


def P_select(key, label, default, options):
    return {"key": key, "label": label, "type": "select", "default": default,
            "options": [{"value": v, "label": l} for v, l in options]}


def P_bool(key, label, default):
    return {"key": key, "label": label, "type": "bool", "default": default}


COMMON_MOVING = [
    P_range("speed", "Geschwindigkeit", 50, 1, 100, unit="%"),
    P_select("flow", "Bewegung", "chain", [("chain", "Entlang der Kette"), ("fan", "Pro Lüfter kreisen")]),
    P_bool("reverse", "Richtung umkehren", False),
]
COMMON_ALL = [P_range("brightness", "Helligkeit", 100, 0, 100, unit="%")]


def effect(eid, name, category, icon, params=(), moving=True, audio=False, desc=""):
    def deco(fn):
        spec = list(params) + (COMMON_MOVING if moving else []) + COMMON_ALL
        EFFECTS[eid] = {"id": eid, "name": name, "category": category, "icon": icon, "desc": desc,
                        "params": spec, "moving": moving, "audio": audio, "fn": fn}
        return fn
    return deco


def defaults(eid):
    return {p["key"]: p["default"] for p in EFFECTS[eid]["params"]}


def effect_catalog():
    return [{k: v for k, v in e.items() if k != "fn"} for e in EFFECTS.values()]


class Ctx:
    __slots__ = ("t", "rt", "dt", "n", "idx", "pos", "angle", "fan", "fans", "u", "p", "state", "audio", "sys", "rng")

    def col(self, key):
        return hex_to_rgb(self.p.get(key, "#ffffff"))

    def pal(self, key="palette"):
        return resolve_palette(self.p.get(key, "rainbow"))

    def fill(self, c):
        return np.tile(np.asarray(c, dtype=np.float32), (self.n, 1))


def _ease_breath(x):
    return (1 - np.cos(2 * np.pi * x)) / 2


# ================================================================== BASICS
@effect("static", "Statisch", "Grundlagen", "circle", [P_color("color", "Farbe", "#7c4dff")], moving=False,
        desc="Eine ruhige, feste Farbe.")
def fx_static(c):
    return c.fill(c.col("color"))


@effect("white", "Weißlicht", "Grundlagen", "sun", [P_range("kelvin", "Farbtemperatur", 4500, 1800, 10000, 100, "K")],
        moving=False, desc="Neutrales Licht mit einstellbarer Farbtemperatur.")
def fx_white(c):
    return c.fill(kelvin(c.p["kelvin"]))


@effect("gradient", "Verlauf", "Grundlagen", "blend",
        [P_palette("palette", "Palette", "synthwave"), P_range("scale", "Wiederholungen", 1, 0.25, 4, 0.25),
         P_range("offset", "Verschiebung", 0, 0, 100, unit="%"), P_select("flow", "Verlauf über", "chain",
         [("chain", "Ganze Kette"), ("fan", "Jeden Lüfter")]), P_bool("mirror", "Spiegeln", False)], moving=False,
        desc="Fester Farbverlauf über alle LEDs oder jeden Lüfter.")
def fx_gradient(c):
    x = c.u * c.p["scale"] + c.p["offset"] / 100.0
    if c.p["mirror"]:
        x = 1 - np.abs((x % 1.0) * 2 - 1)
        return sample(c.pal(), x, wrap=False)
    return sample(c.pal(), x)


@effect("breathing", "Atmen", "Grundlagen", "wind",
        [P_palette("palette", "Farben", ["#7c4dff", "#00e5ff"]), P_range("floor", "Minimum", 5, 0, 80, unit="%"),
         P_range("speed", "Geschwindigkeit", 40, 1, 100, unit="%")], moving=False,
        desc="Sanftes Ein- und Ausatmen, wechselt durch die Farben.")
def fx_breathing(c):
    pal = c.pal()
    period = 4.0
    k = int(c.t / period) % len(pal)
    lvl = _ease_breath((c.t / period) % 1.0)
    lvl = c.p["floor"] / 100 + (1 - c.p["floor"] / 100) * lvl
    return c.fill(pal[k] * lvl)


@effect("cycle", "Farbwechsel", "Grundlagen", "refresh",
        [P_palette("palette", "Palette", "rainbow"), P_range("speed", "Geschwindigkeit", 30, 1, 100, unit="%")],
        moving=False, desc="Alle LEDs gleiten gemeinsam durch die Palette.")
def fx_cycle(c):
    return c.fill(sample(c.pal(), np.array([c.t * 0.05]))[0])


# ================================================================== WAVES
@effect("rainbow", "Regenbogen-Welle", "Wellen", "rainbow",
        [P_range("scale", "Wellenlänge", 1, 0.25, 5, 0.25), P_range("sat", "Sättigung", 100, 0, 100, unit="%")],
        desc="Der Klassiker: ein fließender Regenbogen.")
def fx_rainbow(c):
    return hsv(c.u * c.p["scale"] - c.t * 0.15, c.p["sat"] / 100, 1)


@effect("wave", "Paletten-Welle", "Wellen", "waves",
        [P_palette("palette", "Palette", "ocean"), P_range("scale", "Wellenlänge", 1, 0.25, 5, 0.25)],
        desc="Eine Farbpalette wandert über die LEDs.")
def fx_wave(c):
    return sample(c.pal(), c.u * c.p["scale"] - c.t * 0.15)


@effect("plasma", "Plasma", "Wellen", "atom",
        [P_palette("palette", "Palette", "vaporwave"), P_range("scale", "Dichte", 2, 0.5, 6, 0.5)],
        desc="Überlagerte Sinuswellen – organisch und hypnotisch.")
def fx_plasma(c):
    s = c.p["scale"]
    x = c.u * np.pi * 2 * s
    v = np.sin(x + c.t * 0.9) + np.sin(x * 0.6 - c.t * 1.3 + c.fan) + np.sin(x * 1.7 + c.t * 0.4)
    return sample(c.pal(), v / 6 + 0.5 + c.t * 0.02)


@effect("flow", "Fließen", "Wellen", "droplet",
        [P_palette("palette", "Palette", "lava"), P_range("scale", "Struktur", 3, 0.5, 10, 0.5),
         P_range("contrast", "Kontrast", 50, 0, 100, unit="%")],
        desc="Weiches Rauschen wie Lava, Ozean oder Nebel.")
def fx_flow(c):
    v = fbm(c.u * c.p["scale"] + c.fan * 3.7, c.t * 0.25, 3, seed=7)
    k = 1 + c.p["contrast"] / 25
    v = np.clip((v - 0.5) * k + 0.5, 0, 1)
    return sample(c.pal(), v, wrap=False)


@effect("aurora", "Nordlicht", "Wellen", "sparkles",
        [P_palette("palette", "Palette", "aurora"), P_range("curtain", "Vorhänge", 50, 0, 100, unit="%")],
        desc="Tanzende Lichtvorhänge am Polarhimmel.")
def fx_aurora(c):
    col = sample(c.pal(), noise2(c.u * 2 + c.fan * 5, c.t * 0.15, 3) * 1.4)
    curtain = noise2(c.u * 6 - c.t * 0.4, c.t * 0.3, 11)
    lvl = np.clip(1 - c.p["curtain"] / 100 + curtain * (c.p["curtain"] / 50), 0, 1) ** 1.5
    return col * lvl[:, None]


@effect("ripple", "Wellenringe", "Wellen", "radar",
        [P_palette("palette", "Palette", "mint"), P_range("rate", "Häufigkeit", 40, 1, 100, unit="%"),
         P_range("width", "Breite", 30, 5, 100, unit="%")],
        desc="Tropfen fallen und breiten sich in Ringen aus.")
def fx_ripple(c):
    st = c.state
    drops = st.setdefault("drops", [])
    if c.rng.random() < c.dt * (0.3 + c.p["rate"] / 25):
        drops.append([c.rng.random(), c.t, c.rng.random()])
    out = np.zeros((c.n, 3), np.float32)
    pal = c.pal()
    w = c.p["width"] / 400 + 0.01
    keep = []
    for x0, t0, hue in drops:
        age = c.t - t0
        r = age * 0.25
        if r > 1.2:
            continue
        keep.append([x0, t0, hue])
        d = np.abs(np.abs(c.u - x0) - r)
        inten = np.exp(-(d / w) ** 2) * max(0.0, 1 - r / 1.2)
        out += sample(pal, np.array([hue]))[0] * inten[:, None]
    st["drops"] = keep[-24:]
    return np.clip(out, 0, 1)


# ================================================================== MOTION
@effect("comet", "Komet", "Bewegung", "comet",
        [P_palette("palette", "Kometen-Farben", ["#00e5ff", "#ff00e5"]), P_range("count", "Anzahl", 1, 1, 6),
         P_range("tail", "Schweiflänge", 35, 2, 100, unit="%"), P_color("bg", "Hintergrund", "#000000")],
        desc="Leuchtende Kometen ziehen ihre Bahn.")
def fx_comet(c):
    pal = c.pal()
    cnt = int(c.p["count"])
    tail = c.p["tail"] / 100
    out = np.tile(c.col("bg"), (c.n, 1))
    for k in range(cnt):
        head = (c.t * 0.2 + k / cnt) % 1.0
        d = (head - c.u) % 1.0
        inten = np.clip(1 - d / tail, 0, 1) ** 2.2
        out = out * (1 - inten[:, None]) + pal[k % len(pal)] * inten[:, None]
    return out


@effect("rotor", "Rotor", "Lüfter", "fan",
        [P_palette("palette", "Flügel-Farben", ["#ff0055", "#00d5ff"]), P_range("blades", "Flügel", 3, 1, 8),
         P_range("duty", "Flügelbreite", 50, 5, 95, unit="%"), P_range("soft", "Weichheit", 30, 0, 100, unit="%"),
         P_range("speed", "Geschwindigkeit", 50, 1, 100, unit="%"), P_select("flow", "Bewegung", "fan",
         [("fan", "Pro Lüfter kreisen"), ("chain", "Entlang der Kette")]), P_bool("reverse", "Richtung umkehren", False),
         P_bool("alternate", "Lüfter gegenläufig", False)],
        moving=False, desc="Rotierende Flügel auf jedem Lüfter – wie ein Propeller aus Licht.")
def fx_rotor(c):
    dirs = np.where((c.fan % 2 == 1) & bool(c.p["alternate"]), -1, 1)
    blades = int(c.p["blades"])
    x = (c.u * blades + c.t * 0.35 * dirs) % 1.0
    blade_idx = np.floor((c.u * blades + c.t * 0.35 * dirs)).astype(int)
    pal = c.pal()
    duty = c.p["duty"] / 100
    soft = c.p["soft"] / 100 * 0.25 + 1e-3
    inten = np.clip((duty - x) / soft, 0, 1) * np.clip(x / soft, 0, 1)
    col = pal[blade_idx % len(pal)]
    return col * inten[:, None]


@effect("spiral", "Doppel-Helix", "Lüfter", "dna",
        [P_color("color_a", "Farbe A", "#ff2bd6"), P_color("color_b", "Farbe B", "#2bd9ff"),
         P_range("tail", "Länge", 40, 5, 100, unit="%")],
        desc="Zwei Lichtschweife kreisen gegeneinander.")
def fx_spiral(c):
    tail = c.p["tail"] / 100
    ha = (c.t * 0.25) % 1.0
    hb = (-c.t * 0.25 + 0.5) % 1.0
    ia = np.clip(1 - ((ha - c.u) % 1.0) / tail, 0, 1) ** 2
    ib = np.clip(1 - ((c.u - hb) % 1.0) / tail, 0, 1) ** 2
    return np.clip(c.col("color_a") * ia[:, None] + c.col("color_b") * ib[:, None], 0, 1)


@effect("halo", "Halo-Puls", "Lüfter", "target",
        [P_palette("palette", "Farben", "royal"), P_range("speed", "Geschwindigkeit", 40, 1, 100, unit="%")],
        moving=False, desc="Lüfter pulsieren nacheinander wie eine Welle.")
def fx_halo(c):
    pal = c.pal()
    fans = max(1, c.fans)
    phase = (c.t * 0.3 - c.fan / fans) % 1.0
    lvl = _ease_breath(phase) ** 1.5
    col = pal[(np.floor(c.t * 0.3).astype(int) + c.fan) % len(pal)]
    return col * lvl[:, None]


@effect("fanwheel", "Lüfter-Kaskade", "Lüfter", "layers",
        [P_palette("palette", "Palette", "rainbow"), P_range("speed", "Geschwindigkeit", 40, 1, 100, unit="%")],
        moving=False, desc="Jeder Lüfter bekommt eine eigene Farbe, die weiterwandert.")
def fx_fanwheel(c):
    fans = max(1, c.fans)
    return sample(c.pal(), c.fan / fans + c.t * 0.05 + c.angle * 0.08)


@effect("chase", "Lauflicht", "Bewegung", "dots",
        [P_palette("palette", "Farben", ["#ff3d00", "#ffd600"]), P_range("gap", "Abstand", 3, 1, 10),
         P_color("bg", "Hintergrund", "#000000")],
        desc="Theater-Lauflicht mit wechselnden Farben.")
def fx_chase(c):
    gap = int(c.p["gap"]) + 1
    step = np.floor(c.t * 3).astype(int)
    on = ((c.idx + step) % gap) == 0
    pal = c.pal()
    col = pal[((c.idx + step) // gap) % len(pal)]
    out = np.tile(c.col("bg"), (c.n, 1))
    out[on] = col[on]
    return out


@effect("scanner", "Scanner", "Bewegung", "scan",
        [P_color("color", "Farbe", "#ff0030"), P_range("width", "Breite", 15, 2, 60, unit="%"),
         P_color("bg", "Hintergrund", "#100000")],
        desc="Ein Lichtpunkt gleitet hin und her.")
def fx_scanner(c):
    x = 1 - abs((c.t * 0.25) % 2 - 1)
    w = c.p["width"] / 100
    inten = np.exp(-((c.u - x) / (w * 0.5)) ** 2)
    return c.col("bg") * (1 - inten[:, None]) + c.col("color") * inten[:, None]


@effect("wipe", "Farbwischer", "Bewegung", "brush",
        [P_palette("palette", "Farben", "cyberpunk"), P_range("soft", "Kante", 10, 0, 50, unit="%")],
        desc="Farben schieben sich nacheinander über die LEDs.")
def fx_wipe(c):
    pal = c.pal()
    period = c.t * 0.25
    k = int(period) % len(pal)
    f = period % 1.0
    soft = c.p["soft"] / 100 + 1e-3
    m = np.clip((f * (1 + soft) - c.u) / soft, 0, 1)
    return pal[k - 1] * (1 - m[:, None]) + pal[k] * m[:, None]


@effect("meteor", "Meteorregen", "Bewegung", "meteor",
        [P_color("color", "Farbe", "#c8f0ff"), P_range("size", "Größe", 8, 2, 30, unit="%"),
         P_range("decay", "Nachglühen", 60, 0, 100, unit="%")],
        desc="Meteore mit flackerndem, zerfallendem Schweif.")
def fx_meteor(c):
    st = c.state
    buf = st.get("buf")
    if buf is None or len(buf) != c.n:
        buf = np.zeros(c.n, np.float32)
    decay = 1 - (1 - c.p["decay"] / 100) * 0.6
    rnd = c.rng.random(c.n) < 0.5
    buf = np.where(rnd, buf * decay ** (c.dt * 30), buf)
    head = (c.t * 0.3) % 1.3
    size = c.p["size"] / 100
    buf = np.maximum(buf, (np.abs(c.u - head) < size / 2).astype(np.float32))
    st["buf"] = buf
    return c.col("color") * buf[:, None]


# ================================================================== NATURE
@effect("fire", "Feuer", "Natur", "flame",
        [P_palette("palette", "Palette", "fire"), P_range("intensity", "Intensität", 70, 10, 100, unit="%"),
         P_range("speed", "Geschwindigkeit", 55, 1, 100, unit="%")], moving=False,
        desc="Lodernde Flammen mit glühender Glut.")
def fx_fire(c):
    v = fbm(c.u * 6 + c.fan * 9.1, c.t * 1.6, 3, seed=3)
    flick = noise2(c.idx * 0.7, c.t * 6, 5)
    h = np.clip(v * 1.3 * (c.p["intensity"] / 70) * (0.75 + flick * 0.45) - 0.15, 0, 1)
    return sample(c.pal(), h, wrap=False)


@effect("candle", "Kerzenschein", "Natur", "candle",
        [P_color("color", "Flammenfarbe", "#ff8a1c"), P_range("flicker", "Flackern", 45, 0, 100, unit="%")],
        moving=False, desc="Gemütliches, unruhiges Kerzenlicht.")
def fx_candle(c):
    f = c.p["flicker"] / 100
    n1 = noise2(c.idx * 0.15 + c.fan * 4, c.rt * 7, 21)
    n2 = noise2(c.idx * 0.05, c.rt * 1.3, 22)
    lvl = 1 - f * 0.8 * (1 - (n1 * 0.6 + n2 * 0.4))
    col = c.col("color")
    warm = np.array([1.0, 0.75, 0.55], np.float32)
    return (col * warm ** (1 - lvl[:, None])) * lvl[:, None]


@effect("twinkle", "Sternenhimmel", "Natur", "star",
        [P_color("bg", "Himmel", "#020418"), P_palette("palette", "Sternfarben", ["#ffffff", "#a0c8ff", "#ffe0a0"]),
         P_range("density", "Sterndichte", 35, 1, 100, unit="%"), P_range("speed", "Funkeltempo", 40, 1, 100, unit="%")],
        moving=False, desc="Funkelnde Sterne auf dunklem Himmel.")
def fx_twinkle(c):
    st = c.state
    lv = st.get("lv")
    if lv is None or len(lv) != c.n:
        lv = np.zeros(c.n, np.float32)
        st["hue"] = c.rng.random(c.n).astype(np.float32)
        st["dir"] = np.zeros(c.n, np.float32)
    d = st["dir"]
    spawn = (c.rng.random(c.n) < c.dt * c.p["density"] / 30) & (lv <= 0.01)
    d[spawn] = 1
    st["hue"][spawn] = c.rng.random(int(spawn.sum()))
    sp = c.dt * (0.6 + c.p["speed"] / 30)
    lv = np.clip(lv + np.where(d > 0, sp * 2, -sp), 0, 1)
    d[lv >= 1] = -1
    st["lv"], st["dir"] = lv, d
    stars = sample(c.pal(), st["hue"], wrap=False)
    e = (lv ** 2)[:, None]
    return c.col("bg") * (1 - e) + stars * e


@effect("galaxy", "Galaxie", "Natur", "orbit",
        [P_palette("palette", "Nebel", "galaxy"), P_range("stars", "Sterne", 40, 0, 100, unit="%")],
        desc="Ziehende Nebel mit funkelnden Sternen.")
def fx_galaxy(c):
    neb = sample(c.pal(), fbm(c.u * 3 + c.fan * 2, c.t * 0.12, 3, 31) * 1.2, wrap=False) * 0.85
    tw = noise2(c.idx * 13.37, c.rt * 2.5, 33)
    star = np.clip((tw - (1 - c.p["stars"] / 400)) * 12, 0, 1)
    return np.clip(neb + star[:, None], 0, 1)


@effect("matrix", "Matrix-Regen", "Natur", "code",
        [P_color("color", "Farbe", "#20ff50"), P_range("density", "Dichte", 40, 5, 100, unit="%")],
        desc="Grüner Code-Regen wie im Film.")
def fx_matrix(c):
    st = c.state
    buf = st.get("buf")
    if buf is None or len(buf) != c.n:
        buf = np.zeros(c.n, np.float32)
        st["acc"] = 0.0
    st["acc"] += c.dt * 12
    while st["acc"] >= 1:
        st["acc"] -= 1
        buf = np.roll(buf, 1) * 0.86
        if c.rng.random() < c.p["density"] / 100:
            buf[0] = 1.0
    st["buf"] = buf
    order = np.argsort(np.argsort(c.u))
    v = buf[order]
    col = c.col("color")
    head = (v > 0.95)[:, None]
    return np.where(head, np.array([0.85, 1, 0.85], np.float32), col * v[:, None])


@effect("daylight", "Tageslicht", "Natur", "sunrise",
        [P_range("night", "Nachts", 2200, 1500, 4000, 100, "K"), P_range("day", "Mittags", 6500, 4000, 10000, 100, "K"),
         P_range("dim", "Nachts dimmen", 60, 0, 95, unit="%")], moving=False,
        desc="Folgt der Uhrzeit: kühl am Tag, warm und gedimmt am Abend.")
def fx_daylight(c):
    lt = time.localtime()
    h = lt.tm_hour + lt.tm_min / 60
    day = max(0.0, math.sin((h - 6) / 14 * math.pi)) if 6 <= h <= 20 else 0.0
    k = c.p["night"] + (c.p["day"] - c.p["night"]) * day
    lvl = 1 - c.p["dim"] / 100 * (1 - day)
    return c.fill(kelvin(k) * lvl)


# ================================================================== ACTION
@effect("heartbeat", "Herzschlag", "Action", "heart",
        [P_color("color", "Farbe", "#ff0033"), P_range("bpm", "Puls (BPM)", 70, 30, 180)], moving=False,
        desc="Doppelschlag eines Herzens.")
def fx_heart(c):
    beat = (c.rt * c.p["bpm"] / 60) % 1.0
    lvl = math.exp(-((beat - 0.08) / 0.05) ** 2) + 0.7 * math.exp(-((beat - 0.28) / 0.06) ** 2)
    return c.fill(c.col("color") * (0.08 + 0.92 * min(1.0, lvl)))


@effect("police", "Blaulicht", "Action", "siren",
        [P_color("color_a", "Farbe A", "#ff0000"), P_color("color_b", "Farbe B", "#0030ff"),
         P_range("speed", "Geschwindigkeit", 60, 1, 100, unit="%")], moving=False,
        desc="Wechselblinker wie bei Einsatzfahrzeugen.")
def fx_police(c):
    ph = (c.t * 1.5) % 2.0
    side = (c.angle < 0.5) if c.fans else (c.pos < 0.5)
    flash = (int(c.t * 12) % 2 == 0) and (ph % 1.0 < 0.6)
    a_on = ph < 1.0 and flash
    b_on = ph >= 1.0 and flash
    out = np.zeros((c.n, 3), np.float32)
    out[side] = c.col("color_a") * a_on
    out[~side] = c.col("color_b") * b_on
    return out


@effect("strobe", "Stroboskop", "Action", "zap",
        [P_color("color", "Farbe", "#ffffff"), P_range("hz", "Frequenz", 6, 1, 20, unit=" Hz"),
         P_range("duty", "Leuchtdauer", 25, 5, 80, unit="%")], moving=False,
        desc="Harte Lichtblitze. Vorsicht bei Lichtempfindlichkeit.")
def fx_strobe(c):
    on = (c.rt * c.p["hz"]) % 1.0 < c.p["duty"] / 100
    return c.fill(c.col("color") * on)


@effect("glitch", "Glitch", "Action", "glitch",
        [P_palette("palette", "Farben", "cyberpunk"), P_range("chaos", "Chaos", 40, 1, 100, unit="%"),
         P_color("bg", "Grundfarbe", "#0a0014")], moving=False,
        desc="Digitale Störungen und Farbblöcke.")
def fx_glitch(c):
    st = c.state
    out = st.get("out")
    if out is None or len(out) != c.n:
        out = np.tile(c.col("bg"), (c.n, 1))
    out = out * 0.82 + c.col("bg") * 0.18
    if c.rng.random() < c.dt * c.p["chaos"] / 6:
        a = c.rng.integers(0, c.n)
        b = min(c.n, a + c.rng.integers(1, max(2, c.n // 4)))
        pal = c.pal()
        out[a:b] = pal[c.rng.integers(0, len(pal))]
    st["out"] = out
    return out


@effect("confetti", "Konfetti", "Action", "party",
        [P_palette("palette", "Palette", "pastel"), P_range("density", "Menge", 50, 1, 100, unit="%"),
         P_range("speed", "Verblassen", 50, 1, 100, unit="%")], moving=False,
        desc="Bunte Farbtupfer, die aufblitzen und verblassen.")
def fx_confetti(c):
    st = c.state
    out = st.get("out")
    if out is None or len(out) != c.n:
        out = np.zeros((c.n, 3), np.float32)
    out *= (1 - c.dt * (0.5 + c.p["speed"] / 25))
    spawn = c.rng.random(c.n) < c.dt * c.p["density"] / 25
    if spawn.any():
        out[spawn] = sample(c.pal(), c.rng.random(int(spawn.sum())))
    st["out"] = np.clip(out, 0, 1)
    return st["out"]


# ================================================================== REACTIVE
@effect("music_spectrum", "Musik-Spektrum", "Reaktiv", "music",
        [P_palette("palette", "Palette", "synthwave"), P_range("gain", "Empfindlichkeit", 60, 1, 100, unit="%"),
         P_select("style", "Stil", "bars", [("bars", "Frequenzbalken"), ("glow", "Ganzflächig leuchten")])],
        moving=False, audio=True, desc="Visualisiert die Musik, die gerade läuft.")
def fx_music_spectrum(c):
    bands = c.audio.bands
    g = 0.4 + c.p["gain"] / 50
    if c.p["style"] == "glow":
        lvl = min(1.0, c.audio.level * g)
        col = sample(c.pal(), np.array([c.t * 0.03 + c.audio.bass * 0.3]))[0]
        return c.fill(col * (0.05 + 0.95 * lvl))
    b = np.interp(c.angle if c.fans else c.pos, np.linspace(0, 1, len(bands)), bands)
    lvl = np.clip(b * g, 0, 1)
    return sample(c.pal(), c.angle if c.fans else c.pos, wrap=False) * lvl[:, None]


@effect("music_pulse", "Bass-Puls", "Reaktiv", "speaker",
        [P_palette("palette", "Farben", "rainbow"), P_range("gain", "Empfindlichkeit", 60, 1, 100, unit="%"),
         P_bool("shift", "Farbe bei jedem Beat wechseln", True)], moving=False, audio=True,
        desc="Blitzt im Takt des Basses.")
def fx_music_pulse(c):
    st = c.state
    st.setdefault("k", 0)
    st.setdefault("env", 0.0)
    if c.audio.beat:
        st["env"] = 1.0
        if c.p["shift"]:
            st["k"] += 1
    st["env"] = max(c.audio.bass * (0.4 + c.p["gain"] / 60), st["env"] * math.exp(-c.dt * 5))
    pal = c.pal()
    return c.fill(pal[st["k"] % len(pal)] * min(1.0, 0.05 + st["env"]))


@effect("music_wave", "Klangwelle", "Reaktiv", "audio-wave",
        [P_palette("palette", "Palette", "ocean"), P_range("gain", "Empfindlichkeit", 60, 1, 100, unit="%")],
        audio=True, desc="Die Lautstärke schickt Wellen über die LEDs.")
def fx_music_wave(c):
    st = c.state
    hist = st.get("hist")
    if hist is None:
        hist = np.zeros(64, np.float32)
    st["acc"] = st.get("acc", 0) + c.dt * 30 * (0.3 + c.p.get("speed", 50) / 60)
    while st["acc"] >= 1:
        st["acc"] -= 1
        hist = np.roll(hist, 1)
        hist[0] = min(1.0, c.audio.level * (0.4 + c.p["gain"] / 50))
    st["hist"] = hist
    lvl = np.interp(c.u, np.linspace(0, 1, 64), hist)
    return sample(c.pal(), c.u + c.t * 0.02) * lvl[:, None]


@effect("cpu", "CPU-Monitor", "Reaktiv", "cpu",
        [P_palette("palette", "Skala", "heat"), P_select("style", "Anzeige", "fill",
         [("fill", "Füllstand"), ("color", "Ganze Farbe")]), P_select("metric", "Messwert", "cpu",
         [("cpu", "CPU-Auslastung"), ("ram", "RAM-Auslastung")])], moving=False,
        desc="Zeigt die Auslastung deines PCs in Farbe.")
def fx_cpu(c):
    v = c.sys.cpu if c.p["metric"] == "cpu" else c.sys.ram
    pal = c.pal()
    if c.p["style"] == "color":
        return c.fill(sample(pal, np.array([v]), wrap=False)[0])
    x = c.angle if c.fans else c.pos
    col = sample(pal, x, wrap=False)
    on = np.clip((v - x) * c.n * 0.5 + 1, 0.06, 1)
    return col * on[:, None]
