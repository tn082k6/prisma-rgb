"""JSON API used by the UI, served over a local token-protected HTTP endpoint."""
import copy
import json
import logging
import mimetypes
import os
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .color import PALETTES
from .effects import effect_catalog, defaults, EFFECTS
from .store import new_id
from .version import VERSION

log = logging.getLogger("prisma.api")

CONFLICTS = {
    "RGBFusion": "Gigabyte RGB Fusion",
    "GBT_DL_LIB": "Gigabyte RGB-Hilfsdienst",
    "iCUE": "Corsair iCUE",
    "RazerAppEngine": "Razer Synapse",
    "SignalRgb": "SignalRGB",
    "LightingService": "ASUS Aura",
}

STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Prisma.lnk"


class Api:
    def __init__(self, store, backend, engine, audio, app_dir):
        self.store, self.backend, self.engine, self.audio = store, backend, engine, audio
        self.app_dir = Path(app_dir)
        self.window = None

    # ---------------------------------------------------------------- read
    def state(self):
        st = self.store
        devs = []
        for d in self.backend.devices:
            dd = d.to_dict()
            dd["cfg"] = st.device_cfg(d)
            devs.append(dd)
        return {
            "settings": {**st.settings, "mqtt_pass": "set" if st.settings.get("mqtt_pass") else ""},  # never send the secret
            "scenes": st.scenes,
            "favorites": st.favorites,
            "schedule": st.schedule,
            "devices": devs,
            "effects": effect_catalog(),
            "palettes": {k: {"name": v[0], "colors": v[1]} for k, v in PALETTES.items()},
            "backend": {"demo": self.backend.demo, "status": self.backend.status, "version": self.backend.version},
            "audio": {"available": self.audio.available, "error": self.audio.error},
            "autostart": STARTUP_LNK.exists(),
            "version": VERSION,
            "update": self.updater.state if getattr(self, "updater", None) else None,
            "ha": self.ha.info() if getattr(self, "ha", None) else None,
        }

    # ---------------------------------------------------------------- fans
    def fan_status(self):
        st = self.fans.status() if getattr(self, "fans", None) else None
        return {"running": st is not None, "status": st, "task": self.fans.task_exists() if st is None else True,
                "service_wanted": self.store.settings.get("fans_service", False)}

    def fan_config(self, cfg):
        return self.fans.request({"cmd": "config", "config": cfg})

    def fan_profile(self, name):
        return self.fans.set_profile(name)

    def fan_enable(self, on):
        return self.fans.request({"cmd": "enable", "on": bool(on)})

    def fan_calibrate(self):
        return self.fans.request({"cmd": "calibrate"})

    def fan_import(self):
        return self.fans.request({"cmd": "import"})

    def fan_setup(self):
        """One UAC prompt: create the elevated task, retire FanControl, start the service."""
        import ctypes
        script = self.app_dir / "fansetup.ps1"
        user = f'{os.environ.get("USERDOMAIN", "")}\\{os.environ.get("USERNAME", "")}'
        args = f'-NoProfile -ExecutionPolicy Bypass -File "{script}" -User "{user}" -UserProfile "{os.environ["USERPROFILE"]}"'
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, str(self.app_dir), 0)
        if r <= 32:
            return {"ok": False, "msg": "Einrichtung abgebrochen."}
        self.store.settings["fans_service"] = True
        self.store.save()
        return {"ok": True, "msg": "Lüfter-Dienst wird eingerichtet …"}

    def ha_info(self):
        return self.ha.info() if getattr(self, "ha", None) else None

    # ---------------------------------------------------------------- updates
    def update_check(self):
        return self.updater.check() if getattr(self, "updater", None) else None

    def update_install(self):
        return self.updater.install() if getattr(self, "updater", None) else None

    def frame(self):
        prev = self.engine.preview
        return {
            "fps": round(self.engine.fps_real, 1),
            "ms": round(self.engine.frame_ms, 2),
            "power": self.store.settings.get("power", True),
            "demo": self.backend.demo,
            "status": self.backend.status,
            "dv": self.backend.version,
            "scene": self.store.settings.get("active_scene"),
            "master": self.store.settings.get("master", 100),
            "cpu": round(self.engine.system.cpu, 3),
            "level": round(float(self.audio.level), 3),
            "devices": {k: (v.tobytes().hex() if v is not None else None) for k, v in prev.items()},
        }

    # ---------------------------------------------------------------- scenes
    def set_scene(self, sid):
        self.engine.set_scene(sid)
        self.store.settings["power"] = True
        return True

    def save_scene(self, scene):
        s = self.store.upsert_scene(scene)
        self.engine.invalidate()
        return s

    def new_scene(self, name="Neue Szene"):
        layer = {"id": new_id(), "target": {"type": "all"}, "effect": "wave", "params": defaults("wave"),
                 "span": "group", "enabled": True}
        s = self.store.upsert_scene({"id": new_id(), "name": name, "category": "Eigene", "desc": "",
                                     "layers": [layer], "builtin": False})
        return s

    def duplicate_scene(self, sid):
        src = self.store.scene(sid)
        if not src:
            return None
        s = copy.deepcopy(src)
        s["id"], s["name"], s["builtin"], s["category"] = new_id(), src["name"] + " (Kopie)", False, "Eigene"
        for ly in s["layers"]:
            ly["id"] = new_id()
        return self.store.upsert_scene(s)

    def import_scene(self, scene):
        scene = copy.deepcopy(scene)
        scene["id"], scene["builtin"] = new_id(), False
        scene.setdefault("category", "Eigene")
        for ly in scene.get("layers", []):
            ly["id"] = new_id()
        return self.store.upsert_scene(scene)

    def delete_scene(self, sid):
        self.store.delete_scene(sid)
        if self.store.settings.get("active_scene") == sid and self.store.scenes:
            self.engine.set_scene(self.store.scenes[0]["id"])
        self.engine.invalidate()
        return True

    def reset_scenes(self):
        self.store.reset_scenes()
        self.engine.invalidate()
        return True

    def set_favorite(self, sid, on):
        favs = [f for f in self.store.favorites if f != sid]
        if on:
            favs.append(sid)
        self.store.favorites = favs
        self.store.save()
        return favs

    def effect_defaults(self, eid):
        return defaults(eid) if eid in EFFECTS else {}

    # ---------------------------------------------------------------- settings
    def set_settings(self, patch):
        if patch.get("mqtt_pass") == "set":  # placeholder echoed back by the UI
            patch = {k: v for k, v in patch.items() if k != "mqtt_pass"}
        self.store.settings.update(patch)
        self.store.save()
        if any(k in patch for k in ("openrgb_host", "openrgb_port")):
            threading.Thread(target=self.backend.reconnect, daemon=True).start()
        if any(k.startswith("mqtt_") for k in patch) and getattr(self, "ha", None):
            threading.Thread(target=self.ha.restart, daemon=True).start()
        return self.store.settings

    def set_power(self, on):
        self.store.settings["power"] = bool(on)
        self.store.save()
        return on

    # ---------------------------------------------------------------- devices
    def set_device(self, key, patch):
        d = self.backend.get(key)
        if not d:
            return False
        cfg = self.store.device_cfg(d)
        cfg.update({k: v for k, v in patch.items() if k != "zones"})
        self.store.save()
        self.engine.invalidate()
        return cfg

    def set_zone(self, key, zone, patch):
        d = self.backend.get(key)
        if not d:
            return False
        cfg = self.store.device_cfg(d)
        cfg["zones"][str(zone)].update(patch)
        self.store.save()
        self.engine.invalidate()
        return cfg

    def resize_zone(self, key, zone, count):
        ok = self.backend.resize_zone(key, int(zone), int(count))
        d = self.backend.get(key)
        if d and ok:
            self.store.device_cfg(d)["zones"][str(int(zone))]["leds"] = int(count)
            self.store.save()
        self.engine.invalidate()
        return ok

    # ---------------------------------------------------------------- LED wizard
    PROBE_LEDS = 240  # enough for 6 fans x 40 LEDs; extra LEDs are simply ignored by the hardware

    def probe_start(self, key, zone):
        d = self.backend.get(key)
        if not d:
            return False
        z = d.zones[int(zone)]
        self._probe_prev = (key, int(zone), z.leds)
        if z.hi and z.leds < self.PROBE_LEDS:
            self.backend.resize_zone(key, int(zone), min(z.hi, self.PROBE_LEDS))
        self.engine.probe = {"key": key, "zone": int(zone), "mode": "all", "k": 1}
        return {"leds": self.backend.get(key).zones[int(zone)].leds}

    def probe_set(self, mode, k=1):
        if self.engine.probe:
            self.engine.probe = {**self.engine.probe, "mode": mode, "k": int(k)}
        return True

    def probe_finish(self, key, zone, total, fans):
        self.engine.probe = None
        zone, total, fans = int(zone), int(total), int(fans)
        self.backend.resize_zone(key, zone, total)
        d = self.backend.get(key)
        cfg = self.store.device_cfg(d)
        zc = cfg["zones"][str(zone)]
        zc["leds"] = total  # remembered so it can be restored after a reboot
        if fans > 0 and total > 1:
            zc.update({"layout": "fans", "fans": fans, "enabled": True})
        else:
            zc.update({"layout": "strip", "fans": 1})
        self.store.save()
        self.engine.invalidate()
        return cfg

    def probe_cancel(self):
        self.engine.probe = None
        prev = getattr(self, "_probe_prev", None)
        if prev:
            self.backend.resize_zone(*prev)
        self.engine.invalidate()
        return True

    def identify(self, key, zone=None):
        self.engine.do_identify(key, zone)
        return True

    # ---------------------------------------------------------------- schedule
    def set_schedule(self, entries):
        self.store.schedule = entries
        self.store.save()
        return entries

    # ---------------------------------------------------------------- OpenRGB
    def openrgb_reconnect(self):
        threading.Thread(target=self.backend.reconnect, daemon=True).start()
        return True

    def openrgb_rescan(self):
        self.backend.rescan()
        return True

    def openrgb_launch(self):
        ok, msg = self.backend.launch_openrgb()
        if ok:
            threading.Timer(4, self.backend.reconnect).start()
        return {"ok": ok, "msg": msg}

    def openrgb_find(self):
        cands = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "OpenRGB/OpenRGB.exe",
                 Path.home() / "OpenRGB/OpenRGB.exe", Path("C:/OpenRGB/OpenRGB.exe"),
                 Path.home() / "Downloads/OpenRGB/OpenRGB.exe", Path.home() / ".prisma/OpenRGB/OpenRGB.exe"]
        dl = Path.home() / "Downloads"
        if dl.exists():
            cands += list(dl.glob("OpenRGB*/OpenRGB.exe")) + list(dl.glob("OpenRGB*/*/OpenRGB.exe"))
        for c in cands:
            if c.exists():
                self.set_settings({"openrgb_path": str(c)})
                return str(c)
        return ""

    def pick_openrgb(self):
        if not self.window:
            return ""
        import webview
        res = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("OpenRGB (OpenRGB.exe)",))
        if res:
            self.set_settings({"openrgb_path": res[0]})
            return res[0]
        return ""

    # ---------------------------------------------------------------- conflicts / system
    def conflicts(self):
        import psutil
        found = {}
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").rsplit(".", 1)[0]
            if n in CONFLICTS:
                found[n] = CONFLICTS[n]
        return [{"name": k, "label": v} for k, v in found.items()]

    def kill_conflicts(self, names):
        import psutil
        killed = []
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").rsplit(".", 1)[0]
            if n in names and n in CONFLICTS:
                try:
                    p.kill()
                    killed.append(n)
                except Exception:
                    pass
        return killed

    def set_autostart(self, on):
        if on:
            target = self.app_dir / "Prisma.vbs"
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');"
                  f"$s.TargetPath='wscript.exe';$s.Arguments='\"{target}\" --minimized';"
                  f"$s.WorkingDirectory='{self.app_dir}';$s.IconLocation='{self.app_dir / 'ui' / 'prisma.ico'}';$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000)
        elif STARTUP_LNK.exists():
            STARTUP_LNK.unlink()
        return STARTUP_LNK.exists()

    # ---------------------------------------------------------------- window
    def win(self, action):
        w = self.window
        if not w:
            return False
        if action == "min":
            w.minimize()
        elif action == "max":
            if getattr(w, "_prisma_max", False):
                w.restore()
                w._prisma_max = False
            else:
                w.maximize()
                w._prisma_max = True
        elif action == "close":
            if self.store.settings.get("tray", True) and getattr(self, "hide_cb", None):
                self.hide_cb()
            else:
                os._exit(0)
        elif action == "quit":
            os._exit(0)
        return True


def serve(api, ui_dir, port=0):
    token = secrets.token_urlsafe(16)
    ui_dir = Path(ui_dir)

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: the UI polls ~30x/s

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                path = "/index.html"
            f = (ui_dir / path.lstrip("/")).resolve()
            if not str(f).startswith(str(ui_dir.resolve())) or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if f.suffix == ".js":
                ctype = "text/javascript"
            self._send(200, f.read_bytes(), ctype)

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            if self.headers.get("X-Prisma-Token") != token:
                return self._send(403, b'{"error":"forbidden"}')
            name = self.path.split("/api/", 1)[-1]
            if name.startswith("_") or not callable(getattr(Api, name, None)):
                return self._send(404, b'{"error":"unknown"}')
            ln = int(self.headers.get("Content-Length", 0))
            try:
                args = json.loads(self.rfile.read(ln) or b"[]")
                res = getattr(api, name)(*args)
                self._send(200, json.dumps({"ok": True, "result": res}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                log.exception("api %s", name)
                self._send(500, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))

    import socket as _s
    srv = ThreadingHTTPServer((_s.gethostbyname("localhost"), port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
    return srv.server_address[1], token
