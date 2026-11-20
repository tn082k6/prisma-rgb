"""Persistent state: settings, device configuration, scenes and schedule (JSON in ~/.prisma)."""
import copy
import json
import os
import threading
import uuid
from pathlib import Path

from .effects import EFFECTS, defaults

DATA_DIR = Path(os.environ.get("PRISMA_HOME", Path.home() / ".prisma"))

DEFAULT_SETTINGS = {
    "power": True,
    "master": 100,
    "fps": 60,
    "smoothing": 25,
    "auto_update": True,
    "mqtt_enabled": False,
    "mqtt_host": "",   # set by the user in the app – no network addresses in the code
    "mqtt_port": None,  # empty = MQTT standard port
    "mqtt_user": "",
    "mqtt_pass": "",
    "removed_presets": [],
    "fans_service": False,
    "transition_ms": 700,
    "gamma": 1.0,
    "saturation": 100,
    "wb_r": 100, "wb_g": 100, "wb_b": 100,
    "active_scene": "neon",
    "openrgb_host": "localhost",
    "openrgb_port": None,  # empty = OpenRGB standard port
    "openrgb_path": "",
    "openrgb_autostart": True,
    "openrgb_admin": False,
    "tray": True,
    "start_minimized": False,
    "audio_gain": 100,
}


def new_id():
    return uuid.uuid4().hex[:10]


# ------------------------------------------------------------------ preset scenes
def L(effect, target="all", span="zone", **params):
    p = defaults(effect)
    p.update(params)
    tgt = {"type": target} if isinstance(target, str) and ":" not in target else None
    if isinstance(target, str) and target.startswith("type:"):
        tgt = {"type": "devtype", "dtype": target[5:]}
    return {"id": new_id(), "target": tgt, "effect": effect, "params": p, "span": span, "enabled": True}


def S(sid, name, category, desc, layers):
    return {"id": sid, "name": name, "category": category, "desc": desc, "layers": layers, "builtin": True}


CATEGORY_OF = {}
for _cat, _ids in {
    "White Build": "wb_snow wb_arctic wb_cotton wb_lavender wb_peach wb_champagne wb_mist wb_pastel wb_iceblade "
                   "wb_sakura wb_mint wb_aurora wb_sunrise wb_studio",
    "Gaming": "neon neonstadt gaming_red toxic matrix helix scanner",
    "Bunt": "rainbow rainbow_rotor cascade confetti police ripple heartbeat vaporwave",
    "Natur": "aurora ocean lava campfire forest stars galaxy",
    "Entspannt": "sakura sunset mint ice royal candles blood",
    "Musik": "party club bassdrop",
    "Praktisch": "daylight focus night cpu off",
}.items():
    for _i in _ids.split():
        CATEGORY_OF[_i] = _cat


def preset_scenes():
    scenes = _preset_scenes()
    for s in scenes:
        s["category"] = CATEGORY_OF.get(s["id"], s["category"])
    return scenes


