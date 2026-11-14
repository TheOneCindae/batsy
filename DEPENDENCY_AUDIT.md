# Dependency audit

## Host observed

Fedora 44 Workstation, Python 3.14.10, GCC 16.2, Node 22.23.1, GNOME Wayland, Intel Xeon E5-1620 v3
with 8 logical CPUs and 31 GiB RAM. The GPU is an NVIDIA Quadro K4200; no driver change was attempted.

## AI

| Component | Status | Action |
|---|---|---|
| Codex CLI 0.167.0 | PASS | Run `codex login`; current status is not logged in |
| Ollama | MISSING | Install with the official installer and enable its service |
| Ollama model | BLOCKED | Cannot inspect until Ollama is installed/running |

The Fedora package list is checked by `install_batsy_fedora.sh` before installation. Optional hardware
dependencies (camera, Arduino, Spotify, Tailscale) remain optional and are reported by `batsy doctor`.
