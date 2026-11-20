"""Prisma fan service – runs elevated (scheduled task \\Prisma\\Fans) and owns the fan control loop.

Safety rules (always on, cannot be configured away):
  * a channel whose temperature sensor is missing runs at 100 %
  * any CPU/GPU temperature >= emergency (default 90 °C) -> every channel 100 % until it drops 5 °C below
  * every curve/fixed value is clamped to the channel minimum (never below the fan's stop threshold)
  * an exception inside the loop -> all channels 100 %, hardware re-opened
  * on exit every touched channel is handed back to the BIOS (SetDefault)
Prisma's UI talks to this service over a local socket (port published in ~/.prisma/run, one JSON object per line).
"""
import atexit
import json
import logging
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
from core.version import VERSION  # noqa: E402
from core import hw  # noqa: E402

DATA = Path(os.environ.get("PRISMA_HOME", Path.home() / ".prisma"))
CFG_PATH = DATA / "fans.json"
FC_CONFIGS = [Path(r"C:\Program Files (x86)\FanControl\Configurations\userConfig.json"),
              Path(r"C:\Program Files\FanControl\Configurations\userConfig.json")]

logging.basicConfig(filename=str(DATA / "fans.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prisma.fans")

PROFILES = {"silent": {"name": "Leise", "offset": -12}, "normal": {"name": "Normal", "offset": 0},
            "gaming": {"name": "Gaming", "offset": 15}, "max": {"name": "Volle Leistung", "fixed": 100}}


# ---------------------------------------------------------------------------- configuration
def _lhm_id(fc_id):
    """FanControl ids omit the Super I/O index: /lpc/it8689e/control/0 -> /lpc/it8689e/0/control/0"""
    m = re.match(r"^/lpc/([^/]+)/(control|fan)/(\d+)$", fc_id or "")
    return f"/lpc/{m.group(1)}/0/{m.group(2)}/{m.group(3)}" if m else fc_id


def import_fancontrol():
    """Build a Prisma fan config from FanControl's userConfig.json (curves, names, pairing, start/stop)."""
    src = next((p for p in FC_CONFIGS if p.exists()), None)
    if not src:
        return None
    fc = json.loads(src.read_text("utf-8-sig"))["FanControl"]
    curves = []
    for c in fc.get("FanCurves", []):
        cid = re.sub(r"\W+", "_", c["Name"].lower()).strip("_") or "kurve"
        if c.get("Points"):
            pts = [[round(float(a), 1), round(float(b), 1)] for a, b in (p.split(",") for p in c["Points"])]
            curves.append({"id": cid, "name": c["Name"], "sensor": c.get("SelectedTempSource", {}).get("Identifier"),
                           "points": pts, "hyst": c.get("HysteresisConfig", {}).get("HysteresisValueDown", 2)})
        else:
            curves.append({"id": cid, "name": c["Name"], "fixed": float(c.get("Percent", 100))})
    by_name = {c["name"]: c["id"] for c in curves}
    channels = []
    for ctl in fc.get("Controls", []):
        if ctl.get("IsHidden") or not ctl.get("Enable"):
            continue
        curve = (ctl.get("SelectedFanCurve") or {}).get("Name")
        stop = float(ctl.get("SelectedStop") or 0)
        channels.append({
            "id": _lhm_id(ctl["Identifier"]), "name": (ctl.get("NickName") or "Lüfter").strip(),
            "fan": _lhm_id((ctl.get("PairedFanSensor") or {}).get("Identifier")),
            "mode": "curve" if curve in by_name else "auto", "curve": by_name.get(curve), "fixed": 60.0,
            "min": max(20.0, stop + 4), "max": float(ctl.get("MaximumPercent", 100)),
            "profile": "pump" not in (ctl.get("NickName") or "").lower(),
        })
    return {"enabled": False, "profile": "normal", "curves": curves, "channels": channels,
            "safety": {"emergency_temp": 90}, "imported_from": str(src)}


def default_config():
    return {"enabled": False, "profile": "normal", "curves": [
        {"id": "cpu", "name": "CPU", "sensor": "/intelcpu/0/temperature/12",
         "points": [[35, 30], [55, 45], [65, 70], [75, 100]], "hyst": 2},
        {"id": "full", "name": "Fest 100 %", "fixed": 100}], "channels": [], "safety": {"emergency_temp": 90}}


def load_config():
    if CFG_PATH.exists():
        try:
            return json.loads(CFG_PATH.read_text("utf-8"))
        except Exception:
            log.exception("fans.json unreadable – using defaults")
    cfg = import_fancontrol() or default_config()
    save_config(cfg)
    return cfg


def save_config(cfg):
    tmp = CFG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), "utf-8")
    os.replace(tmp, CFG_PATH)


