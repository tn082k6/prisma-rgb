"""System and audio sensors feeding the reactive effects."""
import logging
import threading
import time

import numpy as np

log = logging.getLogger("prisma.sensors")


class SystemSensor:
    def __init__(self):
        self.cpu = 0.0
        self.ram = 0.0
        threading.Thread(target=self._loop, daemon=True, name="sys-sensor").start()

    def _loop(self):
        import psutil
        while True:
            try:
                c = psutil.cpu_percent(interval=0.5) / 100
                self.cpu = self.cpu * 0.5 + c * 0.5
                self.ram = psutil.virtual_memory().percent / 100
            except Exception:
                time.sleep(1)


class AudioSensor:
    """WASAPI loopback capture of whatever is playing; started only when an audio effect is active."""
    BANDS = 16

    def __init__(self):
        self.bands = np.zeros(self.BANDS, np.float32)
        self.level = 0.0
        self.bass = 0.0
        self.beat = False
        self.available = True
        self.error = ""
        self.gain = 1.0
        self._want = False
        self._running = False
        self._beat_flag = False
        self._peak = 1e-3
        self._bass_avg = 0.0
        self._last_beat = 0.0
        self._lock = threading.Lock()

    def want(self, on):
        if on and not self._running and self.available:
            self._running = True
            threading.Thread(target=self._run, daemon=True, name="audio").start()
        self._want = on

    def consume_beat(self):
        with self._lock:
            b, self._beat_flag = self._beat_flag, False
        return b

    def _run(self):
        try:
            import pyaudiowpatch as pa
        except Exception as e:
            self.available, self.error = False, f"Audio nicht verfügbar: {e}"
            self._running = False
            return
        p = pa.PyAudio()
        stream = None
        try:
            wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
            spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not spk.get("isLoopbackDevice"):
                for lb in p.get_loopback_device_info_generator():
                    if spk["name"] in lb["name"]:
                        spk = lb
                        break
            rate = int(spk["defaultSampleRate"])
            ch = max(1, int(spk["maxInputChannels"]))
            n = 1024
            win = np.hanning(2048).astype(np.float32)
            freqs = np.fft.rfftfreq(2048, 1 / rate)
            edges = np.geomspace(30, 16000, self.BANDS + 1)
            idx = [np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0] for i in range(self.BANDS)]
            buf = np.zeros(2048, np.float32)
            stream = p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True,
                            input_device_index=spk["index"], frames_per_buffer=n)
            self.error = ""
            while self._want:
                data = np.frombuffer(stream.read(n, exception_on_overflow=False), dtype=np.float32)
                mono = data.reshape(-1, ch).mean(axis=1)
                buf = np.concatenate([buf[len(mono):], mono])
                spec = np.abs(np.fft.rfft(buf * win))
                bands = np.array([spec[i].mean() if len(i) else 0 for i in idx], np.float32)
                bands = np.log1p(bands * 4)
                self._peak = max(self._peak * 0.995, bands.max(), 1e-3)
                norm = np.clip(bands / self._peak, 0, 1) * self.gain
                self.bands = np.maximum(norm, self.bands * 0.82)
                rms = float(np.sqrt(np.mean(mono ** 2)))
                self.level = max(min(1.0, rms * 6 * self.gain), self.level * 0.85)
                bass = float(norm[:3].mean())
                self.bass = bass
                now = time.time()
                if bass > self._bass_avg * 1.35 + 0.08 and now - self._last_beat > 0.18:
                    self._last_beat = now
                    with self._lock:
                        self._beat_flag = True
                self._bass_avg = self._bass_avg * 0.92 + bass * 0.08
        except Exception as e:
            self.error = f"Audio-Fehler: {e}"
            log.warning(self.error)
            time.sleep(2)
        finally:
            try:
                if stream:
                    stream.close()
                p.terminate()
            except Exception:
                pass
            self._running = False
            self.bands[:] = 0
            self.level = self.bass = 0.0
