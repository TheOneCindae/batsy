# Architecture

`batsy.cli` starts the supervised application. `batsy.app.Batsy` owns the voice/UI lifecycle and tool registry.
`LocalBrain` talks to Ollama and executes local tools. Complex coding, debugging, project, document, and explicit
Codex requests use `codex_adapter`, which starts `codex exec` in the selected workspace.
Codex uses the installed CLI's ChatGPT login, `--ephemeral`, and `--approve-for-me` (which
provides Codex's workspace-write approval mode). Batsy never stores or forwards credentials. The existing `guard.py` and
`safety.py` remain the policy layer for Batsy shell tools; Codex is not run with a sandbox bypass.