def curve_value(points, t):
    pts = sorted(points)
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t <= t1:
            return p0 + (p1 - p0) * (t - t0) / max(1e-6, t1 - t0)
    return pts[-1][1]


# ---------------------------------------------------------------------------- service
class FanService:
    def __init__(self):
        self.cfg = load_config()
        self.lock = threading.RLock()
        self.computer = None
        self.sensors = {}      # id -> sensor object
        self.values = {}       # id -> last reading
        self.out = {}          # channel id -> last written percent
        self.touched = set()
        self.curve_state = {}  # curve id -> (last temp used, last value) for hysteresis
        self.emergency = False
        self._indexed_at = 0.0
        self.error = ""
        self.started = time.time()
        self.running = True

    # --- hardware
    def open(self):
        hw.ensure_lib()
        self.computer = hw.load_computer()
        self._index()
        self._indexed_at = 0.0  # index again right after the first update

    def _index(self):
        self.sensors = {}
        for h in hw.walk(self.computer):
            for s in h.Sensors:
                if str(s.SensorType) in ("Temperature", "Fan", "Control"):
                    self.sensors[str(s.Identifier)] = (h.Name, s)

    def read(self):
        hw.update(self.computer)
        # Super I/O fan sensors only appear after the first update -> refresh the sensor list regularly
        if time.time() - self._indexed_at > 30:
            self._index()
            self._indexed_at = time.time()
        vals = {}
        for sid, (hname, s) in self.sensors.items():
            v = s.Value
            vals[sid] = None if v is None else float(v)
        self.values = vals

    def set_percent(self, cid, pct):
        entry = self.sensors.get(cid)
        if not entry or entry[1].Control is None:
            return
        pct = max(0.0, min(100.0, float(pct)))
        if abs(self.out.get(cid, -99) - pct) < 0.5:
            return
        entry[1].Control.SetSoftware(pct)
        self.out[cid] = pct
        self.touched.add(cid)

    def release(self, cid):
        entry = self.sensors.get(cid)
        if entry and entry[1].Control is not None:
            entry[1].Control.SetDefault()
        self.out.pop(cid, None)
        self.touched.discard(cid)

    def release_all(self):
        for cid in list(self.touched):
            try:
                self.release(cid)
            except Exception:
                pass

    def all_full(self):
        for ch in self.cfg.get("channels", []):
            try:
                self.set_percent(ch["id"], 100)
            except Exception:
                pass

    # --- control
    def _target(self, ch, curves):
        mode = ch.get("mode", "curve")
        if mode == "auto":
            return None
        prof = PROFILES.get(self.cfg.get("profile", "normal"), PROFILES["normal"])
        if mode == "fixed":
            val = float(ch.get("fixed", 60))
        else:
            cv = curves.get(ch.get("curve"))
            if cv is None:
                return 100.0
            if "fixed" in cv:
                val = float(cv["fixed"])
            else:
                t = self.values.get(cv.get("sensor"))
                if t is None:
                    return 100.0  # sensor missing -> safe
                st = self.curve_state.get(cv["id"])
                hyst = float(cv.get("hyst", 2))
                if st and t < st[0] and st[0] - t < hyst:
                    t = st[0]  # within hysteresis band: hold
                self.curve_state[cv["id"]] = (t, None)
                val = curve_value(cv["points"], t)
            if ch.get("profile", True):
                if "fixed" in prof:
                    val = prof["fixed"]
                else:
                    val += prof.get("offset", 0)
        lo, hi = float(ch.get("min", 20)), float(ch.get("max", 100))
        return max(lo, min(hi, val))

    def step(self):
        self.read()
        cfg = self.cfg
        temps = [v for k, v in self.values.items() if v is not None and "/temperature/" in k
                 and (k.startswith("/intelcpu") or k.startswith("/amdcpu") or k.startswith("/gpu"))]
        hot = max(temps) if temps else None
        em = float(cfg.get("safety", {}).get("emergency_temp", 90))
        if hot is not None and hot >= em:
            if not self.emergency:
                log.warning("EMERGENCY %.1f °C -> all fans 100 %%", hot)
            self.emergency = True
        elif self.emergency and (hot is None or hot < em - 5):
            self.emergency = False
        if not cfg.get("enabled"):
            self.release_all()
            return
        curves = {c["id"]: c for c in cfg.get("curves", [])}
        for ch in cfg.get("channels", []):
            target = 100.0 if self.emergency else self._target(ch, curves)
            if target is None:
                if ch["id"] in self.touched:
                    self.release(ch["id"])
                continue
            cur = self.out.get(ch["id"], target)
            # smooth: up fast (10 %/s), down gently (4 %/s)
            new = min(target, cur + 10) if target > cur else max(target, cur - 4)
            self.set_percent(ch["id"], new)

    def loop(self):
        while self.running:
            t0 = time.time()
            try:
                with self.lock:
                    if self.computer is None:
                        self.open()
                    self.step()
                self.error = ""
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                log.exception("control step failed – fans to 100 %")
                try:
                    self.all_full()
                except Exception:
                    pass
                try:
                    self.computer.Close()
                except Exception:
                    pass
                self.computer = None
                time.sleep(2)
            time.sleep(max(0.05, 1.0 - (time.time() - t0)))

    # --- status / commands
    def status(self):
        def name(sid):
            e = self.sensors.get(sid)
            return e[1].Name if e else sid
        temps = [{"id": k, "hw": self.sensors[k][0], "name": name(k), "value": v} for k, v in self.values.items()
                 if "/temperature/" in k and v is not None]
        fans = {k: v for k, v in self.values.items() if "/fan/" in k}
        controls = [{"id": k, "hw": self.sensors[k][0], "name": name(k), "value": v} for k, v in self.values.items()
                    if "/control/" in k]
        return {"version": VERSION, "config": self.cfg, "temps": temps, "fans": fans, "controls": controls,
                "out": self.out, "emergency": self.emergency, "error": self.error, "profiles": PROFILES,
                "uptime": round(time.time() - self.started)}

    def command(self, msg):
        cmd = msg.get("cmd")
        with self.lock:
            if cmd == "status":
                return self.status()
            if cmd == "config":
                cfg = msg["config"]
                for ch in cfg.get("channels", []):  # floor can never go below 0/above 100
                    ch["min"] = max(0.0, min(100.0, float(ch.get("min", 20))))
                self.cfg = cfg
                save_config(cfg)
                return {"ok": True}
            if cmd == "profile":
                if msg.get("name") in PROFILES:
                    self.cfg["profile"] = msg["name"]
                    save_config(self.cfg)
                return {"ok": True, "profile": self.cfg["profile"]}
            if cmd == "enable":
                self.cfg["enabled"] = bool(msg.get("on"))
                save_config(self.cfg)
                return {"ok": True}
            if cmd == "import":
                imp = import_fancontrol()
                if imp:
                    imp["enabled"] = self.cfg.get("enabled", False)
                    self.cfg = imp
                    save_config(imp)
                return {"ok": bool(imp)}
            if cmd == "shutdown":
                threading.Timer(0.2, self.shutdown).start()
                return {"ok": True}
        return {"error": "unknown"}

    def shutdown(self):
        self.running = False
        with self.lock:
            self.release_all()
        os._exit(0)


def serve(svc):
    from core import localport
    try:  # only one service at a time
        localport.connect("fans", timeout=0.5).close()
        raise OSError("already running")
    except ConnectionRefusedError:
        pass
    except OSError as e:
        if "already" in str(e):
            raise
    srv = localport.bind("fans")

    def handle(conn):
        with conn:
            f = conn.makefile("rwb")
            for line in f:
                try:
                    res = svc.command(json.loads(line))
                except Exception as e:
                    res = {"error": str(e)}
                f.write((json.dumps(res, ensure_ascii=False) + "\n").encode("utf-8"))
                f.flush()

    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def main():
    svc = FanService()
    atexit.register(svc.release_all)  # hand fans back to the BIOS on any orderly exit
    try:
        import ctypes
        handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(lambda ev: (svc.release_all(), False)[1])
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
        svc._ctrl_handler = handler
    except Exception:
        pass
    threading.Thread(target=svc.loop, daemon=True, name="fan-loop").start()
    log.info("fan service %s started", VERSION)
    try:
        serve(svc)
    except OSError:
        log.error("another fan service is already running")
        svc.running = False
        svc.release_all()


if __name__ == "__main__":
    main()