def _white_build_scenes():
    W = "White Build"
    return [
        S("wb_snow", "Schneeweiß", W, "Reines, kühles Weiß – lässt weiße Teile strahlen. Lüfter atmen ganz leicht.", [
            L("white", "all", kelvin=6200),
            L("breathing", "fans", palette=["#ffffff", "#dcefff"], floor=72, speed=12),
        ]),
        S("wb_studio", "Studio-Weiß", W, "Neutrales Weiß wie im Fotostudio – perfekt für Fotos vom Build.", [
            L("white", "all", kelvin=4800, brightness=90),
        ]),
        S("wb_arctic", "Arktis", W, "Eisblaue Wellen auf Weiß, Lüfter mit sanften Eis-Rotoren.", [
            L("wave", "all", "group", palette="arctic", scale=1, speed=18),
            L("rotor", "fans", palette=["#ffffff", "#5fd6ff"], blades=2, duty=55, soft=75, speed=20),
        ]),
        S("wb_iceblade", "Eisrotor", W, "Weißes Grundlicht, darin kreisen hellblaue Flügel.", [
            L("static", "all", color="#dff4ff", brightness=70),
            L("rotor", "fans", palette=["#ffffff", "#7fd8ff", "#ffffff"], blades=3, duty=45, soft=60, speed=28),
        ]),
        S("wb_cotton", "Zuckerwatte", W, "Rosa und Babyblau fließen ineinander – der Klassiker für White Builds.", [
            L("flow", "all", "group", palette="cotton", scale=2, contrast=15, speed=16),
        ]),
        S("wb_lavender", "Lavendelfeld", W, "Weiß mit zartem Lavendel, ruhig und edel.", [
            L("wave", "all", "group", palette="lavender", scale=1, speed=14),
            L("gradient", "type:dram", "group", palette="lavender", mirror=True),
        ]),
        S("wb_peach", "Pfirsichblüte", W, "Warme Pfirsich- und Korallentöne, gemütlich auf Weiß.", [
            L("flow", "all", "group", palette="peach", scale=2, contrast=20, speed=14),
            L("halo", "fans", palette="peach", speed=18),
        ]),
        S("wb_champagne", "Champagner", W, "Warmweiß und Gold – elegant, wie ein Hotel bei Nacht.", [
            L("gradient", "all", "group", palette="champagne", mirror=True),
            L("breathing", "fans", palette=["#fff3dc", "#f5d28a"], floor=55, speed=14),
        ]),
        S("wb_mist", "Morgennebel", W, "Weiße Lichtschleier ziehen langsam durch das Gehäuse.", [
            L("aurora", "all", "group", palette=["#ffffff", "#d8ecff", "#bfe0ff", "#ffffff"], curtain=40, speed=12),
        ]),
        S("wb_pastel", "Pastell-Regenbogen", W, "Ein Regenbogen in Pastell – bunt, aber nie grell.", [
            L("wave", "all", "group", palette="pastel", scale=1, speed=18),
        ]),
        S("wb_sakura", "Kirschblüte Weiß", W, "Weiß und Rosa, auf dem RAM funkeln Blüten.", [
            L("flow", "all", "group", palette=["#ffffff", "#ffd6e4", "#ffb3cb", "#ffffff"], scale=2, contrast=15, speed=14),
            L("twinkle", "type:dram", bg="#ffe6ee", palette=["#ffffff", "#ff9ec0"], density=25, speed=30),
        ]),
        S("wb_mint", "Minze & Weiß", W, "Frisches Mint auf Weiß.", [
            L("wave", "all", "group", palette=["#ffffff", "#bfffe8", "#7dffd0", "#ffffff"], scale=1, speed=15),
        ]),
        S("wb_aurora", "Pastell-Polarlicht", W, "Nordlicht in Pastell – Mint, Hellblau und Flieder.", [
            L("aurora", "all", "group", palette="pastel_aurora", curtain=45, speed=16),
        ]),
        S("wb_sunrise", "Sonnenaufgang Pastell", W, "Pfirsich, Rosé und Himmelblau als sanfter Verlauf.", [
            L("gradient", "all", "group", palette=["#ffd1a8", "#ffb3c7", "#d9c2ff", "#bfe3ff"], mirror=True),
            L("breathing", "fans", palette=["#ffd1a8", "#bfe3ff"], floor=60, speed=12),
        ]),
    ]


