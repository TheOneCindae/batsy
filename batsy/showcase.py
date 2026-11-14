""""Batsy, show what you can do": a narrated tour on a live dashboard, for showing Batsy off.

The screen becomes a control room: a live CPU temperature dial (every core, a 90-second trace, the fans), load,
memory, network and battery, beside tiles for what Batsy can do. Batsy walks through the tiles one by one, with real
numbers from this laptop (requests answered, skills learned, files received by sender), lighting each as it speaks.
The live panel keeps running after the tour until the screen closes. Esc, a click or "~" stops it, like any answer.
"""
import logging
import re
import sqlite3
import threading
import time

import psutil

from batsy.config import DATA_DIR

log = logging.getLogger("batsy.showcase")

ASK = re.compile(r"^(?:(?:can you|could you|please|now|ok|okay|go on|come on)\s+)*(?:show|tell)(?: me| us| them| everyone| "
                 r"the (?:class|audience|room))? (?:what you can do|what you(?:'re| are) capable of|your (?:capabilities|"
                 r"skills|powers|features|tricks)|what you(?:'ve)? got|everything you can do|off)(?: please| now)?$|"
                 r"^(?:show off|demo|demo mode|do a demo|give (?:me|us) a demo|showcase|run the showcase|"
                 r"start the (?:demo|showcase|presentation))(?: please| now)?$")
# Anywhere in what was heard: speech-to-text often keeps chatter around it ("show me what you can do, I understand it").
SPOKEN = re.compile(r"\b(?:show|tell)(?: me| us| them| everyone| the (?:class|audience|room))? (?:what you can do|"
                    r"everything you can do|what you(?:'re| are) capable of)\b")
LIVE_SECONDS = 600  # the live panel stops updating after this long, even if the screen is still up
OFFLINE_ROUTES = ("command", "instant", "quick", "cache", "skill", "3d", "doc")


def handle(app, n: str) -> bool:
    if not (ASK.match(n) or SPOKEN.search(n)):
        return False
    if not app.overlay.enabled:
        app._reply("I need my screen for that. Run me with the overlay on.")
        return True
    Showcase(app).run()
    return True


def cpu_temps() -> tuple[float | None, list[float]]:
    """(package temperature, each core), from the CPU's own sensors."""
    temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        rows = temps.get(key) or []
        if rows:
            cores = [t.current for t in rows if t.label.lower().startswith(("core", "tccd", "tdie"))]
            package = next((t.current for t in rows if t.label.lower().startswith(("package", "tctl"))), None)
            return package if package is not None else max(t.current for t in rows), cores
    return None, []


def fans() -> list[int]:
    try:
        return [int(f.current) for rows in psutil.sensors_fans().values() for f in rows if f.current > 0]
    except (AttributeError, OSError):
        return []


def facts(app) -> dict:
    """Real numbers for the tiles. Anything that can't be read is left out, never made up."""
    out: dict = {}
    try:
        db = sqlite3.connect(str(DATA_DIR / "requests.db"))
        rows = dict(db.execute("SELECT route, COUNT(*) FROM requests GROUP BY route").fetchall())
        total = sum(rows.values())
        if total:
            out["requests"] = total
            out["offline_pct"] = round(100 * sum(rows.get(r, 0) for r in OFFLINE_ROUTES) / total)
        day = time.time() - 86400
        out["today"] = db.execute("SELECT COUNT(*) FROM requests WHERE t > ?", (day,)).fetchone()[0]
        db.close()
    except sqlite3.Error:
        pass
    try:
        out["skills"] = len(list((DATA_DIR / "skills").glob("*.py")))
    except OSError:
        pass
    inbox = getattr(app, "inbox", None)
    if inbox is not None:
        folders = sorted(set(inbox.senders.values()) | {inbox.mine})
        out["senders"] = {name: len(inbox._files(name)) for name in folders}
    out["brain"] = app.cfg.get("local.model")
    out["claude"] = app.cfg.get("claude.model")
    out["stt"] = getattr(getattr(app, "stt", None), "engine", "") or "speech"
    out["voiceprint"] = bool(getattr(app, "voiceprint", None) and app.voiceprint.active)
    out["temp_high"] = app.cfg.get("monitor.cpu_temp_high_c")
    return out


def _n(x: int) -> str:
    return f"{x:,}"


