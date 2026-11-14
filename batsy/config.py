"""Paths and user configuration.

Config lives in ~/.config/batsy/config.toml (every key optional). Codex and Claude
authentication are managed by their respective CLIs; no API keys are stored here.
"""
import os
import tomllib
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "batsy"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "batsy"
WORKSPACE = HOME / "Batsy"  # where Batsy keeps sketches, scripts and scratch work
SKILLS_DIR = DATA_DIR / "skills"
MODELS_DIR = DATA_DIR / "models"
JOBS_DIR = DATA_DIR / "jobs"
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"batsy-{os.getuid()}.sock"
SOURCE_DIR = Path(__file__).resolve().parent

DEFAULTS = {
    # Ollama is the default local brain; Codex CLI is used for complex work when available.
    "brain": {"engine": "local"},
    "local": {
        "url": "http://127.0.0.1:11434",
        "model": "qwen3.5:4b",     # local Ollama model; Codex CLI handles complex work when available
        "keep_alive": "2h",        # keep the model loaded so replies start instantly
        "context": 4096,
        "recent_exchanges": 4,     # short memory keeps the CPU's prompt reading fast
        "related_exchanges": 1,
        "tool_result_chars": 1500,
        "max_steps": 5,
        "auto_codex": True,       # send complex requests to Codex automatically
        "auto_claude": False,     # optional automatic Claude routing; Codex wins if both are enabled
    },
    "codex": {
        "model": "",
    },
    "claude": {"model": ""},  # Claude Code CLI uses its own model and login settings.
    "memory": {
        "recent_exchanges": 6,     # last N back-and-forths sent with each request
        "recent_minutes": 180,     # ...if they happened within this window
        "related_exchanges": 3,    # older exchanges found by local search
        "all_facts_below": 15,     # send every remembered fact while there are this few...
        "max_facts": 8,            # ...otherwise only the most relevant ones
    },
    # Long-term memory as an Obsidian vault (plain Markdown, a knowledge graph you can browse in Obsidian).
    # Point `path` at an existing vault and set `base` (e.g. "Batsy") to keep Batsy's notes in one sub-folder.
    "vault": {
        "enabled": True,
        "path": "~/Batsy/vault",
        "base": "",               # sub-folder of the vault for Batsy's notes; "" = the vault root
        "auto_link": True,        # turn mentions of existing notes into [[wikilinks]]
        "daily_log": True,        # log what Batsy learned in Daily/YYYY-MM-DD.md
        "context_notes": 3,       # related notes attached to each request
        "context_chars": 600,     # max size of each attached note
        "facts_per_note": 6,
        "neighbors": 2,           # linked/backlinked notes pulled in per strong match
    },
    "chat": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8765,
        "open_browser": True,
        "default_mode": "voice",
        "brain": "auto",
        "history_in_context": 8,
    },
    # Who you are: local, Codex and Claude all use this to pitch answers and expect your vocabulary.
    "user": {
        "name": "",               # what Batsy calls you; empty = your login name
        "about": "a college student who codes every day (Python, web, Arduino and ESP32) and does 3D design, "
                 "modelling and 3D printing (Blender, Fusion 360, OpenSCAD, slicers, PLA and PETG)",
    },
    # Your projects ("I'm doing a project on...") are matched to your GitHub repos and project folders.
    "github": {
        "user": "",               # your GitHub username; empty = detected from your clones / GITHUB_TOKEN / gh
    },
    "projects": {
        "roots": [],              # folders to look for projects in; empty = your whole home folder
    },
    # Files in and out over Tailscale (Taildrop): received files land in the inbox; "send this to my Mac".
    "files": {
        "inbox": "~/Batsy/inbox",
        "taildrop": True,             # collect files sent to this laptop with Taildrop
        "devices": {"mac": "c"},      # what "my Mac" / "my iPad" / "my phone" mean: names from `tailscale status`
        # Who may send you files, by Tailscale login -> the inbox folder they land in. You're always allowed.
        "senders": {},
        "receive": True,              # accept files from senders at http://<this laptop>:<receive_port>
        "receive_port": 8765,
        "receive_max_mb": 4096,
    },
    "spotify": {
        "client_id": "",        # from developer.spotify.com (see README)
        "device": "ipad",       # preferred Spotify Connect device (name contains this)
        "redirect_port": 8888,  # login callback http://127.0.0.1:8888/callback
    },
    # Batsy's own budget for Codex (your Pro plan). Change by voice: "set my weekly Codex limit to 5 million"
    "codex_budget": {
        "session_hours": 5,
        "session_tokens": 1_500_000,
        "weekly_tokens": 10_000_000,
        "warn_at": 0.8,
    },
    "claude_budget": {
        "session_hours": 5,
        "session_tokens": 1_500_000,
        "weekly_tokens": 10_000_000,
        "warn_at": 0.8,
    },
    "tasks": {
        "checkin_chore": "20:00",      # when to ask "did you do it?" if a chore has no time
        "checkin_project": "19:30",
        "checkin_habit": "21:00",
        "remind_minutes_before": 15,
    },
    "routines": {
        "day_starts_at": 5,   # before this hour it's still "tonight": good night at 3 AM asks about today's date
        "morning_extra": "",  # optional Codex prompt after the morning briefing, e.g. "weather in Chennai today, one line"
        "custom": {},         # presets, see README
    },
    "overlay": {
        "enabled": True,              # full-screen Batsy view while you talk to it
        "linger_seconds": 6,          # stay up this long after a spoken-only answer
        "linger_media_seconds": 30,   # ...or after showing a picture / card
        "live_words": True,           # show your words as you speak (local Vosk, no tokens)
        "screensaver_minutes": 3,     # token-usage screensaver after this long idle (0 = off)
        "qt_platform": "",            # e.g. "xcb" to force XWayland if the window won't come to the front
        "dock": True,                 # a small Batman bat in the top-right corner while the overlay is closed
        "renderer": "auto",           # auto | web (GPU, line + particles, 3D inside) | classic (plain Qt painting)
    },
    "home": {
        "lights": {},                 # name -> ESP32 address, e.g. {"light": "http://192.168.1.70"} (GET /on, /off)
    },
    "weather": {
        "city": "Chennai",            # "what's the weather" with no place named
    },
    "camera": {
        "name": "lenovo",       # matched against the V4L2 device name; first camera if none match
        "device": None,         # force /dev/videoN by number
        "snapshot_edge": 768,   # px sent to Codex per webcam look (~450 tokens)
        "screen_edge": 1280,    # px per screenshot (readable text, ~1200 tokens)
    },
    "voice": {
        "wake_words": ["batsy", "bat see", "bats see", "betsy", "batsie", "bitsy", "patsy"],
        "vosk_model": "vosk-model-small-en-us-0.15",
        "stt_engine": "auto",  # auto (Parakeet if downloaded, else whisper) | parakeet | whisper | vosk
        "parakeet_model": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        # Words Parakeet should expect (it has never heard "Batsy"); add your own project/part names here.
        # Words Parakeet rarely heard in training. Tested on mic recordings: at score 1.0 this list took tech words
        # from 11/21 to 15/21 with nothing invented in room noise; 1.5+ starts "hearing" them in silence.
        "hotwords": ["Batsy", "Codex", "Codex", "GitHub", "VS Code", "Python", "API", "JSON", "Ollama", "Llama",
                     "Arduino", "ESP32", "GPIO", "Docker", "Kubernetes", "Fedora", "npm", "Bluetooth", "repo", "commit",
                     "sketch", "serial monitor", "night mode"],
        "hotwords_score": 1.0,       # keep at 1.0; higher invents hotwords out of background noise
        # Everyday vocabulary to expect and to repair after recognition (batsy/lexicon.py): your project names are
        # always added. Drop a domain you don't use.
        "lexicon": ["developer", "3d", "student"],
        "speaker_model": "wespeaker_en_voxceleb_resnet34_LM.onnx",  # after "learn my voice", only you trigger Batsy
        "speaker_match": 0.5,
        "speaker_match_wake": 0.75,  # stricter when waking up: videos / TV scored 0.55-0.74, you 0.79+        # 0..1; raise if others still trigger it, lower if it ignores you
        "whisper_model": "base.en",  # fast on CPU; small.en is more accurate but slower
        "end_silence": 0.7,          # seconds of quiet that mean you've finished talking
        "tts_engine": "auto",  # auto | piper | espeak
        "piper_voice": "en_GB-alan-medium",
        "follow_up_seconds": 6,
        "barge_in": True,
        "kill_word": True,  # "Batsy, stop" works at any moment, even mid-action (batsy/voice/killword.py)
        "kill_grace_seconds": 5,  # after a kill, how long actions get to stop safely (in silence)
        "kill_hush_seconds": 20,  # after a kill, nothing is spoken for this long unless you ask something new
        "input_device": None,  # sounddevice device name/index; None = system default
        "chime": True,
    },
    "safety": {
        "confirm_dangerous": True,
    },
    "monitor": {
        "health_interval": 60,
        "battery_low_percent": 15,
        "cpu_temp_high_c": 90,
        "disk_free_low_gb": 5,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Config:
    def __init__(self, data: dict):
        self.data = data

    def get(self, dotted: str):
        node = self.data
        for part in dotted.split("."):
            node = node[part]
        return node

    @classmethod
    def load(cls) -> "Config":
        for d in (CONFIG_DIR, DATA_DIR, WORKSPACE, SKILLS_DIR, MODELS_DIR, JOBS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        _load_env_file(CONFIG_DIR / "env")
        data = DEFAULTS
        cfg_file = CONFIG_DIR / "config.toml"
        if cfg_file.exists():
            with open(cfg_file, "rb") as f:
                data = _merge(DEFAULTS, tomllib.load(f))
        return cls(data)
