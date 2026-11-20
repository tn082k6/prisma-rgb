"""Client side of the elevated fan service (see fanservice.py)."""
import json
import socket
import subprocess
import threading
import time

from .version import VERSION
from . import localport

TASK = r"\Prisma\Fans"


class FanClient:
    def __init__(self, store):
        self.store = store
        self.last = None          # last status from the service
        self.last_ok = 0.0
        self._starting = 0.0
        threading.Thread(target=self._watch, daemon=True, name="fan-watch").start()

    def request(self, msg, timeout=2.0):
        with localport.connect("fans", timeout=timeout) as s:
            f = s.makefile("rwb")
            f.write((json.dumps(msg) + "\n").encode("utf-8"))
            f.flush()
            return json.loads(f.readline().decode("utf-8"))

    def status(self):
        try:
            st = self.request({"cmd": "status"})
            self.last, self.last_ok = st, time.time()
            return st
        except OSError:
            return None

    @property
    def running(self):
        return time.time() - self.last_ok < 6

    def start_service(self):
        """Start the elevated service through its scheduled task (no UAC prompt)."""
        if time.time() - self._starting < 15:
            return False
        self._starting = time.time()
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, creationflags=0x08000000)
        return r.returncode == 0

    def task_exists(self):
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, creationflags=0x08000000)
        return r.returncode == 0

    def _watch(self):
        """Keep the service alive and on the same version as the app."""
        time.sleep(3)
        while True:
            st = self.status()
            if st is None:
                if self.store.settings.get("fans_service", False):
                    self.start_service()
            elif st.get("version") != VERSION:
                try:
                    self.request({"cmd": "shutdown"})  # restarts with the updated code
                except OSError:
                    pass
                time.sleep(2)
                self.start_service()
            time.sleep(3)

    # convenience used by API / HA / schedule
    def profile(self):
        return (self.last or {}).get("config", {}).get("profile")

    def profiles(self):
        return (self.last or {}).get("profiles") or {}

    def set_profile(self, name):
        r = self.request({"cmd": "profile", "name": name})
        self.status()
        return r
