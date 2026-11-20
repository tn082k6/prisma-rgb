"""Render loop: maps scene layers onto device zones, renders effects and pushes frames."""
import logging
import threading
import time

import numpy as np

from .effects import EFFECTS, Ctx

log = logging.getLogger("prisma.engine")

SPECIFICITY = {"zone": 5, "device": 4, "fans": 3, "devtype": 2, "all": 1}


def speed_factor(s):
    return (max(1.0, float(s)) / 50.0) ** 1.6


def layer_matches(layer, dev, zone, zcfg):
    t = layer.get("target") or {"type": "all"}
    ty = t.get("type", "all")
    if ty == "all":
        return True
    if ty == "fans":
        return zcfg.get("layout") == "fans"
    if ty == "devtype":
        return dev.type == t.get("dtype")
    if ty == "device":
        return dev.key == t.get("key")
    if ty == "zone":
        return dev.key == t.get("key") and int(t.get("zone", -1)) == zone.index
    return False


class Group:
    __slots__ = ("key", "layer", "members", "n", "pos", "angle", "fan", "fans", "idx", "state", "phase", "rng")


class Runtime:
    def __init__(self, scene, devices, store, old=None):
        self.scene_id = scene["id"] if scene else None
        self.groups = []
        layers = [l for l in (scene or {}).get("layers", []) if l.get("enabled", True) and l.get("effect") in EFFECTS]
        buckets = {}
        order = []
        for dev in devices:
            cfg = store.device_cfg(dev)
            if not cfg.get("enabled", True) or cfg.get("control", "prisma") != "prisma":
                continue
            start = 0
            for z in dev.zones:
                zc = cfg["zones"][str(z.index)]
                zs = start
                start += z.leds
                if z.leds <= 0 or not zc.get("enabled", True):
                    continue
                best, score = None, 0
                for ly in layers:
                    sc = SPECIFICITY.get((ly.get("target") or {}).get("type", "all"), 0)
                    if sc >= score and layer_matches(ly, dev, z, zc):
                        best, score = ly, sc
                if best is None:
                    continue
                gk = best["id"] if best.get("span") == "group" else f'{best["id"]}/{dev.key}/{z.index}'
                if gk not in buckets:
                    buckets[gk] = (best, [])
                    order.append(gk)
                buckets[gk][1].append((dev, z, zs, zc))
        old_groups = {g.key: g for g in old.groups} if old else {}
        for gk in order:
            layer, members = buckets[gk]
            g = Group()
            g.key, g.layer, g.members = gk, layer, members
            self._geometry(g)
            prev = old_groups.get(gk)
            if prev is not None and prev.layer.get("effect") == layer.get("effect") and prev.n == g.n:
                g.state, g.phase, g.rng = prev.state, prev.phase, prev.rng
            else:
                g.state, g.phase, g.rng = {}, 0.0, np.random.default_rng()
            self.groups.append(g)

    @staticmethod
    def _geometry(g):
        pos, ang, fan = [], [], []
        total = sum(z.leds for _, z, _, _ in g.members)
        cum, fan_base = 0, 0
        for dev, z, _, zc in g.members:
            n = z.leds
            i = np.arange(n, dtype=np.float32)
            order = i[::-1] if zc.get("reverse") else i
            if zc.get("layout") == "fans" and n > 1:
                f = max(1, min(int(zc.get("fans", 1)), n))
                per = n / f
                local_fan = np.floor(i / per).astype(int)
                a = ((i - local_fan * per) + 0.5) / per
                if zc.get("reverse"):
                    a = 1 - a
                a = (a + zc.get("offset", 0) / 100.0) % 1.0
                ang.append(a)
                fan.append(local_fan + fan_base)
                fan_base += f
            else:
                a = (order + 0.5) / max(1, n)
                a = (a + zc.get("offset", 0) / 100.0) % 1.0
                ang.append(a)
                fan.append(np.full(n, fan_base, dtype=int))
                fan_base += 1
            pos.append((cum + order + 0.5) / max(1, total))
            cum += n
        g.n = total
        g.pos = np.concatenate(pos).astype(np.float32) if pos else np.zeros(0, np.float32)
        g.angle = np.concatenate(ang).astype(np.float32) if ang else np.zeros(0, np.float32)
        g.fan = np.concatenate(fan) if fan else np.zeros(0, int)
        g.fans = fan_base
        g.idx = np.arange(total)

    @property
    def wants_audio(self):
        return any(EFFECTS[g.layer["effect"]]["audio"] for g in self.groups)


class AudioView:
    __slots__ = ("bands", "level", "bass", "beat")


