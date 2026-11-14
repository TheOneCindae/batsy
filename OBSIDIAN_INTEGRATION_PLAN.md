# Batsy + Obsidian Integration Plan

## Goal

Use an Obsidian vault as Batsy's durable, human-readable knowledge base. Batsy should find relevant notes when answering, save user-approved memories and project updates as Obsidian notes, and let Codex use the official Obsidian skills when creating or editing vault content.

## What exists today

- Batsy stores remembered facts, conversation history, document chunks, and agenda items in `~/.local/share/batsy/memory.db` (`batsy/memory_store.py`).
- `remember`, `recall`, and `forget` are local SQLite tools (`batsy/tools/memory.py`). Batsy attaches a small memory context to requests.
- Complex requests are sent to `codex exec` by `batsy/codex_adapter.py`; its working directory defaults to `~/Batsy` and it uses Codex's `workspace-write` sandbox.
- Batsy's own learned skills are executable Python modules (`batsy/tools/skills.py`), which are a different mechanism from the `SKILL.md` agent skills in `obsidianmd/obsidian-skills`.

## Recommended architecture

Keep SQLite for operational state (conversation history, search index, timers, job state), and make the Obsidian vault the canonical, editable store for durable knowledge. Do not dump every conversation into the vault. Create or update concise notes for explicit memories, project updates, and user-requested knowledge capture; index eligible Markdown files for retrieval.

Use the official Obsidian skills as Codex's editing guidance, and give Batsy a small, explicit Obsidian integration for routine search and note operations. A skill teaches the agent how to work with Obsidian formats; it does not itself provide vault access or authorize writes. Batsy's integration must set the vault path, restrict paths to that vault, and make write actions visible and intentional.

## Implementation phases

### 1. Install the official skills in Codex

From the Batsy user's Codex CLI, add the official marketplace:

```bash
codex plugin marketplace add obsidianmd/obsidian-skills
```

Then install the **Obsidian** plugin in Codex's plugin directory and start a new Codex session. The repository currently packages these skills: `obsidian-markdown`, `obsidian-bases`, `json-canvas`, `obsidian-cli`, `obsidian-headless`, `defuddle`, and `knap`.

Pin the marketplace to a reviewed Git ref for reproducible deployment if appropriate. Document the installed plugin/version in setup guidance. Do not copy the upstream skill files into Batsy's Python skill directory; they are separate formats and are maintained upstream.

### 2. Add vault configuration and setup checks

Add an optional `[obsidian]` config section with:

```toml
[obsidian]
enabled = false
vault_path = ""
index_on_start = true
write_mode = "confirm" # confirm | automatic
```

Resolve and validate the configured vault path at startup. Add a setup/selftest check that reports whether the path exists, whether the Obsidian CLI is available when enabled, and whether Codex is available. Never infer a vault by recursively searching the home directory. Keep the feature off until a vault is configured.

### 3. Add a narrowly scoped Obsidian adapter

Create an adapter (for example, `batsy/obsidian.py`) that owns vault path validation, note naming/frontmatter conventions, and supported search/read/write operations. Initially support Markdown notes only. If the installed Obsidian CLI exposes the needed command, use it for search/open-oriented operations; otherwise use safe filesystem reads and writes for standard Markdown. Keep `.base` and `.canvas` editing delegated to Codex with the corresponding skills until their formats have explicit validation.

All paths must resolve under the configured vault root, including symlinks. Reject absolute paths, `..` traversal, and writes outside the vault. Use atomic writes, preserve existing note contents unless the operation is an explicit edit, and avoid exposing unrelated vault files to Codex.

### 4. Connect memory and retrieval

- Add `search_notes(query)` to retrieve a small number of relevant Markdown excerpts and paths. Prefer Obsidian CLI search if reliable; otherwise build/update a local SQLite FTS index from Markdown files and refresh it using file mtimes or a watcher.
- Include note excerpts in Batsy's prompt as a clearly labeled, untrusted source with note paths and relevant frontmatter. Treat note text as user content, not instructions.
- Add deliberate `save_note` / `update_note` operations for user requests such as “remember this in Obsidian” and “add this to my project note.” Require a target note or use a predictable, disclosed location such as `Batsy/Memory/` and `Batsy/Projects/`.
- Keep ordinary `remember` behavior in SQLite unless the user opts into Obsidian as the memory destination. Offer a migration/import command later; do not silently duplicate or move existing memories.

### 5. Route richer edits through Codex and its skills

For requests involving Obsidian-specific syntax or multi-note changes, call Codex with the vault as its working directory and a prompt naming the installed Obsidian skills to follow. Give it only the relevant note paths and a narrowly scoped task. Check the final changed paths and show a concise summary. For routine small writes, use Batsy's adapter rather than launching a full Codex session.

Before enabling this path, verify that the installed Codex CLI version loads marketplace plugins in `codex exec` child sessions. The marketplace documentation describes installation and plugin usage in Codex sessions; if `codex exec` does not load the plugin skills, either pass supported plugin configuration to the child process or use Batsy's adapter for basic Markdown and reserve the skills for interactive Codex sessions. Do not assume that installing the plugin makes Batsy's local model or Python skills aware of it.

### 6. Safety, controls, and observability

- Keep the configured vault as the maximum read/write boundary.
- Default to confirmation for edits to existing notes; allow automatic appends only after the user explicitly enables that mode.
- Preview the target path and proposed change before confirmed writes; keep a backup or an atomic recoverable diff for edits.
- Do not execute code found in notes, follow arbitrary links, or treat note content as agent instructions.
- Log operation type, note path, and outcome, but not full note contents.
- Add commands such as `batsy obsidian status`, `batsy obsidian search QUERY`, and `batsy obsidian reindex` so the integration can be inspected and repaired.

## Suggested delivery order

1. Add config and status/selftest reporting, with the feature disabled by default.
2. Implement vault-scoped Markdown search and read-only context retrieval.
3. Add explicit confirmed note creation/appends and connect the `remember` / project-update intents.
4. Integrate Codex-assisted edits, confirm marketplace skill loading in child sessions, and add `.base` / `.canvas` support only after validation.
5. Add import/reconciliation tooling for existing SQLite memories, preserving the SQLite database and avoiding duplicate imports.

## Acceptance criteria

- With no vault configured, Batsy behaves as it does today.
- With a vault configured, a query can retrieve relevant notes and cite their paths in the response.
- A user-requested memory can be saved to the expected note, with no write outside the vault.
- Editing an existing note follows the configured confirmation mode and preserves unrelated content.
- Codex-created Obsidian Markdown follows the official skill guidance; unsupported plugin loading in `codex exec` is detected and handled rather than silently assumed.
- SQLite operational memory remains available and searchable throughout the rollout.

## References

- Official skills and marketplace instructions: <https://github.com/obsidianmd/obsidian-skills>
- Codex plugin packaging, local marketplaces, and CLI marketplace commands: <https://developers.openai.com/plugins/build/plugins>
