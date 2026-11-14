"""Lights on the home network: an ESP32 with a tiny web page (GET /on, /off; the page says "Light status: ON").

"Turn on the light", "lights off", "night mode" (the night light on), "is the light on?" are handled here instantly, no AI. After every
switch Batsy reads the light's real state back, so it never claims something happened that didn't.
Lights are in the config: [home] lights = {"light": "http://192.168.1.70"}.

Good night: routines.night() asks off_after_conversation(): the light goes off only once the whole good-night talk
is over (Batsy done speaking, nothing said or shown for a few seconds), never in the middle of it.
"""
import re
import threading
import time
import urllib.error
import urllib.request

THING = r"(?:the |my |room |bedroom |)(?:light|lights|lamp|room light|bedroom light)"
ON = re.compile(rf"^(?:please )?(?:(?:turn|switch|put|flip) on {THING}|(?:turn|switch|put|flip) {THING} on|{THING} on|"
                rf"night mode|(?:switch to |turn on |enable )night mode|it'?s (?:too )?dark(?: in here)?|"
                rf"i can'?t see(?: anything)?|let there be light)(?: please| now)?$")  # night mode = the night light on
OFF = re.compile(rf"^(?:please )?(?:(?:turn|switch|put|flip) off {THING}|(?:turn|switch|put|flip) {THING} off|{THING} off|"
                 rf"lights out|kill {THING})(?: please| now)?$")
ASK = re.compile(rf"^(?:is|are) {THING} (?:on|off)|{THING} status|(?:is|are) {THING} on or off$")
TOGGLE = re.compile(rf"^(?:please )?(?:toggle|flip|switch) {THING}$")


class Light:
    def __init__(self, name: str, url: str):
        self.name, self.url = name, url.rstrip("/")

    def _get(self, path: str) -> str:
        with urllib.request.urlopen(self.url + path, timeout=4) as r:
            return r.read().decode("utf-8", "replace")

    def state(self) -> bool | None:
        """True / False from the page, None if the light can't be reached."""
        try:
            page = self._get("/")
        except (urllib.error.URLError, OSError, ValueError):
            return None
        m = re.search(r"status:\s*(?:<[^>]+>)?\s*(ON|OFF)", page, re.I)
        return None if not m else m.group(1).upper() == "ON"

    def set(self, on: bool) -> bool | None:
        """Switch, then read back the real state (one retry if it didn't take)."""
        for _ in range(2):
            try:
                self._get("/on" if on else "/off")
            except (urllib.error.URLError, OSError, ValueError):
                pass
            now = self.state()
            if now == on:
                return now
            time.sleep(0.3)
        return self.state()


def lights(cfg) -> list[Light]:
    return [Light(name, url) for name, url in (cfg.get("home.lights") or {}).items()]


# how speech recognition tends to write "night mode"
NIGHT_MODE = re.compile(r"\b(?:nice|nite|knight|night|nights|nyt) (?:mode|mood|moat|morals?|mo|mold|mod|modes|more)\b")


# A light command ANYWHERE in what was heard: speech comes out messy ("Kachi turn off the light. Turn off the
# light.", "Light offs.", "Lights of Batsy. Lights off."), so whole-sentence matching isn't enough.
_L = r"(?:light|lights|lamp|lamps)"
SPOTS = [
    (re.compile(rf"\b(?:turn|switch|put|flip|shut)\s+(?:the |my |that |this )?{_L}\s+(on|off|of|offs)\b"), 1),
    (re.compile(rf"\b(?:turn|switch|put|flip|shut)\s+(on|off)\s+(?:the |my |that |this )?{_L}\b"), 1),
    (re.compile(rf"\b{_L}\s+(on|off|offs|of)\b(?!\s+(?:the|a|my|this|that|your)\b)"), 1),
    (re.compile(r"\b(night mode)\b"), 1),
    (re.compile(r"\b(lights out)\b"), 1),
]
NOT = re.compile(r"\b(don'?t|do not|never|keep|leave|stop|without|didn'?t|doesn'?t|won'?t|why|how|when|should i|can i)\b")


def spot(n: str) -> bool | None:
    """True (on) / False (off) for the LAST light command in the sentence, None if there isn't one."""
    found = []
    for rx, g in SPOTS:
        for m in rx.finditer(n):
            word = m.group(g)
            before = n[max(0, m.start() - 25):m.start()]
            if NOT.search(before):  # "don't turn off the light", "keep the lights on"
                continue
            found.append((m.start(), word in ("on", "night mode")))
    return sorted(found)[-1][1] if found else None


def handle(app, n: str) -> str | None:
    if len(n.split()) <= 8:
        n = NIGHT_MODE.sub("night mode", n)
    if ASK.match(n) or TOGGLE.match(n):
        on = off = None
    else:
        want = spot(n)
        on, off = want is True, want is False
    ask, toggle = ASK.match(n), TOGGLE.match(n)
    if not (on or off or ask or toggle):
        return None
    ls = lights(app.cfg)
    if not ls:
        return "I don't have any lights set up. Add them under [home] lights in the config."
    light = ls[0]
    if ask:
        st = light.state()
        return f"The {light.name} isn't answering." if st is None else f"The {light.name} is {'on' if st else 'off'}."
    want = (not light.state()) if toggle else bool(on)
    if want:
        app._lights_off_pending = None  # you want it on: cancel a pending good-night switch-off
    got = light.set(want)
    if got is None:
        return f"The {light.name} isn't answering. Is the ESP32 powered and on the Wi-Fi?"
    if got != want:
        return f"I asked, but the {light.name} is still {'on' if got else 'off'}."
    if "night mode" in n and want:
        return "Night mode. Light on."
    return f"{light.name.capitalize()} {'on' if want else 'off'}."


def off_when_done_speaking(app) -> None:
    """The good-night talk just ended ("Sleep well."): lights off the moment Batsy finishes saying it."""
    started = time.time()
    app._lights_off_pending = started

    def wait() -> None:
        time.sleep(0.3)  # let the goodbye reach the speaker queue
        app.speaker.wait_idle(30)
        if getattr(app, "_lights_off_pending", None) != started:  # you turned it back on meanwhile
            return
        app._lights_off_pending = None
        for light in lights(app.cfg):
            light.set(False)

    threading.Thread(target=wait, daemon=True, name="lights-off-now").start()


def off_after_conversation(app, quiet: float = 4, longest: float = 600) -> None:
    """Turn the lights off once the current conversation is really over: Batsy isn't waiting for an answer, isn't
    speaking, and nothing has been said or shown for `quiet` seconds. At most `longest` seconds from now."""
    started = time.time()
    app._lights_off_pending = started

    def wait() -> None:
        while time.time() - started < longest:
            time.sleep(2)
            if getattr(app, "_lights_off_pending", None) != started:  # superseded (e.g. "turn the light on")
                return
            busy = (app.routines.capturing or not app.speaker.idle()
                    or time.time() - max(app.overlay.last_active, getattr(app, "last_turn", 0)) < quiet)
            if not busy:
                break
        if getattr(app, "_lights_off_pending", None) != started:
            return
        app._lights_off_pending = None
        for light in lights(app.cfg):
            light.set(False)

    threading.Thread(target=wait, daemon=True, name="lights-off-later").start()
