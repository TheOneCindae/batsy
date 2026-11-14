# Test report

## Static validation

- Python syntax checks: passed after the migration.
- Editable package and voice/3D/Whisper dependencies installed successfully in the user environment.
- Package metadata: no external hosted API SDK dependency remains.

## Host limitations

- Ollama is not installed or running, so local inference and hybrid routing cannot pass yet.
- Codex CLI is installed but `codex login status` reports `Not logged in`; live Codex inference cannot pass yet.
- No camera was detected. Audio input is available through the built-in ALSA device.
- Arduino and serial hardware were not available for a live upload test.

Run after installing/authenticating the AI services:

```bash
batsy doctor
batsy selftest
batsy selftest --all
```
