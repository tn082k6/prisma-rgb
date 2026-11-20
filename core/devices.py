"""Hardware abstraction: OpenRGB SDK backend plus a virtual demo rig."""
import logging
import os
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("prisma.devices")

def _patch_openrgb():
    """openrgb-python drops leds_min/leds_max on Zone objects; keep them for resizable ARGB headers."""
    try:
        from openrgb import orgb
        if getattr(orgb.Zone, "_prisma_patched", False):
            return
        orig = orgb.Zone._update

        def _upd(self, data):
            orig(self, data)
            self.leds_min = getattr(data, "leds_min", None)
            self.leds_max = getattr(data, "leds_max", None)
        orgb.Zone._update = _upd
        orgb.Zone._prisma_patched = True
    except Exception as e:  # noqa
        log.warning("patch failed: %s", e)


_patch_openrgb()

TYPE_NAMES = {0: "motherboard", 1: "dram", 2: "gpu", 3: "cooler", 4: "ledstrip", 5: "keyboard", 6: "mouse",
              7: "mousemat", 8: "headset", 9: "headset_stand", 10: "gamepad", 11: "light", 12: "speaker",
              13: "virtual", 14: "storage", 15: "case", 16: "microphone", 17: "accessory", 18: "keypad"}


class Zone:
    def __init__(self, index, name, leds, kind="linear", lo=None, hi=None):
        self.index, self.name, self.leds, self.kind = index, name, leds, kind
        self.lo, self.hi = lo, hi  # resizable range (None = fixed)

    def to_dict(self):
        return {"index": self.index, "name": self.name, "leds": self.leds, "kind": self.kind,
                "resizable": self.lo is not None and self.lo != self.hi, "min": self.lo, "max": self.hi}


class Device:
    def __init__(self, key, name, dtype, vendor, zones, modes=(), handle=None):
        self.key, self.name, self.type, self.vendor = key, name, dtype, vendor
        self.zones, self.modes, self.handle = zones, list(modes), handle
        self.mode_applied = None

    @property
    def led_count(self):
        return sum(z.leds for z in self.zones)

    def to_dict(self):
        return {"key": self.key, "name": self.name, "type": self.type, "vendor": self.vendor,
                "zones": [z.to_dict() for z in self.zones], "modes": self.modes, "leds": self.led_count}


def guess_layout(dev, zone):
    """Default visual/geometric layout for a zone."""
    n = zone.name.lower()
    if zone.leds <= 1:
        return {"layout": "single", "fans": 1}
    if dev.type == "dram":
        return {"layout": "strip", "fans": 1}
    if any(k in n for k in ("d_led", "argb", "fan", "lüfter", "addressable", "rgb header")) and zone.leds >= 8:
        return {"layout": "fans", "fans": max(1, round(zone.leds / 12))}
    if dev.type in ("cooler",) and zone.leds >= 8:
        return {"layout": "fans", "fans": 1}
    return {"layout": "strip", "fans": 1}


# ------------------------------------------------------------------ virtual rig
def demo_devices():
    """Mirror of the user's actual hardware so the app is usable before OpenRGB runs."""
    devs = [
        Device("demo:mb", "B660 GAMING X DDR4", "motherboard", "Gigabyte",
               [Zone(0, "D_LED1 · Front-Lüfter", 36, lo=0, hi=1024), Zone(1, "D_LED2 · Heck-Lüfter", 12, lo=0, hi=1024),
                Zone(2, "Mainboard-Beleuchtung", 8)], ["Direct", "Static", "Breathing", "Color Cycle"]),
        Device("demo:ram", "Corsair Dominator Platinum RGB", "dram", "Corsair",
               [Zone(i, f"DIMM {i + 1}", 12) for i in range(4)], ["Direct", "Static", "Rainbow Wave"]),
        Device("demo:mouse", "Razer DeathAdder V2 Pro", "mouse", "Razer",
               [Zone(0, "Logo", 1, "single"), Zone(1, "Scrollrad", 1, "single")], ["Direct", "Static", "Spectrum Cycle"]),
        Device("demo:dock", "Razer Mouse Dock", "accessory", "Razer",
               [Zone(0, "Unterleuchte", 1, "single")], ["Direct", "Static", "Spectrum Cycle"]),
    ]
    return devs