class Engine:
    def __init__(self, store, backend, system, audio):
        self.store, self.backend, self.system, self.audio = store, backend, system, audio
        self.lock = threading.RLock()
        self.runtime = None
        self.prev_runtime = None
        self.fade_start = 0.0
        self.dirty = True
        self.rebuild_reason = "init"
        self.last_dev_version = -1
        self.preview = {}
        self.power_level = 1.0 if store.settings.get("power", True) else 0.0
        self.identify = None
        self.probe = None  # LED wizard: {"key", "zone", "mode": "all"|"count", "k"}
        self._smooth = {}
        self.fps_real = 0.0
        self.frame_ms = 0.0
        self._last_sched = None
        self.rt0 = time.perf_counter()
        threading.Thread(target=self._loop, daemon=True, name="engine").start()

    # --- control
    def set_scene(self, sid):
        with self.lock:
            if self.store.settings.get("active_scene") != sid:
                self.store.settings["active_scene"] = sid
                self.store.save()
                self.prev_runtime = self.runtime
                self.fade_start = time.perf_counter()
            self.dirty = True

    def invalidate(self):
        with self.lock:
            self.dirty = True

    def do_identify(self, key, zone=None, seconds=2.5):
        self.identify = (key, zone, time.perf_counter() + seconds)

    # --- loop
    def _rebuild(self):
        version = self.backend.version  # read before the device list so a concurrent swap forces another rebuild
        devices = self.backend.devices
        scene = self.store.scene(self.store.settings.get("active_scene")) or (self.store.scenes[0] if self.store.scenes else None)
        old = self.runtime if (self.runtime and scene and self.runtime.scene_id == scene["id"]) else None
        self.runtime = Runtime(scene, devices, self.store, old=old)
        self.audio.want(self.runtime.wants_audio)
        self.last_dev_version = version
        self.dirty = False

    def _render_runtime(self, rt, now, dt, audio_view, bufs):
        sysv = self.system
        for g in rt.groups:
            if g.n == 0:
                continue
            ef = EFFECTS[g.layer["effect"]]
            p = g.layer["params"]
            g.phase += dt * speed_factor(p.get("speed", 50))
            c = Ctx()
            c.t, c.rt, c.dt, c.n = g.phase, now, dt, g.n
            c.idx, c.pos, c.angle, c.fan, c.fans = g.idx, g.pos, g.angle, g.fan, g.fans
            u = g.angle if p.get("flow") == "fan" else g.pos
            c.u = (1 - u) if p.get("reverse") else u
            c.p, c.state, c.audio, c.sys, c.rng = p, g.state, audio_view, sysv, g.rng
            try:
                out = ef["fn"](c)
                out = np.asarray(out, dtype=np.float32).reshape(g.n, 3)
            except Exception as e:  # never let one effect kill the loop
                log.exception("effect %s failed: %s", g.layer["effect"], e)
                out = np.zeros((g.n, 3), np.float32)
            out *= p.get("brightness", 100) / 100.0
            o = 0
            for dev, z, zs, _ in g.members:
                buf = bufs.get(dev.key)
                if buf is None or zs + z.leds > len(buf):
                    self.dirty = True  # device list changed under us – rebuild next frame
                else:
                    buf[zs:zs + z.leds] = out[o:o + z.leds]
                o += z.leds

    def _postprocess(self, arr, dev_bri):
        s = self.store.settings
        sat = s.get("saturation", 100) / 100.0
        if sat != 1.0:
            gray = arr.mean(axis=1, keepdims=True)
            arr = gray + (arr - gray) * sat
        arr = arr * np.array([s.get("wb_r", 100), s.get("wb_g", 100), s.get("wb_b", 100)], np.float32) / 100.0
        arr = np.clip(arr, 0, 1)
        g = float(s.get("gamma", 1.0))
        if abs(g - 1.0) > 1e-3:
            arr = arr ** g
        arr = arr * (s.get("master", 100) / 100.0) * dev_bri * self.power_level
        if dev_bri > 1.0:
            # boost: brighter without washing out – scale overshooting LEDs back so their hue is kept
            m = arr.max(axis=1, keepdims=True)
            arr = np.where(m > 1.0, arr / np.maximum(m, 1e-6), arr)
        return (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)

    def _schedule_tick(self):
        lt = time.localtime()
        stamp = (lt.tm_yday, lt.tm_hour, lt.tm_min)
        if stamp == self._last_sched:
            return
        self._last_sched = stamp
        hm = "%02d:%02d" % (lt.tm_hour, lt.tm_min)
        for e in self.store.schedule:
            if not e.get("enabled", True) or e.get("time") != hm or lt.tm_wday not in e.get("days", list(range(7))):
                continue
            act = e.get("action", "scene")
            if act == "scene" and self.store.scene(e.get("scene")):
                self.set_scene(e["scene"])
                self.store.settings["power"] = True
            elif act == "off":
                self.store.settings["power"] = False
            elif act == "on":
                self.store.settings["power"] = True
            elif act == "brightness":
                self.store.settings["master"] = int(e.get("value", 50))
            self.store.save()

    def _loop(self):
        last = time.perf_counter()
        acc_t, acc_n = 0.0, 0
        while True:
            fps = max(5, min(90, int(self.store.settings.get("fps", 30))))
            t0 = time.perf_counter()
            dt = min(0.1, t0 - last)
            last = t0
            try:
                self._frame(t0, dt)
            except Exception as e:
                log.exception("frame failed: %s", e)
            spent = time.perf_counter() - t0
            self.frame_ms = self.frame_ms * 0.9 + spent * 1000 * 0.1
            acc_t += dt
            acc_n += 1
            if acc_t >= 1.0:
                self.fps_real, acc_t, acc_n = acc_n / acc_t, 0.0, 0
            time.sleep(max(0.0, 1.0 / fps - spent))

    def _frame(self, now, dt):
        with self.lock:
            self._schedule_tick()
            if self.dirty or self.backend.version != self.last_dev_version:
                self._rebuild()
            rt, prev = self.runtime, self.prev_runtime
            target = 1.0 if self.store.settings.get("power", True) else 0.0
            step = dt / 0.45
            self.power_level = min(target, self.power_level + step) if target > self.power_level else max(target, self.power_level - step)
            av = AudioView()
            av.bands, av.level, av.bass = self.audio.bands, self.audio.level, self.audio.bass
            av.beat = self.audio.consume_beat()
            self.audio.gain = self.store.settings.get("audio_gain", 100) / 100.0
            devices = list(self.backend.devices)
            bufs = {d.key: np.zeros((d.led_count, 3), np.float32) for d in devices}
            self._render_runtime(rt, now - self.rt0, dt, av, bufs)
            fade_ms = max(1, self.store.settings.get("transition_ms", 700)) / 1000.0
            if prev is not None:
                k = (now - self.fade_start) / fade_ms
                if k >= 1:
                    self.prev_runtime = None
                else:
                    pb = {d.key: np.zeros((d.led_count, 3), np.float32) for d in devices}
                    self._render_runtime(prev, now - self.rt0, dt, av, pb)
                    k = k * k * (3 - 2 * k)
                    for key in bufs:
                        if pb[key].shape == bufs[key].shape:
                            bufs[key] = pb[key] * (1 - k) + bufs[key] * k
            # motion smoothing: exponential blend with the previous frame, time-based so it is fps-independent
            sm = self.store.settings.get("smoothing", 0) / 100.0
            if sm > 0:
                keep = sm ** (dt * 60.0) * 0.9
                for key, arr in bufs.items():
                    prevf = self._smooth.get(key)
                    if prevf is not None and prevf.shape == arr.shape:
                        bufs[key] = arr = prevf * keep + arr * (1 - keep)
                    self._smooth[key] = arr
            else:
                self._smooth.clear()
            ident = self.identify
            if ident and now > ident[2]:
                self.identify = ident = None
            preview = {}
            for d in devices:
                cfg = self.store.device_cfg(d)
                ctl = cfg.get("control", "prisma")
                if not cfg.get("enabled", True):
                    ctl = "off"
                arr = bufs[d.key]
                if ctl == "off":
                    arr[:] = 0
                if ident and ident[0] == d.key:
                    on = float(int((now - self.rt0) * 6) % 2)
                    if ident[1] is None:
                        arr[:] = on
                    else:
                        zs = sum(z.leds for z in d.zones[:ident[1]])
                        arr[zs:zs + d.zones[ident[1]].leds] = on
                if ctl.startswith("hw:"):
                    self.backend.apply_mode(d, ctl[3:])
                    preview[d.key] = None
                    continue
                self.backend.apply_mode(d, "direct")
                pr = self.probe
                if pr and pr["key"] == d.key and pr["zone"] < len(d.zones):
                    # LED wizard: only the probed header lights up, at full brightness
                    arr[:] = 0
                    zs = sum(z.leds for z in d.zones[:pr["zone"]])
                    n = d.zones[pr["zone"]].leds
                    if pr["mode"] == "all":
                        arr[zs:zs + n] = float(int((now - self.rt0) * 2.5) % 2)
                    else:
                        k = max(0, min(int(pr["k"]), n))
                        arr[zs:zs + k] = (0.0, 0.55, 1.0)
                        if k < n:
                            arr[zs + k] = (1.0, 0.0, 0.0)
                    u8 = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
                    self.backend.send(d, u8)
                    preview[d.key] = u8
                    continue
                u8 = self._postprocess(arr, cfg.get("brightness", 100) / 100.0)
                self.backend.send(d, u8)
                preview[d.key] = u8
            self.preview = preview