def _preset_scenes():
    return _white_build_scenes() + [
        S("neon", "Neon Synthwave", "Gaming", "Pink-violette Wellen mit rotierenden Lüftern.", [
            L("wave", "all", "group", palette="synthwave", scale=1, speed=35),
            L("rotor", "fans", palette=["#ff00a0", "#00d0ff"], blades=2, duty=45, soft=40, speed=40),
        ]),
        S("neonstadt", "Neonstadt", "Gaming", "Grelles Gelb, Cyan und Rot mit digitalen Störungen.", [
            L("glitch", "all", palette="cyberpunk", chaos=45, bg="#1a1400"),
            L("chase", "type:dram", "group", palette=["#fcee0a", "#00f0ff"], gap=2, speed=55),
            L("spiral", "fans", color_a="#fcee0a", color_b="#ff003c", tail=45),
        ]),
        S("gaming_red", "Rote Arena", "Gaming", "Aggressives Rot mit Kometen auf den Lüftern.", [
            L("static", "all", color="#ff0018"),
            L("comet", "fans", palette=["#ffffff", "#ff2000"], count=2, tail=50, bg="#300000", speed=70, flow="fan"),
        ]),
        S("rainbow", "Regenbogen", "Klassisch", "Ein durchgehender Regenbogen über alle Geräte.", [
            L("rainbow", "all", "group", scale=1, speed=30),
        ]),
        S("rainbow_rotor", "Regenbogen-Rotor", "Klassisch", "Vierflügelige Regenbogen-Propeller.", [
            L("rainbow", "all", "group", speed=25),
            L("rotor", "fans", palette="rainbow", blades=4, duty=60, soft=50, speed=45),
        ]),
        S("cascade", "Lüfter-Kaskade", "Klassisch", "Jeder Lüfter in eigener Farbe, die weiterwandert.", [
            L("fanwheel", "fans", palette="rainbow", speed=30),
            L("wave", "all", "group", palette="rainbow", speed=25),
        ]),
        S("aurora", "Nordlicht", "Natur", "Grüne und violette Lichtvorhänge.", [
            L("aurora", "all", "group", palette="aurora", curtain=60, speed=30),
        ]),
        S("ocean", "Tiefsee", "Natur", "Ruhige Wellen in Blau und Türkis.", [
            L("flow", "all", "group", palette="ocean", scale=2.5, contrast=40, speed=25),
            L("wave", "fans", palette="ocean", flow="fan", speed=20),
        ]),
        S("lava", "Vulkan", "Natur", "Zähflüssige Lava und glühender Arbeitsspeicher.", [
            L("flow", "all", "group", palette="lava", scale=3, contrast=60, speed=30),
            L("fire", "type:dram", intensity=80),
        ]),
        S("campfire", "Lagerfeuer", "Natur", "Lodernde Flammen auf allen Geräten.", [
            L("fire", "all", palette="fire", intensity=75, speed=55),
        ]),
        S("forest", "Waldlichtung", "Natur", "Sattes Grün mit Sonnenflecken.", [
            L("flow", "all", "group", palette="forest", scale=2, contrast=30, speed=20),
            L("twinkle", "type:dram", bg="#002a08", palette=["#e0ff80", "#ffffff"], density=20),
        ]),
        S("stars", "Sternennacht", "Natur", "Funkelnde Sterne auf dunkelblauem Himmel.", [
            L("twinkle", "all", bg="#01030f", palette=["#ffffff", "#a0c8ff", "#ffe0a0"], density=30, speed=35),
        ]),
        S("galaxy", "Galaxie", "Natur", "Ziehende Nebel mit Sternen.", [
            L("galaxy", "all", "group", palette="galaxy", stars=50, speed=25),
        ]),
        S("sakura", "Kirschblüte", "Chill", "Zartes Rosa wie im japanischen Frühling.", [
            L("flow", "all", "group", palette="sakura", scale=2, contrast=20, speed=20),
            L("twinkle", "type:dram", bg="#3a0a1a", palette=["#ffffff", "#ffc0d0"], density=25),
        ]),
        S("sunset", "Sonnenuntergang", "Chill", "Warmer Verlauf von Orange zu Violett.", [
            L("gradient", "all", "group", palette="sunset", scale=1, mirror=True),
            L("breathing", "fans", palette=["#ff5e00", "#ff2a68", "#a020a0"], floor=40, speed=20),
        ]),
        S("vaporwave", "Vaporwave", "Chill", "Pastellige Plasma-Wellen.", [
            L("plasma", "all", "group", palette="vaporwave", scale=2, speed=25),
        ]),
        S("mint", "Minzfrisch", "Chill", "Kühle Minze mit sanften Kometen.", [
            L("static", "all", color="#00ffa0", brightness=60),
            L("comet", "fans", palette=["#ffffff", "#00e0ff"], count=1, tail=60, bg="#003020", flow="fan", speed=30),
        ]),
        S("ice", "Eispalast", "Chill", "Klirrend kaltes Blau und Weiß.", [
            L("wave", "all", "group", palette="ice", scale=1.5, speed=20),
        ]),
        S("royal", "Royal", "Chill", "Violett und Gold mit pulsierenden Lüftern.", [
            L("gradient", "all", "group", palette="royal"),
            L("halo", "fans", palette="royal", speed=30),
            L("wave", "type:dram", "group", palette="gold", speed=25),
        ]),
        S("candles", "Kerzenabend", "Gemütlich", "Warmes, flackerndes Kerzenlicht.", [
            L("candle", "all", color="#ff8a1c", flicker=45),
        ]),
        S("night", "Nachtmodus", "Gemütlich", "Sehr gedimmtes warmes Licht für die Nacht.", [
            L("static", "all", color="#ff5a10", brightness=18),
        ]),
        S("daylight", "Tageslicht", "Fokus", "Passt Farbtemperatur und Helligkeit an die Uhrzeit an.", [
            L("daylight", "all"),
        ]),
        S("focus", "Fokus", "Fokus", "Neutrales, ruhiges Arbeitslicht.", [
            L("white", "all", kelvin=5200, brightness=55),
        ]),
        S("blood", "Blutmond", "Stimmung", "Tiefrotes, langsames Atmen.", [
            L("breathing", "all", palette=["#ff0010", "#600000"], floor=10, speed=25),
        ]),
        S("toxic", "Toxisch", "Stimmung", "Giftgrünes Plasma.", [
            L("plasma", "all", "group", palette="toxic", scale=3, speed=40),
            L("meteor", "fans", color="#a0ff00", size=12, decay=70, flow="fan", speed=60),
        ]),
        S("matrix", "Matrix", "Stimmung", "Grüner Code-Regen.", [
            L("matrix", "all", "group", color="#20ff50", density=45),
        ]),
        S("helix", "Doppel-Helix", "Stimmung", "Zwei Lichtschweife kreisen umeinander.", [
            L("spiral", "fans", color_a="#ff2bd6", color_b="#2bd9ff", tail=45, flow="fan", speed=45),
            L("wave", "all", "group", palette=["#ff2bd6", "#2bd9ff"], speed=30),
        ]),
        S("party", "Party", "Musik", "Blitzt im Takt der Musik in allen Farben.", [
            L("music_pulse", "all", palette="rainbow", gain=60),
            L("music_spectrum", "fans", palette="synthwave", gain=60),
        ]),
        S("club", "Club", "Musik", "Frequenzbalken und Klangwellen.", [
            L("music_wave", "all", "group", palette="synthwave", gain=60),
            L("music_spectrum", "fans", palette="vaporwave", gain=65),
        ]),
        S("bassdrop", "Bass-Gewitter", "Musik", "Eisblaue Bass-Blitze.", [
            L("music_pulse", "all", palette=["#ffffff", "#40a0ff", "#0040ff"], gain=70),
        ]),
        S("confetti", "Konfetti", "Party", "Bunte Farbtupfer überall.", [
            L("confetti", "all", palette="pastel", density=55),
        ]),
        S("police", "Blaulicht", "Party", "Rot-blaues Wechselblinken.", [
            L("police", "all", speed=60),
        ]),
        S("ripple", "Regentropfen", "Party", "Tropfen, die Wellenringe ziehen.", [
            L("ripple", "all", "group", palette="mint", rate=50),
        ]),
        S("heartbeat", "Herzschlag", "Party", "Pulsierendes Rot im Herzrhythmus.", [
            L("heartbeat", "all", color="#ff0033", bpm=72),
        ]),
        S("scanner", "Scanner", "Party", "Ein roter Lichtpunkt gleitet hin und her.", [
            L("scanner", "all", "group", color="#ff0030", width=18, bg="#120000", speed=45),
        ]),
        S("cpu", "CPU-Monitor", "System", "Lüfter zeigen die CPU-Last, RAM die Speicherauslastung.", [
            L("cpu", "all", palette="heat", style="color", metric="cpu"),
            L("cpu", "fans", palette="heat", style="fill", metric="cpu"),
            L("cpu", "type:dram", palette="heat", style="fill", metric="ram"),
        ]),
        S("off", "Alles aus", "System", "Alle LEDs dunkel.", [
            L("static", "all", color="#000000"),
        ]),
    ]


