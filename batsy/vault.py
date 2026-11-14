"""Long-term memory as an Obsidian vault: a knowledge graph of plain Markdown notes.

Why files, not the Obsidian app: Batsy reads and writes the notes directly, so memory works with Obsidian closed
(and for the small local model, with no Claude call). Obsidian is simply the best way for *you* to browse, edit and
graph-view the same files. Claude Code hand-offs get the official obsidian-skills (obsidian-markdown, obsidian-cli,
...) installed in the vault, and write through `batsy vault ...` so every note keeps the same shape.

Layout (under `[vault] path`, optionally inside a `base` sub-folder so an existing vault isn't cluttered):

    Me.md               the hub: who you are, a "Facts" list, and a map linking to every other note
    People/ Projects/ Places/ Setup/ Preferences/ Topics/     one note per thing, `type:` in the frontmatter
    Daily/YYYY-MM-DD.md a timeline of what Batsy learned each day (links to the notes it touched)
    CLAUDE.md           the rules Claude Code follows in this vault
    .claude/skills/     obsidian-skills

A fact is one bullet under "## Facts" ending in an Obsidian block id: `- Uses an ESP32 for it ^a1b2c`.
The id is what "forget" uses. Mentions of other notes are turned into [[wikilinks]] automatically, which is what
builds the graph. Retrieval scores notes, then follows links and backlinks one hop to pull in related notes.
"""
from __future__ import annotations

import getpass
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from batsy.memory_store import _clip, _terms

log = logging.getLogger("batsy.vault")

FOLDERS = {"person": "People", "project": "Projects", "place": "Places", "setup": "Setup",
           "preference": "Preferences", "topic": "Topics", "daily": "Daily"}
KIND_WORDS = {"people": "person", "friend": "person", "family": "person", "contact": "person",
              "projects": "project", "places": "place", "location": "place", "device": "setup",
              "hardware": "setup", "tool": "setup", "tools": "setup", "pref": "preference",
              "preferences": "preference", "like": "preference", "habit": "preference", "topics": "topic"}
SKIP_DIRS = {".obsidian", ".claude", ".trash", ".git", "node_modules"}
INSTRUCTION_FILES = {"CLAUDE.md", "AGENTS.md"}  # instructions for agents, not memories
SKILLS_REPO = "https://github.com/obsidianmd/obsidian-skills.git"
ME = "Me"

FACT_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s+\^(?P<id>[A-Za-z0-9-]{3,12})\s*$")
ID_RE = re.compile(r"^[A-Za-z0-9-]{3,12}$")
WIKILINK = re.compile(r"!?\[\[(?P<target>[^\]|#^]+)(?:[#^][^\]|]*)?(?:\|(?P<alias>[^\]]*))?\]\]")
_PROTECTED = re.compile(r"(!?\[\[.*?\]\]|`[^`]*`|\[[^\]]*\]\([^)]*\)|https?://\S+)")


def clean_title(title: str) -> str:
    """A safe note title: no path separators or characters Obsidian rejects, so it can never escape the vault."""
    t = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", title or "")
    t = re.sub(r"\s+", " ", t).strip(" .")
    if not t:
        raise ValueError("empty note title")
    return t[:80]


def _kind(word: str | None) -> str:
    w = (word or "").strip().lower()
    w = KIND_WORDS.get(w, w)
    return w if w in FOLDERS and w != "daily" else "topic"


def _plain(text: str) -> str:
    return WIKILINK.sub(lambda m: m.group("alias") or m.group("target"), text)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", _plain(text).lower()))


# --- Markdown helpers ----------------------------------------------------------------------------------------
def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[4:end], text[end + 4:].lstrip("\n")
    return "", text


def _parse_props(fm: str) -> dict:
    props: dict = {}
    key = None
    for line in fm.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                props[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            elif val == "":
                props[key] = []
            else:
                props[key] = val.strip("'\"")
        elif key and re.match(r"^\s*-\s+", line) and isinstance(props.get(key), list):
            props[key].append(re.sub(r"^\s*-\s+", "", line).strip().strip("'\""))
    return props


def _insert_in_section(text: str, heading: str, line: str, level: int = 2) -> str:
    """Add `line` at the end of the `## heading` section, creating the section if it's missing."""
    lines = text.split("\n")
    hdr = "#" * level + " " + heading
    stop = re.compile(r"^#{1,%d}\s" % level)
    for i, l in enumerate(lines):
        if l.strip().lower() == hdr.lower():
            j = i + 1
            while j < len(lines) and not stop.match(lines[j]):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, line)
            return "\n".join(lines)
    return text.rstrip("\n") + f"\n\n{hdr}\n{line}\n"


