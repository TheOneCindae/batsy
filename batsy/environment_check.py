"""Write a reproducible Fedora/Batsy host report."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


PACKAGES = {
    "git": "git", "curl": "curl", "gcc": "gcc", "g++": "gcc-c++", "make": "make",
    "python3": "python3", "pip3": "python3-pip", "portaudio": "portaudio",
    "espeak-ng": "espeak-ng", "ffmpeg": "ffmpeg-free", "pw-play": "pipewire-utils",
    "arecord": "alsa-utils", "notify-send": "libnotify", "gnome-screenshot": "gnome-screenshot",
    "brightnessctl": "brightnessctl", "playerctl": "playerctl", "sensors": "lm_sensors",
    "ydotool": "ydotool", "v4l2-ctl": "v4l-utils", "tesseract": "tesseract",
    "arduino-cli": "arduino-cli", "ollama": "ollama", "codex": "Codex CLI",
}


def command(*args: str) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def status(name: str) -> str:
    if name == "ollama":
        return "PASS" if command("systemctl", "is-active", "ollama") == "active" else "MISSING"
    if name == "codex":
        return "PASS" if shutil.which("codex") else "MISSING"
    return "PASS" if shutil.which(name) else "MISSING"


def main() -> int:
    release = Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else ""
    gpu = command("sh", "-c", "lspci | grep -Ei 'vga|3d|display'")
    report = [
        "# Batsy Environment Report", "",
        f"- **Distribution:** {next((line.split('=', 1)[1].strip('\"') for line in release.splitlines() if line.startswith('PRETTY_NAME=')), 'unknown')}",
        f"- **Kernel:** {platform.release()}",
        f"- **CPU:** {platform.processor() or 'unknown'} ({os.cpu_count() or '?'} logical CPUs)",
        f"- **RAM:** {command('free', '-h') or 'unavailable'}",
        f"- **Desktop:** {os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')} / {os.environ.get('XDG_SESSION_TYPE', 'unknown')}",
        f"- **Python:** {platform.python_version()}",
        f"- **GPU:** {gpu or 'unavailable'}",
        f"- **Ollama models:** {command('ollama', 'list') or 'unavailable'}",
        f"- **Ollama service:** {command('systemctl', 'is-active', 'ollama') or 'inactive'}",
        f"- **Codex:** {command('codex', '--version') or 'unavailable'}",
        f"- **Codex login:** {command('codex', 'login', 'status') or 'not logged in'}", "",
        "## Dependency status", "", "| Dependency | Status | Detection |", "|---|---|---|",
    ]
    for binary, package in PACKAGES.items():
        report.append(f"| `{binary}` | **{status(binary) if binary not in ('g++',) else status('g++')}** | `{package}` |")
    report += ["", "Hardware not present is recorded as MISSING/OPTIONAL; this report does not install packages."]
    output = Path("ENVIRONMENT_REPORT.md")
    output.write_text("\n".join(report) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