# ------------------------------------------------------------------ backend
class Backend:
    """Manages the connection to OpenRGB, falling back to the virtual rig."""

    def __init__(self, store):
        self.store = store
        self.client = None
        self.devices = demo_devices()
        self.demo = True
        self.status = "OpenRGB wird gesucht …"
        self.lock = threading.RLock()
        self.version = 0  # bumps whenever the device list changes
        self._stop = False
        self._last_sent = {}
        self._last_time = {}
        self._launched = None
        threading.Thread(target=self._watch, daemon=True, name="openrgb-watch").start()

    # --- connection management
    def _watch(self):
        self._autostart_openrgb()
        while not self._stop:
            if self.client is None:
                self._try_connect()
            else:
                try:
                    if not self.client.comms.connected:
                        raise ConnectionError
                except Exception:
                    self._drop("Verbindung zu OpenRGB verloren")
            time.sleep(3)

    def _autostart_openrgb(self):
        s = self.store.settings
        if s.get("openrgb_autostart") and s.get("openrgb_path") and not self.port_open():
            self._last_launch = time.time()
            self.launch_openrgb()
            time.sleep(4)

    def port_open(self):
        import socket
        try:
            with socket.create_connection((self.store.settings.get("openrgb_host", "127.0.0.1"),
                                           int(self.store.settings.get("openrgb_port", 6742))), timeout=0.4):
                return True
        except OSError:
            return False

    def _try_connect(self):
        s = self.store.settings
        if not self.port_open():
            self.status = "OpenRGB nicht gefunden – Demo-Modus"
            # keep the service alive: relaunch it if it was closed or crashed (at most every 30 s)
            if s.get("openrgb_autostart") and s.get("openrgb_path") and time.time() - getattr(self, "_last_launch", 0) > 30:
                self._last_launch = time.time()
                ok, _ = self.launch_openrgb()
                if ok:
                    self.status = "OpenRGB wird gestartet …"
            return
        try:
            from openrgb import OpenRGBClient
            client = OpenRGBClient(s.get("openrgb_host", "127.0.0.1"), int(s.get("openrgb_port", 6742)), "Prisma")
            with self.lock:
                self.client = client
                self._load_devices()
                self._restore_sizes()
            self.status = f"Verbunden mit OpenRGB · {len(self.devices)} Geräte"
            log.info(self.status)
        except Exception as e:  # noqa
            log.warning("connect failed: %s", e)
            self.status = f"Verbindung fehlgeschlagen: {e}"
            self.client = None

    def _drop(self, why):
        with self.lock:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
            self.devices = demo_devices()
            self.demo = True
            self.version += 1
            self._last_sent.clear()
        self.status = why + " – Demo-Modus"

    def reconnect(self):
        if self.client:
            self._drop("Neu verbinden")
        self._try_connect()

    def rescan(self):
        with self.lock:
            if self.client:
                try:
                    self.client.update()
                    self._load_devices()
                except Exception as e:
                    self._drop(f"Fehler: {e}")

    def _restore_sizes(self):
        """Re-apply LED counts of addressable headers saved by Prisma (OpenRGB may forget them on restart)."""
        changed = False
        for d in self.devices:
            saved = self.store.devices.get(d.key, {}).get("zones", {})
            for z in d.zones:
                want = saved.get(str(z.index), {}).get("leds")
                if want is not None and z.hi and z.leds != want and (z.lo or 0) <= want <= z.hi:
                    try:
                        d.handle.zones[z.index].resize(int(want))
                        changed = True
                    except Exception as e:
                        log.warning("restore size %s/%s failed: %s", d.name, z.name, e)
        if changed:
            self.client.update()
            self._load_devices()

    def _load_devices(self):
        devs = []
        seen = {}
        for d in self.client.devices:
            loc = getattr(getattr(d, "metadata", None), "location", "") or ""
            base = f"{d.name}|{loc}"
            seen[base] = seen.get(base, 0) + 1
            key = base if seen[base] == 1 else f"{base}#{seen[base]}"
            zones = []
            for i, z in enumerate(d.zones):
                kind = {0: "single", 1: "linear", 2: "matrix"}.get(int(getattr(z.type, "value", z.type)), "linear")
                zones.append(Zone(i, z.name, len(z.leds), kind,
                                  getattr(z, "leds_min", None), getattr(z, "leds_max", None)))
            dtype = TYPE_NAMES.get(int(getattr(d.type, "value", d.type)), "accessory")
            vendor = getattr(getattr(d, "metadata", None), "vendor", "") or ""
            devs.append(Device(key, d.name, dtype, vendor, zones, [m.name for m in d.modes], handle=d))
        self.devices = devs
        self.demo = False
        self.version += 1
        self._last_sent.clear()

    # --- control
    def resize_zone(self, key, zone_index, count):
        with self.lock:
            dev = self.get(key)
            if not dev:
                return False
            z = dev.zones[zone_index]
            if dev.handle is not None:
                dev.handle.zones[zone_index].resize(int(count))
                self.client.update()
                self._load_devices()
            else:
                z.leds = int(count)
                self.version += 1
            return True

    def get(self, key):
        for d in self.devices:
            if d.key == key:
                return d
        return None

    def apply_mode(self, dev, mode):
        """mode: 'direct' for app control, or a hardware mode name."""
        if dev.handle is not None and mode == "direct" and self.is_wireless(dev):
            # wireless devices can fall back to onboard lighting after sleep – re-assert direct mode now and then
            if time.monotonic() - getattr(dev, "_mode_t", 0.0) > 20:
                dev.mode_applied = None
        if dev.handle is None or dev.mode_applied == mode:
            dev.mode_applied = mode
            return
        dev._mode_t = time.monotonic()
        with self.lock:
            self._apply_mode(dev, mode)

    def _apply_mode(self, dev, mode):
        try:
            if mode == "direct":
                try:
                    dev.handle.set_custom_mode()
                except Exception:
                    dev.handle.set_mode("direct")
            else:
                dev.handle.set_mode(mode)
            dev.mode_applied = mode
            self._last_sent.pop(dev.key, None)
        except Exception as e:
            log.warning("mode %s on %s failed: %s", mode, dev.name, e)
            dev.mode_applied = mode

    def send(self, dev, rgb_u8):
        """rgb_u8: (n,3) uint8 array. Only pushes when changed."""
        if dev.handle is None:
            return
        with self.lock:  # never interleave with resize/rescan on the same socket
            self._send(dev, rgb_u8)

    KEEPALIVE_S = 1.0  # resend unchanged frames so devices waking from sleep get their colours back

    @staticmethod
    def is_wireless(dev):
        n = dev.name.lower()
        return any(k in n for k in ("wireless", "lightspeed", "hyperspeed", "slipstream"))

    def max_fps(self, dev):
        cfg = self.store.devices.get(dev.key, {})
        if cfg.get("max_fps"):
            return float(cfg["max_fps"])
        if self.is_wireless(dev):
            return 25.0  # measured: radio keyboard keeps up with ~30, keep headroom
        if dev.type == "dram":
            return 40.0  # SMBus: latency starts to build above ~40 fps
        return 0.0

    def _send(self, dev, rgb_u8):
        now = time.monotonic()
        prev = self._last_sent.get(dev.key)
        last_t = self._last_time.get(dev.key, 0.0)
        same = prev is not None and prev.shape == rgb_u8.shape and np.array_equal(prev, rgb_u8)
        if same and now - last_t < self.KEEPALIVE_S:
            return
        mf = self.max_fps(dev)
        if mf and now - last_t < 1.0 / mf:
            return
        self._last_sent[dev.key] = rgb_u8.copy()
        self._last_time[dev.key] = now
        try:
            from openrgb.utils import RGBColor
            cols = [RGBColor(int(r), int(g), int(b)) for r, g, b in rgb_u8]
            h = dev.handle
            if len(cols) != len(h.leds):
                return
            if hasattr(h, "_set_device_colors"):
                h._set_device_colors(cols, True)  # per-LED update regardless of cached mode
            else:
                h.set_colors(cols, fast=True)
        except Exception as e:
            log.warning("send failed: %s", e)
            self._drop(f"Senden fehlgeschlagen: {e}")

    # --- OpenRGB process
    def launch_openrgb(self):
        s = self.store.settings
        path = s.get("openrgb_path", "")
        if not path or not os.path.exists(path):
            return False, "Pfad zu OpenRGB.exe ist nicht gesetzt oder existiert nicht."
        args = f'--server --server-port {int(s.get("openrgb_port", 6742))}'
        try:
            if s.get("openrgb_admin"):
                # preferred: elevated scheduled task -> no UAC prompt
                r = subprocess.run(["schtasks", "/Run", "/TN", r"\Prisma\OpenRGB"], capture_output=True,
                                   creationflags=0x08000000)
                if r.returncode == 0:
                    return True, "OpenRGB wird gestartet …"
                import ctypes
                r = ctypes.windll.shell32.ShellExecuteW(None, "runas", path, args, os.path.dirname(path), 0)
                if r <= 32:
                    return False, "Start als Administrator abgebrochen."
            else:
                self._launched = subprocess.Popen([path] + args.split(), cwd=os.path.dirname(path),
                                                  creationflags=0x08000000)
            return True, "OpenRGB wird gestartet …"
        except Exception as e:
            return False, str(e)

    def close(self):
        self._stop = True
        try:
            if self.client:
                self.client.disconnect()
        except Exception:
            pass
