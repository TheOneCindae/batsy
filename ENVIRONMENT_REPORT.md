# Batsy Environment Report

- **Distribution:** Fedora Linux 44 (Workstation Edition)
- **Kernel:** 7.2.15-200.fc44.x86_64
- **CPU:** unknown (8 logical CPUs)
- **RAM:** total        used        free      shared  buff/cache   available
Mem:            31Gi        10Gi       2.8Gi       399Mi        18Gi        20Gi
Swap:          8.0Gi          0B       8.0Gi
- **Desktop:** GNOME / wayland
- **Python:** 3.14.10
- **GPU:** 03:00.0 VGA compatible controller: NVIDIA Corporation GK104GL [Quadro K4200] (rev a1)
- **Ollama models:** unavailable
- **Ollama service:** inactive
- **Codex:** codex-cli 0.167.0
- **Codex login:** not logged in

## Dependency status

| Dependency | Status | Detection |
|---|---|---|
| `git` | **PASS** | `git` |
| `curl` | **PASS** | `curl` |
| `gcc` | **PASS** | `gcc` |
| `g++` | **PASS** | `gcc-c++` |
| `make` | **PASS** | `make` |
| `python3` | **PASS** | `python3` |
| `pip3` | **PASS** | `python3-pip` |
| `portaudio` | **MISSING** | `portaudio` |
| `espeak-ng` | **PASS** | `espeak-ng` |
| `ffmpeg` | **PASS** | `ffmpeg-free` |
| `pw-play` | **PASS** | `pipewire-utils` |
| `arecord` | **PASS** | `alsa-utils` |
| `notify-send` | **PASS** | `libnotify` |
| `gnome-screenshot` | **MISSING** | `gnome-screenshot` |
| `brightnessctl` | **PASS** | `brightnessctl` |
| `playerctl` | **PASS** | `playerctl` |
| `sensors` | **PASS** | `lm_sensors` |
| `ydotool` | **MISSING** | `ydotool` |
| `v4l2-ctl` | **MISSING** | `v4l-utils` |
| `tesseract` | **MISSING** | `tesseract` |
| `arduino-cli` | **MISSING** | `arduino-cli` |
| `ollama` | **MISSING** | `ollama` |
| `codex` | **PASS** | `Codex CLI` |

Hardware not present is recorded as MISSING/OPTIONAL; this report does not install packages.