def _touch_updated(text: str) -> str:
    """Refresh `updated:` in existing frontmatter, leaving everything else the user may have edited alone."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    fm, rest = text[4:end], text[end:]
    today = time.strftime("%Y-%m-%d")
    if re.search(r"^updated:.*$", fm, re.M):
        fm = re.sub(r"^updated:.*$", f"updated: {today}", fm, flags=re.M)
    else:
        fm += f"\nupdated: {today}"
    return "---\n" + fm + rest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Note:
    title: str
    path: Path
    kind: str
    aliases: list[str]
    tags: list[str]
    body: str
    links: list[str]  # lower-cased titles this note links to

    def facts(self) -> list[tuple[str, str]]:
        """[(id, text)] for the bullets under '## Facts'."""
        out, in_facts = [], False
        for line in self.body.splitlines():
            if re.match(r"^##\s", line):
                in_facts = line.strip().lower() == "## facts"
            elif in_facts:
                m = FACT_RE.match(line)
                if m:
                    out.append((m.group("id"), m.group("text")))
        return out

    def has_content(self) -> bool:
        """Facts or real prose: not just headings and a list of links."""
        return any(l.strip() and not l.lstrip().startswith("#")
                   and not re.fullmatch(r"\s*[-*]\s*\[\[[^\]]+\]\]\s*", l) for l in self.body.splitlines())


@dataclass
class Hit:
    note: Note
    score: float
    via: str | None = None


class Vault:
    def __init__(self, root, settings: dict | None = None, user: str = ""):
        s = {"base": "", "auto_link": True, "daily_log": True, "context_notes": 3, "context_chars": 600,
             "facts_per_note": 6, "neighbors": 2}
        s.update(settings or {})
        self.s = s
        self.root = Path(root).expanduser()
        self.base = self.root / s["base"] if s["base"] else self.root
        self.user = user or getpass.getuser()
        self.me_path = self.base / "Me.md"
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[int, int, Note | None]] = {}

    # --- reading --------------------------------------------------------------------------------------------
    def _md_files(self):
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if f.endswith(".md") and f not in INSTRUCTION_FILES:
                    yield Path(dirpath) / f

    def _load(self, p: Path) -> Note | None:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            return None
        fm, body = _split_frontmatter(text)
        props = _parse_props(fm)
        kind = props.get("type") if isinstance(props.get("type"), str) else None
        if not kind:
            folder = p.parent.name
            kind = next((k for k, v in FOLDERS.items() if v == folder), "topic")
        links = []
        for m in WIKILINK.finditer(body):
            t = m.group("target").strip()
            t = t[:-3] if t.endswith(".md") else t
            links.append(t.rsplit("/", 1)[-1].lower())
        aliases = props.get("aliases") if isinstance(props.get("aliases"), list) else []
        tags = props.get("tags") if isinstance(props.get("tags"), list) else []
        return Note(p.stem, p, kind, aliases, tags, body, list(dict.fromkeys(links)))

    def notes(self) -> dict[str, Note]:
        """Every note in the vault, keyed by lower-cased title. Re-reads only files that changed."""
        out: dict[str, Note] = {}
        seen = set()
        with self.lock:
            if not self.root.exists():
                return out
            for p in self._md_files():
                try:
                    st = p.stat()
                except OSError:
                    continue
                key = str(p)
                seen.add(key)
                c = self._cache.get(key)
                if c and c[0] == st.st_mtime_ns and c[1] == st.st_size:
                    note = c[2]
                else:
                    note = self._load(p)
                    self._cache[key] = (st.st_mtime_ns, st.st_size, note)
                if note:
                    out.setdefault(note.title.lower(), note)
            for k in set(self._cache) - seen:
                del self._cache[k]
        return out

    def resolve(self, name: str) -> Note | None:
        """A note by title (case-insensitive), or by alias."""
        n = name.strip().lower()
        notes = self.notes()
        if n in notes:
            return notes[n]
        return next((x for x in notes.values() if any(a.lower() == n for a in x.aliases)), None)

    def me(self) -> Note | None:
        return self._load(self.me_path) if self.me_path.exists() else None

    def backlinks(self, note: Note) -> list[Note]:
        t = note.title.lower()
        return [n for n in self.notes().values() if t in n.links and n.title != note.title]

    # --- searching ------------------------------------------------------------------------------------------
    def search(self, text: str, limit: int = 5, include_daily: bool = False, expand: bool = True) -> list[Hit]:
        terms = _terms(text)
        if not terms:
            return []
        notes = self.notes()
        hits: list[Hit] = []
        for n in notes.values():
            if n.path == self.me_path or (n.kind == "daily" and not include_daily):
                continue
            tl, body = n.title.lower(), n.body.lower()
            score = 0.0
            for t in terms:
                if t in tl:
                    score += 5
                elif any(t in a.lower() for a in n.aliases):
                    score += 4
                if any(t in tag.lower() for tag in n.tags):
                    score += 3
                score += min(body.count(t), 3)
            if score >= 2:
                hits.append(Hit(n, score))
        hits.sort(key=lambda h: -h.score)
        top = hits[:limit]
        if expand:
            have = {h.note.title for h in top}
            extra: list[Hit] = []
            for seed in [h for h in top[:2] if h.score >= 5]:
                near = [notes[t] for t in seed.note.links if t in notes] + self.backlinks(seed.note)
                added = 0
                for n in near:
                    if (n.title in have or n.kind == "daily" or n.path == self.me_path
                            or not n.has_content() or added >= self.s["neighbors"]):
                        continue
                    have.add(n.title)
                    extra.append(Hit(n, seed.score * 0.4, seed.note.title))
                    added += 1
            top += extra
        return top[:limit + 2 * self.s["neighbors"]]

    # --- context for each request ---------------------------------------------------------------------------
    def context(self, text: str, m: dict) -> list[str]:
        """Prompt sections: what Batsy knows about you (the Me facts) and the notes related to this request."""
        parts: list[str] = []
        me = self.me()
        facts = me.facts() if me else []
        if len(facts) > m["all_facts_below"]:
            terms = _terms(text)
            scored = [(sum(t in f[1].lower() for t in terms), f) for f in facts]
            facts = [f for s, f in sorted(scored, key=lambda x: -x[0]) if s > 0][: m["max_facts"]]
        if facts:
            parts.append("[Things you remember]\n" + "\n".join(f"- ({i}) {_plain(t)}" for i, t in facts))
        hits = self.search(text, limit=self.s["context_notes"])
        if hits:
            parts.append("[From your notes: linked knowledge about the user's world]\n"
                         + "\n\n".join(self._brief(h, _terms(text)) for h in hits))
        return parts

    def _brief(self, h: Hit, terms: list[str]) -> str:
        n = h.note
        head = f"[{n.title} · {n.kind}" + (f" · linked from {h.via}" if h.via else "") + "]"
        facts = n.facts()
        facts.sort(key=lambda f: -sum(t in f[1].lower() for t in terms))  # stable: ties stay oldest-first
        lines = [f"- ({i}) {_clip(_plain(t), 160)}" for i, t in facts[: self.s["facts_per_note"]]]
        if not lines:
            para = next((p for p in re.split(r"\n\s*\n", n.body)
                         if p.strip() and not p.lstrip().startswith("#")), "")
            if para:
                lines = [_clip(_plain(para), 240)]
        notes = self.notes()
        near = [notes[t].title for t in n.links if t in notes][:6]
        if near:
            lines.append("links: " + ", ".join(near))
        out = head
        for line in lines:
            if len(out) + len(line) + 1 > self.s["context_chars"]:
                break
            out += "\n" + line
        return out

    # --- writing --------------------------------------------------------------------------------------------
    def _new_id(self) -> str:
        used = {i for n in self.notes().values() for i, _ in n.facts()}
        while True:
            fid = uuid.uuid4().hex[:5]
            if fid not in used:
                return fid

    def _autolink(self, text: str, exclude: str) -> str:
        """Wrap the first mention of each existing note (or alias) in [[wikilinks]]: this is what grows the graph."""
        if not self.s["auto_link"]:
            return text
        names: dict[str, str] = {}
        for n in self.notes().values():
            if n.path == self.me_path or n.kind == "daily" or n.title.lower() == exclude.lower():
                continue
            for nm in [n.title, *n.aliases]:
                if len(nm) >= 3:
                    names.setdefault(nm.lower(), n.title)
        if not names:
            return text
        pat = re.compile(r"(?<![\w\[])(" + "|".join(re.escape(k) for k in sorted(names, key=len, reverse=True)[:500])
                         + r")(?![\w\]])", re.I)
        done: set[str] = set()

        def sub(m):
            target = names[m.group(1).lower()]
            if target in done:
                return m.group(0)
            done.add(target)
            return f"[[{target}]]" if m.group(1) == target else f"[[{target}|{m.group(1)}]]"

        return "".join(seg if _PROTECTED.fullmatch(seg) else pat.sub(sub, seg) for seg in _PROTECTED.split(text))

    def _backfill_links(self, title: str) -> None:
        """A note was just created: link the first earlier mention of its title in each existing fact."""
        if not self.s["auto_link"] or len(title) < 3:
            return
        pat = re.compile(r"(?<![\w\[])(" + re.escape(title) + r")(?![\w\]])", re.I)
        for n in self.notes().values():
            if n.title.lower() == title.lower() or n.kind == "daily":
                continue
            lines, changed, done = n.path.read_text(encoding="utf-8").split("\n"), False, False
            for i, line in enumerate(lines):
                m = FACT_RE.match(line)
                if done or not m:
                    continue

                def sub(mm):
                    return f"[[{title}]]" if mm.group(1) == title else f"[[{title}|{mm.group(1)}]]"

                new = "".join(seg if _PROTECTED.fullmatch(seg) else pat.sub(sub, seg, count=1)
                              for seg in _PROTECTED.split(m.group("text")))
                if new != m.group("text"):
                    lines[i], changed, done = f"- {new} ^{m.group('id')}", True, True
            if changed:
                _write(n.path, _touch_updated("\n".join(lines)))

    def _seed_me(self) -> None:
        if self.me_path.exists():
            return
        today = time.strftime("%Y-%m-%d")
        _write(self.me_path, f"---\ntype: me\naliases: [{clean_title(self.user)}]\ntags: [batsy]\n"
                             f"created: {today}\nupdated: {today}\n---\n# Me\n\n"
                             "The hub of Batsy's memory: everything it knows about me links from here.\n\n"
                             "## Facts\n")

    def _add_to_map(self, note_title: str, kind: str) -> None:
        heading = FOLDERS.get(kind, "Topics")
        text = self.me_path.read_text(encoding="utf-8")
        link = f"- [[{note_title}]]"
        if link not in text:
            _write(self.me_path, _touch_updated(_insert_in_section(text, heading, link)))

    def _create(self, title: str, kind: str) -> Note:
        today = time.strftime("%Y-%m-%d")
        path = self.base / FOLDERS[kind] / f"{title}.md"
        _write(path, f"---\ntype: {kind}\naliases: []\ntags: [batsy]\ncreated: {today}\nupdated: {today}\n---\n"
                     f"# {title}\n\n## Facts\n")
        if kind != "daily":
            self._add_to_map(title, kind)
        self._backfill_links(title)
        return self._load(path)

    def _log_daily(self, line: str) -> None:
        if not self.s["daily_log"]:
            return
        day = time.strftime("%Y-%m-%d")
        path = self.base / FOLDERS["daily"] / f"{day}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = f"---\ntype: daily\naliases: []\ntags: [batsy]\ncreated: {day}\nupdated: {day}\n---\n# {day}\n"
        _write(path, text.rstrip("\n") + f"\n- {time.strftime('%H:%M')} {line}\n")

    def remember(self, fact: str, about: str | None = None, kind: str | None = None,
                 fid: str | None = None) -> tuple[str, str]:
        """Save one fact on the note it's about (or on Me). Creates and links the note when it's new, skips
        duplicates, links mentions of other notes, and logs it in today's daily note. Returns (id, message)."""
        fact = " ".join((fact or "").split())
        if not fact:
            raise ValueError("empty fact")
        with self.lock:
            self._seed_me()
            about = (about or "").strip()
            is_me = not about or about.lower() in {"me", "myself", "i", "user", "the user", self.user.lower()}
            if is_me:
                note = self.me()
            else:
                title = clean_title(about)
                note = self.resolve(title) or self._create(title, _kind(kind))
            existing = {_norm(t): i for i, t in note.facts()}
            if _norm(fact) in existing:
                return existing[_norm(fact)], f"Already knew that ({note.title})."
            fid = fid if fid and ID_RE.match(fid) and fid not in {i for n in self.notes().values()
                                                                  for i, _ in n.facts()} else self._new_id()
            line = f"- {self._autolink(fact, note.title)} ^{fid}"
            text = note.path.read_text(encoding="utf-8")
            _write(note.path, _touch_updated(_insert_in_section(text, "Facts", line)))
            self._log_daily(f"[[{note.title}]]: {_plain(fact)}")
            return fid, f"Saved to {note.title}."

    def forget(self, fid: str) -> str | None:
        """Remove the fact with this id. Returns the title of the note it was on."""
        if not ID_RE.match(fid or ""):
            return None
        with self.lock:
            for n in self.notes().values():
                lines = n.path.read_text(encoding="utf-8").split("\n")
                keep = [l for l in lines if not ((m := FACT_RE.match(l)) and m.group("id") == fid)]
                if len(keep) != len(lines):
                    _write(n.path, _touch_updated("\n".join(keep)))
                    return n.title
        return None

    def link(self, a: str, b: str) -> str:
        """Connect two notes both ways under '## Related' (creating either as a topic note if missing)."""
        with self.lock:
            self._seed_me()
            na = self.resolve(clean_title(a)) or self._create(clean_title(a), "topic")
            nb = self.resolve(clean_title(b)) or self._create(clean_title(b), "topic")
            for src, dst in ((na, nb), (nb, na)):
                text = src.path.read_text(encoding="utf-8")
                line = f"- [[{dst.title}]]"
                if line not in text:
                    _write(src.path, _touch_updated(_insert_in_section(text, "Related", line)))
            return f"Linked {na.title} and {nb.title}."

    def read(self, name: str, max_chars: int = 3000) -> str | None:
        n = self.resolve(name)
        if not n:
            return None
        text = n.path.read_text(encoding="utf-8", errors="replace")
        back = [b.title for b in self.backlinks(n)]
        return _clip_block(text, max_chars) + (f"\n\nBacklinks: {', '.join(back[:15])}" if back else "")

    # --- housekeeping ---------------------------------------------------------------------------------------
    def init(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        self._seed_me()
        claude_md = self.base / "CLAUDE.md"  # never overwrites one the user already has
        if not claude_md.exists():
            where = f"`{self.s['base']}/`" if self.s["base"] else "the vault root"
            claude_md.write_text(CLAUDE_MD.format(base=where), encoding="utf-8")

    def status(self) -> dict:
        notes = self.notes()
        kinds: dict[str, int] = {}
        for n in notes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        linked = {t for n in notes.values() for t in n.links}
        orphans = [n.title for n in notes.values()
                   if n.kind not in ("daily", "me") and not n.links and n.title.lower() not in linked]
        skills = sorted(p.parent.name for p in (self.root / ".claude" / "skills").glob("*/SKILL.md"))
        return {"path": str(self.root), "notes": len(notes), "kinds": kinds, "facts": sum(len(n.facts()) for n in notes.values()),
                "orphans": orphans, "skills": skills, "obsidian_cli": bool(shutil.which("obsidian"))}

    def install_skills(self) -> tuple[list[str], str]:
        """Copy the official obsidian-skills into <vault>/.claude/skills. Returns (skill names, message)."""
        from batsy.config import DATA_DIR

        src = DATA_DIR / "obsidian-skills"
        try:
            if (src / ".git").exists():
                subprocess.run(["git", "-C", str(src), "pull", "--ff-only", "-q"], timeout=120, check=True,
                               capture_output=True)
            else:
                shutil.rmtree(src, ignore_errors=True)
                subprocess.run(["git", "clone", "--depth", "1", "-q", SKILLS_REPO, str(src)], timeout=180,
                               check=True, capture_output=True)
        except (OSError, subprocess.SubprocessError) as e:
            if not (src / "plugins").exists():
                return [], (f"Couldn't fetch obsidian-skills ({type(e).__name__}). Install them yourself with: "
                            f"claude plugin marketplace add obsidianmd/obsidian-skills && "
                            f"claude plugin install obsidian@obsidian-skills")
        found = sorted((src / "plugins" / "obsidian" / "skills").glob("*/SKILL.md"))
        names = []
        for skill in found:
            dest = self.root / ".claude" / "skills" / skill.parent.name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(skill.parent, dest)
            names.append(skill.parent.name)
        return names, (f"Installed {len(names)} skills into {self.root / '.claude' / 'skills'}" if names
                       else "obsidian-skills had no skills in the expected folder.")

    def open_in_obsidian(self) -> None:
        subprocess.Popen(["xdg-open", f"obsidian://open?path={quote(str(self.root))}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def claude_hint(self) -> str:
        """A paragraph for Claude Code's brief: where memory lives and how to use it."""
        cli = ("The `obsidian` CLI is installed: use it (see the obsidian-cli skill) to rename or move notes so links "
               "update; it needs the Obsidian app open. " if shutil.which("obsidian") else "")
        return (f" The user's long-term memory is an Obsidian vault at {self.root}: notes about them, their people, "
                f"projects, places, devices and preferences, linked as a graph. Read {self.base}/CLAUDE.md first "
                f"and search the vault (Grep) before answering anything personal. When you learn something lasting "
                f"about them, save it with Bash: batsy vault remember \"one short fact\" --about \"Note title\" "
                f"[--kind person|project|place|setup|preference|topic]; it links, dedups and logs it. For richer "
                f"note editing follow the skills in {self.root}/.claude/skills (obsidian-markdown etc.). {cli}"
                f"Never store passwords, tokens or secrets in the vault.")


def _clip_block(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit("\n", 1)[0] + "\n…"


CLAUDE_MD = """# Batsy's memory vault

This vault is the long-term memory of Batsy, a voice assistant, about its user. Notes are plain Markdown linked
with [[wikilinks]], so Obsidian's graph view shows the knowledge graph. Batsy's notes live in {base}.

## Layout
- `Me.md` is the hub: the user's own facts under `## Facts`, and a map linking every other note.
- `People/ Projects/ Places/ Setup/ Preferences/ Topics/`: one note per thing; the folder matches `type:` in the frontmatter.
- `Daily/YYYY-MM-DD.md`: a timeline of what Batsy learned that day. Append only.

## Rules
- A fact is one short bullet under `## Facts` that ends in a block id: `- Uses an ESP32 for it ^a1b2c`. Keep the id; "forget" uses it.
- Add facts with `batsy vault remember "fact" --about "Note title" --kind person|project|place|setup|preference|topic`.
  It creates the note, links it from `Me`, links mentions of other notes, skips duplicates and logs the day.
- Link with `[[Note Title]]`, never a path. Connect two notes with `batsy vault link "A" "B"`.
- Rename or move notes with the `obsidian` CLI (see the obsidian-cli skill) so links update; otherwise edit the file.
- Use the obsidian-markdown skill for note syntax (properties, callouts, embeds).
- Never delete a note without being asked. Never store passwords, API keys or other secrets here.
- Search before answering anything personal: the answer is probably already in a note.
"""


# --- the shared instance ----------------------------------------------------------------------------------------
_vault: Vault | None = None
_vault_key = None
_vault_lock = threading.Lock()


def get(cfg) -> Vault | None:
    """The vault from config, or None when it's switched off or can't be opened (memory then falls back to SQLite)."""
    global _vault, _vault_key
    try:
        s = dict(cfg.get("vault"))
    except KeyError:
        return None
    if not s.get("enabled"):
        return None
    key = (s.get("path"), s.get("base"))
    with _vault_lock:
        if _vault is not None and _vault_key == key:
            return _vault
        try:
            user = cfg.get("user")["name"] or ""
            v = Vault(s.pop("path"), s, user)
            v.init()
            _import_legacy(v)
        except Exception:  # memory must never take the assistant down
            log.exception("vault unavailable, using SQLite memory only")
            return None
        _vault, _vault_key = v, key
        return v


def _import_legacy(v: Vault) -> None:
    """One time: move the facts Batsy already saved in SQLite into the vault, keeping their ids."""
    from batsy.memory_store import get_store

    store = get_store()
    flag = f"vault_imported:{v.root}"
    with store.lock:
        done = store.db.execute("SELECT 1 FROM state WHERE key = ?", (flag,)).fetchone()
    if done:
        return
    for f in store.all_facts():
        v.remember(f["fact"], fid=f["id"])
    with store.lock, store.db:
        store.db.execute("INSERT OR REPLACE INTO state VALUES (?, '1')", (flag,))
