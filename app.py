"""Prisma – unified RGB control. Entry point.

  python app.py              native window (+ tray icon)
  python app.py --web        headless, prints a browser URL (development)
  python app.py --minimized  start hidden in the tray
"""
import logging
import os
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from core.store import Store, DATA_DIR  # noqa: E402
from core.devices import Backend  # noqa: E402
from core.sensors import SystemSensor, AudioSensor  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.api import Api, serve  # noqa: E402

DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(DATA_DIR / "prisma.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("prisma")


def make_icon(size=256):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s = size
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    cols = [(255, 0, 160), (124, 77, 255), (0, 213, 255), (0, 255, 160)]
    for i, c in enumerate(cols):
        a0 = i * 90 - 45
        d.arc([s * .14, s * .14, s * .86, s * .86], a0, a0 + 92, fill=c + (255,), width=int(s * .13))
    blurred = glow.filter(ImageFilter.GaussianBlur(s * .05))
    img = Image.alpha_composite(img, blurred)
    img = Image.alpha_composite(img, glow)
    d = ImageDraw.Draw(img)
    d.ellipse([s * .38, s * .38, s * .62, s * .62], fill=(255, 255, 255, 235))
    return img


def ensure_icons():
    ico = APP_DIR / "ui" / "prisma.ico"
    png = APP_DIR / "ui" / "prisma.png"
    if not ico.exists() or not png.exists():
        im = make_icon(256)
        im.save(png)
        im.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico, png


def start_tray(api, store, engine, show_cb, quit_cb):
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None
    img = Image.open(APP_DIR / "ui" / "prisma.png")

    def scene_items():
        favs = [store.scene(f) for f in store.favorites]
        favs = [f for f in favs if f] or store.scenes[:8]
        for sc in favs[:12]:
            yield pystray.MenuItem(sc["name"], (lambda sid: lambda icon, item: api.set_scene(sid))(sc["id"]),
                                   checked=(lambda sid: lambda item: store.settings.get("active_scene") == sid)(sc["id"]),
                                   radio=True)

    def toggle(icon, item):
        api.set_power(not store.settings.get("power", True))

    menu = pystray.Menu(
        pystray.MenuItem("Prisma öffnen", lambda i, it: show_cb(), default=True),
        pystray.MenuItem("Szenen", pystray.Menu(scene_items)),
        pystray.MenuItem("Licht an", toggle, checked=lambda item: store.settings.get("power", True)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", lambda i, it: quit_cb()),
    )
    icon = pystray.Icon("Prisma", img, "Prisma RGB", menu)
    icon.run_detached()
    return icon


CONTROL_PORT = 47321


def single_instance(show_cb_holder):
    """Returns False if another Prisma is running (and asks it to show its window)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=0.5) as s:
            s.sendall(b"show")
        return False
    except OSError:
        pass
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", CONTROL_PORT))
    except OSError:
        return True
    srv.listen(2)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
                with c:
                    if c.recv(16) == b"show" and show_cb_holder.get("show"):
                        show_cb_holder["show"]()
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=loop, daemon=True, name="single").start()
    return True


def main():
    web = "--web" in sys.argv
    minimized = "--minimized" in sys.argv
    holder = {}
    if not web and not single_instance(holder):
        return
    ensure_icons()
    store = Store()
    backend = Backend(store)
    system, audio = SystemSensor(), AudioSensor()
    engine = Engine(store, backend, system, audio)
    api = Api(store, backend, engine, audio, APP_DIR)
    from core.updater import Updater
    api.updater = Updater(store, APP_DIR)
    port_arg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--port=")), "0")
    port, token = serve(api, APP_DIR / "ui", int(port_arg))
    url = f"http://127.0.0.1:{port}/?token={token}"
    log.info("UI at %s", url.split("?")[0])

    if web:
        print(url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    import webview
    hidden = minimized or store.settings.get("start_minimized", False)
    window = webview.create_window("Prisma", "about:blank" if hidden else url, width=1400, height=900, min_size=(1120, 720), frameless=True,
                                   easy_drag=False, background_color="#07070c", hidden=hidden)
    api.window = window
    state = {"quitting": False}

    state["blank"] = hidden

    def show():
        if state.get("blank"):
            window.load_url(url)  # reload UI that was unloaded while in the tray
            state["blank"] = False
        window.show()
        window.restore()

    def hide_to_tray():
        window.hide()
        # unload the UI so WebView2 releases its memory while only the LEDs run
        threading.Timer(0.3, lambda: (window.load_url("about:blank"), state.__setitem__("blank", True))).start()

    holder["show"] = show
    api.hide_cb = hide_to_tray

    def quit_app():
        state["quitting"] = True
        try:
            store.save(now=True)
            backend.close()
        finally:
            os._exit(0)

    def on_closing():
        if store.settings.get("tray", True) and not state["quitting"]:
            hide_to_tray()
            return False
        quit_app()

    window.events.closing += on_closing
    start_tray(api, store, engine, show, quit_app)
    webview.start(private_mode=False, storage_path=str(DATA_DIR / "webview"))
    quit_app()


if __name__ == "__main__":
    main()