class Store:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / "state.json"
        self.lock = threading.RLock()
        self._timer = None
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except Exception:
                data = {}
        self.settings = {**DEFAULT_SETTINGS, **data.get("settings", {})}
        self.devices = data.get("devices", {})
        self.scenes = data.get("scenes") or preset_scenes()
        self.schedule = data.get("schedule", [])
        self.favorites = data.get("favorites", ["neon", "aurora", "campfire", "party", "focus", "night"])
        # add built-in scenes introduced by newer versions (unless the user deleted them on purpose)
        have = {s["id"] for s in self.scenes}
        removed = set(self.settings.get("removed_presets", []))
        new = [p for p in preset_scenes() if p["id"] not in have and p["id"] not in removed]
        if new and data:
            self.scenes = new + self.scenes if new[0]["id"].startswith("wb_") else self.scenes + new
        self._sanitize()
        self.save(now=True)

    def _sanitize(self):
        for sc in self.scenes:
            if sc.get("builtin") and sc.get("id") in CATEGORY_OF:
                sc["category"] = CATEGORY_OF[sc["id"]]
            for ly in sc.get("layers", []):
                if ly.get("effect") not in EFFECTS:
                    ly["effect"] = "static"
                ly["params"] = {**defaults(ly["effect"]), **ly.get("params", {})}
                ly.setdefault("span", "zone")
                ly.setdefault("enabled", True)
                ly.setdefault("id", new_id())
                ly.setdefault("target", {"type": "all"})

    def save(self, now=False):
        with self.lock:
            if self._timer:
                self._timer.cancel()
            if now:
                self._write()
            else:
                self._timer = threading.Timer(0.6, self._write)
                self._timer.daemon = True
                self._timer.start()

    def _write(self):
        with self.lock:
            data = {"settings": self.settings, "devices": self.devices, "scenes": self.scenes,
                    "schedule": self.schedule, "favorites": self.favorites}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, self.path)

    # --- scenes
    def scene(self, sid):
        for s in self.scenes:
            if s["id"] == sid:
                return s
        return None

    def upsert_scene(self, scene):
        with self.lock:
            scene = copy.deepcopy(scene)
            scene.setdefault("id", new_id())
            scene["builtin"] = False if not scene.get("builtin") else scene["builtin"]
            for i, s in enumerate(self.scenes):
                if s["id"] == scene["id"]:
                    self.scenes[i] = scene
                    break
            else:
                self.scenes.append(scene)
            self._sanitize()
            self.save()
            return scene

    def delete_scene(self, sid):
        with self.lock:
            sc = self.scene(sid)
            if sc and sc.get("builtin"):
                self.settings.setdefault("removed_presets", []).append(sid)
            self.scenes = [s for s in self.scenes if s["id"] != sid]
            self.favorites = [f for f in self.favorites if f != sid]
            self.save()

    def reset_scenes(self):
        with self.lock:
            presets = preset_scenes()
            ids = {p["id"] for p in presets}
            custom = [s for s in self.scenes if s["id"] not in ids and not s.get("builtin")]
            self.scenes = presets + custom
            self.save()

    # --- devices
    def device_cfg(self, dev):
        from .devices import guess_layout
        with self.lock:
            cfg = self.devices.setdefault(dev.key, {"enabled": True, "brightness": 100, "control": "prisma",
                                                    "alias": "", "zones": {}})
            for z in dev.zones:
                zc = cfg["zones"].setdefault(str(z.index), {**guess_layout(dev, z), "reverse": False, "offset": 0,
                                                            "enabled": True})
                zc.setdefault("offset", 0)
                zc.setdefault("enabled", True)
            return cfg
