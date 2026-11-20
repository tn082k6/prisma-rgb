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


GUARD_PROCS = {"rgbfusion.exe", "gbt_dl_lib.exe", "runled.exe", "prerun.exe", "apcent.exe"}
GUARD_RUNONCE = ("PreRun", "SelLed")  # Gigabyte re-adds these on every boot


def guard_conflicts():
    """Keep Gigabyte RGB Fusion from fighting Prisma: end its processes and drop its RunOnce entries.
    Runs in the elevated fan service, so it also works for entries Gigabyte writes to HKLM."""
    done = []
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() in GUARD_PROCS:
                try:
                    p.kill()
                    done.append(p.info["name"])
                except Exception:
                    pass
    except Exception:
        pass
    try:
        import winreg
        path = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            for n in GUARD_RUNONCE:
                try:
                    winreg.DeleteValue(k, n)
                    done.append("RunOnce:" + n)
                except FileNotFoundError:
                    pass
    except Exception:
        pass
    if done:
        log.info("conflict guard removed: %s", ", ".join(done))


def boost_openrgb():
    """OpenRGB shares the low-level hardware driver (PawnIO) with us. If it holds the driver while being
    starved under full CPU load, our control loop waits for seconds (priority inversion). Run it high too."""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == "openrgb.exe" and p.nice() != psutil.HIGH_PRIORITY_CLASS:
                p.nice(psutil.HIGH_PRIORITY_CLASS)
                log.info("OpenRGB priority raised (pid %s)", p.pid)
    except Exception as e:
        log.info("could not raise OpenRGB priority: %s", e)


