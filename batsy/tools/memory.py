"""Memory tools. Long-term memory is an Obsidian vault of linked Markdown notes (batsy.vault); if the vault is
switched off or can't be opened it falls back to SQLite facts. Conversations always live in SQLite
(batsy.memory_store). Relevant memories are attached to each request automatically, so these tools are only for
saving, deliberate lookups and forgetting."""
import time

from batsy import vault
from batsy.memory_store import _clip, get_store


def register(reg):
    @reg.tool(
        "remember",
        "Save a lasting fact about the user, their setup, projects, people or preferences "
        "(e.g. 'Their Uno is usually on /dev/ttyACM0'). Use when they tell you something worth knowing next week, "
        "or say 'remember'. Keep it one short sentence. 'about' is the thing it's about (a project, person, device, "
        "place or topic, e.g. 'Robot project'); leave it out for facts about the user themselves. "
        "'kind' only when about is new: person, project, place, setup, preference or topic.",
        {"fact": {"type": "string"}, "about": {"type": "string"}, "kind": {"type": "string"}},
        ["fact"],
    )
    def remember(ctx, fact, about=None, kind=None):
        v = vault.get(ctx.cfg)
        if v is None:
            return f"Remembered ({get_store().add_fact(fact)})."
        return v.remember(fact, about=about, kind=kind)[1]

    @reg.tool(
        "recall",
        "Search long-term memory (notes about the user and every past conversation) when the attached context "
        "doesn't cover it.",
        {"query": {"type": "string"}},
        ["query"],
    )
    def recall(ctx, query):
        store = get_store()
        v = vault.get(ctx.cfg)
        lines = []
        if v is not None:
            for h in v.search(query, limit=5, include_daily=True):
                if h.note.kind == "daily":  # a timeline: show what was logged that day
                    facts = "; ".join(vault._plain(l.lstrip("- ")) for l in h.note.body.splitlines()
                                      if l.startswith("- "))[:400] or "(nothing logged)"
                else:
                    facts = "; ".join(vault._plain(t) for _, t in h.note.facts()[:6]) or "(no facts yet)"
                lines.append(f"note [[{h.note.title}]] ({h.note.kind}"
                             + (f", linked from {h.via}" if h.via else "") + f"): {_clip(facts, 400)}")
        else:
            lines += [f"fact ({f['id']}): {f['fact']}" for f in store.search_facts(query, 8)]
        talks = store.search_exchanges(query, 5, None)
        lines += [f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['t']))} user: {_clip(r['user'], 160)} | "
                  f"you: {_clip(r['reply'], 200)}" + (f" [did: {_clip(r['actions'], 160)}]" if r["actions"] else "")
                  for r in talks]
        return "\n".join(lines) or "Nothing found."

    @reg.tool(
        "note_read",
        "Read one note from long-term memory in full, with the notes that link to it. Use after recall when you "
        "need more detail about a project, person or device.",
        {"title": {"type": "string"}},
        ["title"],
    )
    def note_read(ctx, title):
        v = vault.get(ctx.cfg)
        if v is None:
            return "The notes vault is switched off."
        return v.read(title) or f"No note called {title}."

    @reg.tool(
        "forget",
        "Delete a remembered fact by its id (the id in brackets before each remembered fact).",
        {"id": {"type": "string"}},
        ["id"],
    )
    def forget(ctx, id):
        v = vault.get(ctx.cfg)
        if v is not None:
            where = v.forget(id)
            if where:
                return f"Forgotten (from {where})."
        return "Forgotten." if get_store().forget_fact(id) else f"No fact {id}."

    @reg.tool(
        "new_conversation",
        "Start a fresh conversation after this reply (nothing is deleted; old talk stays searchable). "
        "Use when the user says 'new conversation', 'start over', 'clear context'.",
        {},
    )
    def new_conversation(ctx):
        ctx.flags["new_conversation"] = True
        return "Fresh conversation after this reply."