def tiles(f: dict, temp: float | None) -> list[dict]:
    """What's shown, and what Batsy says about each, in order."""
    senders = f.get("senders") or {}
    names = [s for s in senders]
    who = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else (names[0] if names else "each person")
    received = sum(senders.values())
    reqs, pct = f.get("requests"), f.get("offline_pct")
    temp_line = f"The CPU is at {temp:.0f} degrees right now" if temp is not None else "This panel is live"
    return [
        {"icon": "wave", "title": "Hears you, offline",
         "fact": f"Wake word + {f['stt'].title()} on this laptop",
         "say": f"I listen for my name on this laptop, and I turn speech into text with {f['stt'].title()}, "
                f"right here. Nothing leaves the room until it needs to."},
        {"icon": "print", "title": "Knows your voice",
         "fact": "Voiceprint check on every wake" if f.get("voiceprint") else "Voiceprint ready to train",
         "say": "I know whose voice is whose. A video, or someone else saying my name, won't wake me."},
        {"icon": "brain", "title": "Two brains",
         "fact": (f"{pct}% of {_n(reqs)} requests answered offline" if reqs else f"{f['brain']} + Claude"),
         "say": (f"I have two brains. Small things I answer myself, which is {pct} percent of the {_n(reqs)} "
                 f"requests so far. Hard questions go to Claude.") if reqs else
                "I have two brains: a small one on this laptop, and Claude for the hard questions."},
        {"icon": "spark", "title": "Learns your habits",
         "fact": f"{f.get('skills', 0)} skills written for itself",
         "say": f"When you ask for the same thing again and again, I write myself the code for it. "
                f"I've learned {f.get('skills', 0)} skills that way, and they run instantly."},
        {"icon": "eye", "title": "Sees and shows",
         "fact": "Camera vision · pictures · 3D models",
         "say": "I can look through the camera and tell you what I see, pull up pictures and diagrams, "
                "and open 3D models you can turn and measure by voice."},
        {"icon": "files", "title": "Files, by who sent them",
         "fact": " · ".join(f"{k} {v}" for k, v in senders.items()) or "Over Tailscale",
         "say": f"Files arrive over Tailscale, sorted by who sent them: {who}. Tailscale proves who's sending, "
                f"so nobody can pretend to be someone else." + (f" {received} files so far." if received else "")},
        {"icon": "home", "title": "Runs the room",
         "fact": "Spotify · lights · apps · terminals",
         "say": "I play music on Spotify, switch the lights, open apps and terminals, and run system updates "
                "where you can watch them."},
        {"icon": "gauge", "title": "Watches the laptop",
         "fact": f"Warns at {f.get('temp_high', 95)}°C · live",
         "say": f"And I keep an eye on the laptop itself. {temp_line}, and I'll warn you before it gets too hot."},
    ]


class Showcase:
    def __init__(self, app):
        self.app, self.overlay = app, app.overlay
        self.t0 = time.time()

    def stopped(self) -> bool:
        if self.overlay.muted or getattr(self.app, "conversation_ended", 0) > self.t0:
            log.info("showcase stopped early (muted=%s)", self.overlay.muted)
            return True
        return False

    def run(self) -> None:
        temp, _ = cpu_temps()
        f = facts(self.app)
        shown = tiles(f, temp)
        self.overlay.has_media = True
        self.overlay.send("showcase", on=True, tiles=[{k: t[k] for k in ("icon", "title", "fact")} for t in shown],
                          high=f.get("temp_high") or 95, cores=psutil.cpu_count() or 1)
        threading.Thread(target=self._live, daemon=True, name="showcase").start()
        app = self.app
        app.speaker.say("Alright. Let me show you what I can do.")
        app.speaker.wait_idle(15)
        for i, t in enumerate(shown):
            if self.stopped():
                return
            self.overlay.send("showcase_focus", i=i)
            app.last_said = t["say"]
            self.overlay.say(t["say"])
            app.speaker.say(t["say"])
            app.speaker.wait_idle(40)
            self.overlay.send("showcase_seen", i=i)
        if self.stopped():
            return
        self.overlay.send("showcase_focus", i=-1)
        app._reply("That's me. Go on, ask me anything.")

    def _live(self) -> None:
        """Push sensor readings every second while the dashboard is up."""
        psutil.cpu_percent(percpu=True)  # the first reading is always 0: prime it
        net0, t_prev = psutil.net_io_counters(), time.time()
        while self.overlay.showcase_on and not self.stopped() and time.time() - self.t0 < LIVE_SECONDS:
            time.sleep(1)
            try:
                temp, cores = cpu_temps()
                net, now = psutil.net_io_counters(), time.time()
                dt = max(0.001, now - t_prev)
                down, up = (net.bytes_recv - net0.bytes_recv) / dt, (net.bytes_sent - net0.bytes_sent) / dt
                net0, t_prev = net, now
                bat = psutil.sensors_battery()
                self.overlay.send("showcase_stats", temp=temp, cores=cores, load=psutil.cpu_percent(percpu=True),
                                  ram=psutil.virtual_memory().percent, down=down, up=up, fans=fans(),
                                  battery=None if bat is None else round(bat.percent),
                                  plugged=None if bat is None else bool(bat.power_plugged),
                                  uptime=int(now - psutil.boot_time()))
            except Exception:
                log.exception("showcase stats")
        log.info("showcase live panel stopped")