# ---------------------------------------------------------------------------- GPU reader process
def gpu_worker(q):
    """Separate process: reads GPU sensors every 2 s and hands them to the service (never blocks it)."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x80)
    except Exception:
        pass
    try:
        c = hw.load_computer(cpu=False, gpu=True, board=False)
    except Exception:
        log.exception("gpu worker could not open hardware")
        return
    import multiprocessing as mp
    parent = mp.parent_process()
    while True:
        if parent is not None and not parent.is_alive():
            return  # service is gone -> do not linger
        try:
            hw.update(c)
            out = []
            for h in hw.walk(c):
                for s in h.Sensors:
                    if str(s.SensorType) == "Temperature" and s.Value is not None:
                        out.append((str(s.Identifier), h.Name, s.Name, float(s.Value)))
            try:
                q.put_nowait({"ts": time.time(), "sensors": out})
            except Exception:
                pass
        except Exception:
            log.exception("gpu worker update failed")
        time.sleep(2)


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
        self.pi = {}           # channel id -> integral of the Automatik controller
        self.override = {}     # channel id -> forced percent (calibration)
        self.calib = {"running": False, "log": [], "channel": None}
        self._indexed_at = 0.0
        self.error = ""
        self.started = time.time()
        self.running = True

    # --- hardware
    def open(self):
        hw.ensure_lib()
        # only the mainboard Super I/O here (1 ms per update). The GPU driver can take 15+ s under full
        # CPU load, so it is read in a separate process (gpu_worker) and never blocks the control loop.
        self.computer = hw.load_computer(cpu=False, gpu=False)
        self._index()
        self._setup_cpu_sensor()
        self._start_gpu_worker()

    def _start_gpu_worker(self):
        import multiprocessing as mp
        if getattr(self, "gpu_proc", None) and self.gpu_proc.is_alive():
            return
        self.gpu_q = mp.Queue(maxsize=4)
        self.gpu_proc = mp.Process(target=gpu_worker, args=(self.gpu_q,), daemon=True, name="prisma-gpu")
        self.gpu_proc.start()
        self.gpu = {"ts": 0.0, "sensors": []}

    def _sname(self, sid):
        """Sensor name for board sensors and for GPU sensors delivered by the worker process."""
        e = self.sensors.get(sid)
        if e:
            return e[1].Name
        x = getattr(self, "extra", {}).get(sid)
        return x[1] if x else ""

    def _merge_gpu(self, vals):
        """Take the newest GPU reading; drop it when it is older than 30 s (curves then fall back to 100 %)."""
        import queue
        try:
            while True:
                self.gpu = self.gpu_q.get_nowait()
        except (queue.Empty, AttributeError, OSError):
            pass
        g = getattr(self, "gpu", {"ts": 0, "sensors": []})
        fresh = time.time() - g["ts"] < 30
        self.extra = {}
        for sid, hname, sname, v in g["sensors"]:
            self.extra[sid] = (hname, sname)
            vals[sid] = v if fresh else None
        if not getattr(self, "gpu_proc", None) or not self.gpu_proc.is_alive():
            self._start_gpu_worker()

    def _setup_cpu_sensor(self):
        """Pick the mainboard's CPU temperature (fast, also under full load) and move curves onto it."""
        cid = self.cfg.get("cpu_temp_id")
        if not cid or cid not in self.sensors:
            # Gigabyte boards with ITE IT86xx report the CPU (PECI) on Super I/O temperature #3 (index 2) –
            # verified on Till's B660 under load: it tracks CPU Package within 1 °C
            cid = next((k for k in self.sensors if re.match(r"^/lpc/it86\w+/0/temperature/2$", k)), None)
        self.cpu_id = cid
        if not cid:
            return
        changed = self.cfg.get("cpu_temp_id") != cid
        self.cfg["cpu_temp_id"] = cid
        for item in self.cfg.get("curves", []) + self.cfg.get("channels", []):
            s = item.get("sensor") or ""
            if s.startswith("/intelcpu") or s.startswith("/amdcpu"):
                item["sensor"] = cid
                changed = True
        if changed:
            save_config(self.cfg)
            log.info("CPU temperature source: %s", cid)
        self._indexed_at = 0.0  # index again right after the first update

    def _index(self):
        self.sensors = {}
        for h in hw.walk(self.computer):
            for s in h.Sensors:
                if str(s.SensorType) in ("Temperature", "Fan", "Control"):
                    self.sensors[str(s.Identifier)] = (h.Name, s)

    def read(self):
        self.timings = {}
        hw.update(self.computer, self.timings)
        # Super I/O fan sensors only appear after the first update -> refresh the sensor list regularly
        if time.time() - self._indexed_at > 30:
            self._index()
            self._indexed_at = time.time()
            if not getattr(self, "cpu_id", None) or self.cpu_id not in self.sensors:
                self._setup_cpu_sensor()
            boost_openrgb()
        vals = {}
        for sid, (hname, s) in self.sensors.items():
            v = s.Value
            vals[sid] = None if v is None else float(v)
        self._merge_gpu(vals)
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
    def _smart(self, ch, prof):
        """Automatik: PI controller that keeps the sensor near a target temperature as quietly as possible."""
        t = self.values.get(ch.get("sensor"))
        if t is None:
            return 100.0  # sensor missing -> safe
        if ch.get("profile", True) and "fixed" in prof:
            return 100.0
        target = float(ch.get("target", 70))
        if ch.get("profile", True):
            target += {"silent": 5, "gaming": -5}.get(self.cfg.get("profile"), 0)
        lo = float(ch.get("min", 20))
        err = t - target
        if err >= 10:
            self.pi[ch["id"]] = 60.0
            return 100.0
        i = self.pi.get(ch["id"], 0.0) + 0.25 * err          # integral (per 1 s step)
        i = max(-10.0, min(80.0, i))
        self.pi[ch["id"]] = i
        # proportional part only pushes up when we are within 8 °C of the target
        p = 4.0 * max(err + 8.0, 0.0)
        return lo + p + i

    def _target(self, ch, curves):
        mode = ch.get("mode", "curve")
        if ch["id"] in self.override:
            return self.override[ch["id"]]
        if mode == "auto":
            return None
        prof = PROFILES.get(self.cfg.get("profile", "normal"), PROFILES["normal"])
        if mode == "smart":
            val = self._smart(ch, prof)
            return max(float(ch.get("min", 20)), min(float(ch.get("max", 100)), val))
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
        if self.cfg.get("guard", True) and time.time() - getattr(self, "_guard_at", 0) > 10:
            self._guard_at = time.time()
            threading.Thread(target=guard_conflicts, daemon=True, name="guard").start()
        self.read()
        cfg = self.cfg
        # emergency only looks at the real chip temperatures (CPU package / Tctl, GPU core) – the GPU
        # "Hot Spot" normally runs 90-100 °C under load and would trigger false alarms
        crit = ("CPU Package", "Core (Tctl/Tdie)", "Tctl", "GPU Core")
        temps = [v for k, v in self.values.items() if v is not None and "/temperature/" in k
                 and (self._sname(k) in crit or k == getattr(self, "cpu_id", None))]
        if not temps:  # no known core sensor -> be conservative and use every CPU/GPU temperature
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
            if ch["id"] in self.override and not self.emergency:
                new = target  # calibration steps must be exact
            elif getattr(self, "step_ms", 0) > 2000 and target > cur:
                new = target  # the last step stalled (hardware busy) -> catch up immediately, no ramp
            else:
                # smooth: up fast (10 %/s), down gently (4 %/s)
                new = min(target, cur + 10) if target > cur else max(target, cur - 4)
            self.set_percent(ch["id"], new)

    def loop(self):
        try:  # the control thread gets the highest thread priority inside the high-priority process
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), 2)
        except Exception:
            pass
        while self.running:
            t0 = time.time()
            try:
                with self.lock:
                    if self.computer is None:
                        self.open()
                    self.step()
                self.error = ""
                self.step_ms = round((time.time() - t0) * 1000)
                if self.step_ms > 1500:
                    log.warning("slow control step: %d ms – %s", self.step_ms,
                                sorted(getattr(self, "timings", {}).items(), key=lambda x: -x[1])[:4])
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
            time.sleep(max(0.25, 1.0 - (time.time() - t0)))  # always release the lock for a moment

    # --- calibration
    def _crit_temp(self):
        crit = ("CPU Package", "Core (Tctl/Tdie)", "Tctl", "GPU Core")
        vals = [v for k, v in self.values.items() if v is not None
                and (self._sname(k) in crit or k == getattr(self, "cpu_id", None))]
        return max(vals) if vals else None

    def calibrate(self):
        """Step every fan (never the pump) down from 100 % and find where it stops -> safe minimum."""
        c = self.calib
        c.update(running=True, log=[], channel=None)
        steps = [100, 80, 60, 50, 40, 35, 30, 25, 20, 15, 10]
        try:
            for ch in list(self.cfg.get("channels", [])):
                if not ch.get("profile", True) or "pump" in ch["name"].lower() or ch.get("mode") == "auto" or not ch.get("fan"):
                    continue
                c["channel"] = ch["name"]
                rpm_at = {}
                stop = None
                for pct in steps:
                    t = self._crit_temp()
                    if t is not None and t >= 80:
                        raise RuntimeError(f"abgebrochen – Temperatur {t:.0f} °C")
                    self.override[ch["id"]] = float(pct)
                    time.sleep(8 if pct == 100 else 6)
                    rpm = self.values.get(ch["fan"]) or 0.0
                    rpm_at[pct] = round(rpm)
                    c["log"].append(f"{ch['name']}: {pct} % → {rpm:.0f} U/min")
                    if rpm < 60:
                        stop = pct
                        break
                self.override.pop(ch["id"], None)
                new_min = min(60.0, max(20.0, (stop + 10) if stop is not None else min(rpm_at) + 5))
                with self.lock:
                    for live in self.cfg["channels"]:
                        if live["id"] == ch["id"]:
                            live["min"] = new_min
                            live["calibration"] = {"rpm_max": rpm_at.get(100), "stops_at": stop, "rpm": rpm_at,
                                                   "date": time.strftime("%Y-%m-%d %H:%M")}
                    save_config(self.cfg)
                c["log"].append(f"{ch['name']}: Minimum {new_min:.0f} % (bleibt stehen bei {stop if stop is not None else '–'} %)")
            c["log"].append("Kalibrierung fertig")
        except Exception as e:
            c["log"].append(f"Kalibrierung {e}")
            log.warning("calibration: %s", e)
        finally:
            self.override.clear()
            c.update(running=False, channel=None)

    # --- status / commands
    def status(self):
        values = dict(self.values)  # snapshots – the loop may change these meanwhile
        sensors = dict(self.sensors)

        extra = dict(getattr(self, "extra", {}))

        def name(sid):
            if sid == getattr(self, "cpu_id", None):
                return "CPU"
            if sid in extra:
                return extra[sid][1]
            e = sensors.get(sid)
            return e[1].Name if e else sid

        def hwn(sid):
            if sid in extra:
                return extra[sid][0]
            e = sensors.get(sid)
            return e[0] if e else ""
        temps = [{"id": k, "hw": hwn(k), "name": name(k), "value": v} for k, v in values.items()
                 if "/temperature/" in k and v is not None]
        fans = {k: v for k, v in values.items() if "/fan/" in k}
        controls = [{"id": k, "hw": hwn(k), "name": name(k), "value": v} for k, v in values.items() if "/control/" in k]
        return {"version": VERSION, "config": json.loads(json.dumps(self.cfg)), "temps": temps, "fans": fans,
                "controls": controls, "out": dict(self.out), "emergency": self.emergency, "error": self.error,
                "profiles": PROFILES, "calibration": {**self.calib, "log": list(self.calib["log"])},
                "step_ms": getattr(self, "step_ms", None),
                "uptime": round(time.time() - self.started)}

    def command(self, msg):
        cmd = msg.get("cmd")
        if cmd == "status":  # read-only snapshot: never wait for the control loop (it can be slow under full load)
            return self.status()
        with self.lock:
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
            if cmd == "calibrate":
                if not self.calib["running"] and self.cfg.get("enabled"):
                    threading.Thread(target=self.calibrate, daemon=True, name="calibrate").start()
                return {"ok": self.cfg.get("enabled", False)}
            if cmd == "shutdown":
                threading.Timer(0.2, self.shutdown).start()
                return {"ok": True}
        return {"error": "unknown"}

    def shutdown(self):
        self.running = False
        with self.lock:
            self.release_all()
        try:
            self.gpu_proc.terminate()
        except Exception:
            pass
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
    try:  # a safety controller must not be starved by games or stress tests
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x80)  # HIGH_PRIORITY_CLASS
    except Exception:
        pass
    svc = FanService()
    try:  # run the fan task through Prisma.exe too, so Task Manager shows "Prisma" instead of "Python"
        exe = APP_DIR / "Prisma.exe"
        if exe.exists():
            import subprocess
            ps = (f"$t = Get-ScheduledTask -TaskPath '\\Prisma\\' -TaskName 'Fans'; "
                  f"if ($t.Actions[0].Execute -ne '{exe}') {{ "
                  f"Set-ScheduledTask -TaskPath '\\Prisma\\' -TaskName 'Fans' "
                  f"-Action (New-ScheduledTaskAction -Execute '{exe}' -Argument '--fans' -WorkingDirectory '{APP_DIR}') }}")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=0x08000000)
    except Exception:
        pass
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
