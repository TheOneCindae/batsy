# Codex migration

1. Keep Ollama as the offline/default brain.
2. Replace the external adapter with `codex exec` and use its real non-interactive flags.
3. Keep process cancellation, timeouts, workspace selection, output capture, and usage accounting.
4. Route complex and explicit requests to Codex only when the CLI is installed and within the local budget.
5. Keep all existing safety, voice, UI, and tool modules.
6. Verify with syntax checks, package installation checks, `batsy doctor`, and self-tests.

Authentication is intentionally external: run `codex login`; no API key belongs in Batsy configuration.
