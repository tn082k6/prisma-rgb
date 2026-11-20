"""Home Assistant integration via MQTT discovery.

Creates in Home Assistant (no YAML needed):
  light.<node>   "PC-Licht"  – on/off, brightness (master), effect = scene
  select.<node>  "PC-Szene"  – pick a scene
State is mirrored back whenever it changes in Prisma.
"""
import json
import logging
import re
import socket
import threading
import time

from .version import VERSION

log = logging.getLogger("prisma.mqtt")
FAN_PROFILES = {"silent": "Leise", "normal": "Normal", "gaming": "Gaming", "max": "Volle Leistung"}


class HomeAssistantBridge:
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.client = None
        self.status = "Aus"
        self.connected = False
        self._last_state = None
        self._last_names = None
        self._lock = threading.RLock()
        node = re.sub(r"[^a-z0-9_]", "_", socket.gethostname().lower())
        self.node = f"prisma_{node}"
        self.base = f"prisma/{node}"
        threading.Thread(target=self._watch, daemon=True, name="mqtt-watch").start()
        self.restart()

    # ------------------------------------------------------------------ lifecycle
    def restart(self):
        with self._lock:
            self._stop_client()
            s = self.store.settings
            if not s.get("mqtt_enabled"):
                self.status = "Aus"
                return
            if not s.get("mqtt_host"):
                self.status = "Bitte MQTT-Server eintragen"
                return
            try:
                import paho.mqtt.client as mqtt
            except Exception as e:
                self.status = f"MQTT-Bibliothek fehlt: {e}"
                return
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.node}_{int(time.time())}")
            if s.get("mqtt_user"):
                c.username_pw_set(s.get("mqtt_user"), s.get("mqtt_pass") or None)
            c.will_set(f"{self.base}/status", "offline", retain=True)
            c.on_connect = self._on_connect
            c.on_disconnect = self._on_disconnect
            c.on_message = self._on_message
            c.reconnect_delay_set(2, 60)
            self.client = c
            self.status = "Verbinde …"
            try:
                if s.get("mqtt_port"):
                    c.connect_async(s["mqtt_host"], int(s["mqtt_port"]), 30)
                else:
                    c.connect_async(s["mqtt_host"], keepalive=30)  # library default = MQTT standard port
                c.loop_start()
            except Exception as e:
                self.status = f"Fehler: {e}"

    def _stop_client(self):
        if self.client:
            try:
                self.client.publish(f"{self.base}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.connected = False

    # ------------------------------------------------------------------ callbacks
    def _on_connect(self, c, userdata, flags, rc, props=None):
        if getattr(rc, "is_failure", False):
            reason = str(rc)
            self.status = "Anmeldung abgelehnt – Benutzer/Passwort prüfen" if "auth" in reason.lower() or "not authorized" in reason.lower() else f"Abgelehnt: {reason}"
            self.connected = False
            return
        self.connected = True
        self.status = "Verbunden mit Home Assistant"
        c.subscribe(f"{self.base}/light/set")
        c.subscribe(f"{self.base}/scene/set")
        c.subscribe(f"{self.base}/fanprofile/set")
        c.subscribe("homeassistant/status")
        self._last_names = None
        self._last_state = None
        self._publish_discovery()
        c.publish(f"{self.base}/status", "online", retain=True)
        self._publish_state(force=True)

    def _on_disconnect(self, c, userdata, flags=None, rc=None, props=None):
        self.connected = False
        if self.store.settings.get("mqtt_enabled"):
            self.status = "Getrennt – verbinde neu …"

    def _on_message(self, c, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace")
            if msg.topic == "homeassistant/status" and payload == "online":
                self._last_names = None  # HA restarted -> announce again
                self._publish_discovery()
                self._publish_state(force=True)
                return
            if msg.topic.endswith("/fanprofile/set"):
                key = next((k for k, v in FAN_PROFILES.items() if v == payload or k == payload), None)
                if key and getattr(self, "fans", None):
                    self.fans.set_profile(key)
            elif msg.topic.endswith("/scene/set"):
                self._apply_scene_name(payload)
            elif msg.topic.endswith("/light/set"):
                cmd = json.loads(payload)
                s = self.store.settings
                if "brightness" in cmd:
                    s["master"] = max(0, min(100, int(round(float(cmd["brightness"])))))
                if cmd.get("effect"):
                    self._apply_scene_name(cmd["effect"])
                if "state" in cmd:
                    s["power"] = cmd["state"] == "ON"
                self.store.save()
            self._publish_state(force=True)
        except Exception as e:
            log.warning("bad command on %s: %s", msg.topic, e)

    def _apply_scene_name(self, name):
        for sc in self.store.scenes:
            if sc["name"] == name or sc["id"] == name:
                self.engine.set_scene(sc["id"])
                self.store.settings["power"] = True
                self.store.save()
                return True
        return False

    # ------------------------------------------------------------------ publishing
    def _device(self):
        return {"identifiers": [self.node], "name": f"Prisma ({socket.gethostname()})", "manufacturer": "Prisma",
                "model": "RGB Studio", "sw_version": VERSION}

    def _scene_names(self):
        seen, out = set(), []
        for sc in self.store.scenes:
            if sc["name"] not in seen:
                seen.add(sc["name"])
                out.append(sc["name"])
        return out

    def _publish_discovery(self):
        c = self.client
        if not c or not self.connected:
            return
        names = self._scene_names()
        if names == self._last_names:
            return
        self._last_names = names
        avail = {"availability_topic": f"{self.base}/status", "payload_available": "online", "payload_not_available": "offline"}
        light = {"name": "PC-Licht", "unique_id": f"{self.node}_light", "object_id": f"{self.node}_licht", "schema": "json",
                 "command_topic": f"{self.base}/light/set", "state_topic": f"{self.base}/light/state",
                 "brightness": True, "brightness_scale": 100, "effect": True, "effect_list": names,
                 "icon": "mdi:led-strip-variant", "device": self._device(), **avail}
        select = {"name": "PC-Szene", "unique_id": f"{self.node}_scene", "object_id": f"{self.node}_szene",
                  "command_topic": f"{self.base}/scene/set", "state_topic": f"{self.base}/scene/state",
                  "options": names, "icon": "mdi:palette", "device": self._device(), **avail}
        c.publish(f"homeassistant/light/{self.node}/licht/config", json.dumps(light, ensure_ascii=False), retain=True)
        c.publish(f"homeassistant/select/{self.node}/szene/config", json.dumps(select, ensure_ascii=False), retain=True)
        fans = {"name": "PC-Lüfterprofil", "unique_id": f"{self.node}_fanprofile", "object_id": f"{self.node}_lueftprofil",
                "command_topic": f"{self.base}/fanprofile/set", "state_topic": f"{self.base}/fanprofile/state",
                "options": list(FAN_PROFILES.values()), "icon": "mdi:fan", "device": self._device(), **avail}
        c.publish(f"homeassistant/select/{self.node}/lueftprofil/config", json.dumps(fans, ensure_ascii=False), retain=True)

    def _publish_state(self, force=False):
        c = self.client
        if not c or not self.connected:
            return
        s = self.store.settings
        sc = self.store.scene(s.get("active_scene"))
        name = sc["name"] if sc else ""
        fp = FAN_PROFILES.get(self.fans.profile() if getattr(self, "fans", None) else None, "")
        st = (bool(s.get("power", True)), int(s.get("master", 100)), name, fp)
        if st == self._last_state and not force:
            return
        self._last_state = st
        c.publish(f"{self.base}/light/state", json.dumps({"state": "ON" if st[0] else "OFF", "brightness": st[1],
                                                          "effect": name}, ensure_ascii=False), retain=True)
        c.publish(f"{self.base}/scene/state", name, retain=True)
        if fp:
            c.publish(f"{self.base}/fanprofile/state", fp, retain=True)

    def _watch(self):
        while True:
            time.sleep(0.5)
            try:
                if self.connected:
                    self._publish_discovery()
                    self._publish_state()
            except Exception as e:
                log.warning("mqtt watch: %s", e)

    def info(self):
        return {"status": self.status, "connected": self.connected, "light": f"light.{self.node}_licht",
                "select": f"select.{self.node}_szene"}

    def close(self):
        with self._lock:
            self._stop_client()
