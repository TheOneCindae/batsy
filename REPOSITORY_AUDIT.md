# Repository audit

The project is a Python voice assistant with a local Ollama HTTP client, a tool registry, memory, voice/UI
features, and optional external coding assistance. The previous external path depended on a different vendor CLI/API.
It is now routed through the OpenAI Codex CLI, while the default conversational path remains Ollama.

Existing voice, OCR, document, image, 3D, Arduino, serial, memory, reminders, projects, GitHub, Tailscale,
Spotify, web, and learned-skill modules remain in the package. No feature module was replaced with a stub.

Validation surfaces are `batsy doctor`, `batsy selftest`, and the Fedora installer.
