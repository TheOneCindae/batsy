#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -r /etc/fedora-release ]]; then
  echo "This installer supports Fedora only." >&2
  exit 1
fi

python3 -m batsy.environment_check
missing=()
for package in git curl wget unzip tar gcc gcc-c++ make cmake python3 python3-pip python3-devel \
  portaudio portaudio-devel espeak-ng ffmpeg-free pipewire-utils alsa-utils libnotify \
  gnome-screenshot brightnessctl playerctl lm_sensors ydotool v4l-utils tesseract \
  tesseract-langpack-eng qt6-qtwayland xcb-util-cursor libxkbcommon-x11; do
  rpm -q "$package" >/dev/null 2>&1 || missing+=("$package")
done
if ((${#missing[@]})); then
  sudo dnf install -y "${missing[@]}"
fi

python3 -m venv --clear .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[voice,threed,whisper]'
mkdir -p "$HOME/.config/autostart" "$HOME/.local/bin"
install -m 755 batsy_environment_audit.sh "$HOME/.local/bin/batsy-environment-audit"
install -m 644 packaging/batsy.desktop "$HOME/.config/autostart/batsy.desktop"
if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI is missing. Install it with the official installer, then run: codex login" >&2
fi
if ! systemctl is-active --quiet ollama; then
  echo "Ollama is not active. Install/start it separately to preserve existing models." >&2
fi
.venv/bin/batsy selftest || true
echo "Batsy installed. Run: .venv/bin/batsy doctor"
