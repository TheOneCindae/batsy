"""Persisted voice/chat presets and independent surface switches."""
import json
import threading


class ModeManager:
    DEFAULT = {"listen": True, "speak": True, "overlay": True, "style": "voice"}

    def __init__(self, cfg, bus=None):
        self.cfg, self.bus = cfg, bus
        self.lock = threading.RLock()
        self.state = dict(self.DEFAULT)
        if cfg.get("chat.default_mode") == "chat":
            self.state.update({"listen": False, "speak": False, "overlay": False, "style": "chat"})
        self._load()

    def _load(self):
        try:
            from batsy.config import DATA_DIR
            self.state.update(json.loads((DATA_DIR / "mode.json").read_text()))
        except (OSError, ValueError):
            pass

    def set_mode(self, mode):
        if mode not in ("voice", "chat"):
            raise ValueError("mode must be voice or chat")
        self.update({"listen": mode == "voice", "speak": mode == "voice", "overlay": mode == "voice",
                     "style": mode})

    def toggle(self):
        self.set_mode("chat" if self.state["style"] == "voice" else "voice")

    def update(self, changes):
        with self.lock:
            self.state.update(changes)
            from batsy.config import DATA_DIR
            (DATA_DIR / "mode.json").write_text(json.dumps(self.state))
            if self.bus:
                self.bus.publish("mode_changed", switches=dict(self.state))
            return dict(self.state)
