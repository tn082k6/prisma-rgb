"""Self-update from GitHub releases.

Checks https://api.github.com/repos/<REPO>/releases/latest, downloads the release's source zip,
copies it over the installed app folder (user data lives separately in ~/.prisma) and restarts.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from .version import VERSION, REPO

log = logging.getLogger("prisma.update")
UA = {"User-Agent": "Prisma-Updater", "Accept": "application/vnd.github+json"}


def _ver(s):
    s = (s or "").lstrip("vV").split("-")[0]
    try:
        return tuple(int(x) for x in s.split("."))
    except ValueError:
        return (0,)


class Updater:
    CHECK_EVERY_S = 6 * 3600

    def __init__(self, store, app_dir):
        self.store = store
        self.app_dir = Path(app_dir)
        self.state = {"current": VERSION, "latest": None, "available": False, "notes": "",
                      "checking": False, "installing": False, "error": "", "checked_at": 0}
        self._release = None
        threading.Thread(target=self._loop, daemon=True, name="updater").start()

    # ------------------------------------------------------------------ public
    def check(self):
        self.state.update(checking=True, error="")
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                rel = json.loads(r.read().decode("utf-8"))
            latest = rel.get("tag_name", "")
            self._release = rel
            self.state.update(latest=latest.lstrip("v"), notes=(rel.get("body") or "")[:2000],
                              available=_ver(latest) > _ver(VERSION), checked_at=time.time())
        except urllib.error.HTTPError as e:
            self.state["error"] = "Noch kein Release veröffentlicht." if e.code == 404 else f"GitHub-Fehler {e.code}"
        except Exception as e:  # offline etc.
            self.state["error"] = f"Update-Prüfung fehlgeschlagen: {e}"
        finally:
            self.state["checking"] = False
        return self.state

    def install(self):
        if self.state["installing"]:
            return self.state
        if not self.state["available"] or not self._release:
            self.check()
            if not self.state["available"]:
                return self.state
        self.state.update(installing=True, error="")
        threading.Thread(target=self._install, daemon=True, name="update-install").start()
        return self.state

    # ------------------------------------------------------------------ internals
    def _loop(self):
        time.sleep(20)
        while True:
            self.check()
            if self.state["available"] and self.store.settings.get("auto_update", True):
                log.info("auto-installing update %s", self.state["latest"])
                self.install()
                return
            time.sleep(self.CHECK_EVERY_S)

    def _install(self):
        try:
            url = self._release["zipball_url"]
            tmp = Path(tempfile.mkdtemp(prefix="prisma-update-"))
            zpath = tmp / "release.zip"
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r, open(zpath, "wb") as f:
                shutil.copyfileobj(r, f)
            with zipfile.ZipFile(zpath) as z:
                for m in z.namelist():  # refuse path traversal
                    if m.startswith("/") or ".." in Path(m).parts:
                        raise RuntimeError("Ungültiges Update-Archiv")
                z.extractall(tmp / "x")
            roots = [p for p in (tmp / "x").iterdir() if p.is_dir()]
            src = roots[0] if len(roots) == 1 else tmp / "x"
            if not (src / "app.py").exists() or not (src / "core").is_dir():
                raise RuntimeError("Update-Archiv hat nicht den erwarteten Aufbau")

            old_req = _digest(self.app_dir / "requirements.txt")
            for item in src.iterdir():
                if item.name in (".git", ".github", "__pycache__"):
                    continue
                dst = self.app_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
            if _digest(self.app_dir / "requirements.txt") != old_req:
                py = Path(sys.executable).with_name("python.exe")
                subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(self.app_dir / "requirements.txt")],
                               creationflags=0x08000000, timeout=600)
            shutil.rmtree(tmp, ignore_errors=True)
            log.info("update installed, restarting")
            self.restart()
        except Exception as e:
            log.exception("update failed")
            self.state.update(installing=False, error=f"Update fehlgeschlagen: {e}")

    def restart(self):
        vbs = self.app_dir / "Prisma.vbs"
        visible = getattr(self, "is_visible", None)
        arg = " --show" if visible and visible() else ""
        # wait until this process has exited (single-instance lock), then start again
        subprocess.Popen(f'cmd /c timeout /t 3 /nobreak >nul & wscript.exe "{vbs}"{arg}',
                         creationflags=0x08000000 | 0x00000008, close_fds=True)
        self.store.save(now=True)
        os._exit(0)


def _digest(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return ""
