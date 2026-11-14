"""Local brain: a small model running in Ollama on this laptop. Free, offline, no limits.

Tuned for speed on a CPU with 8 GB RAM: a ~3B model kept loaded, a tiny system prompt (prompt reading is
the slow part on CPU), few compact tools, short tool outputs, and replies streamed straight into speech.
"Ask Codex ..." hands a request to Codex on the user's Pro plan, only when they say so.
"""
import getpass
import json
import re
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from batsy.speech import SpeechChunker
from batsy.ai_router import route as ai_route
from batsy.config import WORKSPACE
from batsy.memory_store import get_store
from batsy.tools import ToolRegistry, truncate

log = logging.getLogger("batsy.local")

SYSTEM = """You are Batsy, {user}'s voice assistant on their Fedora laptop. {user} is {about}; use their \
vocabulary and skip the basics. Talk like a friend in a real conversation: warm, quick, natural, usually one or two short sentences. No lists, markdown or emoji. Input is \
speech-to-text, so guess sensibly at misheard words.
Use tools to actually do things on the laptop, then confirm in a few words. Open apps with run_shell \
background=true (e.g. gtk-launch firefox, xdg-open URL). Music plays on the iPad: use spotify.
Whenever the user wants to see something, however they phrase it, write [[show: exact name of the thing]] for a \
picture, or [[pull: exact name]] to put a person, place or thing on screen with its facts. For a list or steps \
write [[card: Title | line one; line two]]. Markers are not spoken.
If a request needs real expertise, careful reasoning, code, or you are not sure of the facts, call ask_codex \
with the full request instead of answering yourself.
Earlier conversation and things you remember come before [Now ...] in the message."""

# Compact tool set for the small model: (description, properties, required). Executed by the normal registry.
LOCAL_TOOLS = {
    "run_shell": ("Run a bash command on the laptop. background=true for apps and long tasks.",
                  {"command": {"type": "string"}, "background": {"type": "boolean"}}, ["command"]),
    "system_status": ("CPU, memory, temperature, battery, disk, network, top processes.",
                      {"sections": {"type": "array", "items": {"type": "string",
                       "enum": ["cpu", "memory", "temps", "battery", "disks", "network", "processes"]}}}, []),
    "spotify": ("Music on the iPad. action: play (query), queue, pause, resume, next, previous, now_playing, like.",
                {"action": {"type": "string", "enum": ["play", "queue", "pause", "resume", "next", "previous",
                                                       "now_playing", "like"]},
                 "query": {"type": "string"}}, ["action"]),
    "set_timer": ("Timer or reminder; message is spoken when it fires.",
                  {"seconds": {"type": "number"}, "message": {"type": "string"}}, ["seconds", "message"]),
    "agenda": ("The user's plans. add: date YYYY-MM-DD + text. list: date (and end_date).",
               {"action": {"type": "string", "enum": ["add", "list"]}, "date": {"type": "string"},
                "end_date": {"type": "string"}, "text": {"type": "string"}}, ["action"]),
    "remember": ("Save a lasting fact. about = the project, person or device it's about (omit for facts about the user).",
                 {"fact": {"type": "string"}, "about": {"type": "string"}}, ["fact"]),
    "camera": ("Open or close the live camera window.",
               {"action": {"type": "string", "enum": ["open", "close"]}}, ["action"]),
    "look": ("See the camera (what the user is holding/showing) or the screen. Codex looks and answers.",
             {"source": {"type": "string", "enum": ["webcam", "screen"]}, "question": {"type": "string"}},
             ["source", "question"]),
    "ask_codex": ("Hand a hard request to Codex (expert answers, code, careful reasoning). Codex replies to the user.",
                   {"request": {"type": "string"}}, ["request"]),
}

# Requests that a 3B model would fumble: route straight to Codex without asking the small model first.
COMPLEX = re.compile(
    r"\b(write|code|coding|program|script|sketch|debug|function|algorithm|compile error|error message|"
    r"explain (why|how|the difference)|step by step|compare|difference between|pros and cons|analy[sz]e|"
    r"summari[sz]e|essay|email|letter|report|cover letter|resume|plan (a|my|the)|itinerary|design|calculate|"
    r"solve|prove|derive|translate|strategy|optimi[sz]e|review my|in detail|detailed|research)\b")


# Questions about what the camera or screen shows: the small model can't see, so Codex looks at one frame.
VISION = re.compile(
    r"\b(what am i holding|what('s| is) (this|that)( thing| in my hand)?\??$|what do you see|can you see|look at (this|me|my)|"
    r"what('s| is) in front of|in my hand|on (my|the) screen|this (error|page|window|tab|message)|"
    r"what colou?r is (this|my)|read (this|that)|how do i look|do you see|which (one|component) is this|identify this)")
SCREEN_WORDS = re.compile(r"\b(screen|window|page|tab|error|message|browser|code)\b")


# While a model is in the viewer, these are about it (anything the local phrase parser didn't handle).
# Only real 3D words: "it" / "this" / "what is" alone are everyday talk (and the room's chatter), not the model.
THREE_D_TALK = re.compile(r"\b(model|part|mesh|print|printable|printing|hole|holes|wall|walls|fillet|chamfer|"
                          r"thread|screw|mount|edge|corner|face|support|overhang|infill|tolerance|design|stl|3d|"
                          r"extrude|combine|join|subtract|engrave|logo|rotate|dimensions?|thickness|millimet\w+)\b")
DESIGN = re.compile(r"\b(design|model|make|create|build|generate|draw)\b.*\b(3d|three d|stl|printable|"
                    r"to print|for printing|3d print|enclosure|bracket|mount|stand|holder|case|box|gear|knob)\b")


# While the live camera is on screen, questions like these are about what it shows.
CAMERA_TALK = re.compile(r"\b(this|that|these|those|it|its|here|they|them|what|which|who|how|why|is|are|does|do|can|"
                         r"should|could|would|identify|read|tell|explain|wrong|right|correct|value|colou?r|wired|"
                         r"connected|working|broken)\b")


# Short questions without these go to the fast path: no tools, a tiny prompt (the CPU reads ~45 tokens/s,
# so the full prompt with tools and memory costs ~30 s before the first word).
NEEDS_TOOLS = re.compile(  # clear actions only: anything else tries the fast path, which replies PASS if stuck
    r"\b(open|close|launch|play|pause|resume|skip|spotify|timer|remind|reminder|alarm|remember|forget|cpu|ram|"
    r"battery|temperature|disk|storage|wifi|volume|brightness|camera|install|download|agenda|schedule|calendar|"
    r"weather|news|price|score|search|look up|google|who won|git|pull|push|commit|repo|update|upgrade|restart|"
    r"reboot|yourself|folder|file|terminal|command|script|run|code|arduino|sketch|upload)\b")
# The fast path has no tools, so it must never get a command: it would only pretend ("Done.").
COMMAND = re.compile(r"^(?:(?:hey|ok|okay|please|now|so|and|can you|could you|would you|will you|go and|just)\s+)*"
                     r"(do|run|open|close|start|stop|restart|update|upgrade|install|pull|push|commit|create|make|write|"
                     r"delete|remove|move|copy|rename|send|set|turn|switch|change|go|find|search|download|build|"
                     r"compile|upload|launch|kill|show|play|put|add|check|fix|clean|save|type|press|click|enable|"
                     r"disable|connect|mute|unmute|lock|record|take|bring|pull up|let's|lets)\b")
QUICK_SYSTEM = ("You are Batsy, {user}'s friendly voice assistant. {user} is {about}. Answer in one or two short spoken sentences, "
                "no lists or markdown. Speak plainly as yourself: no role-play, sound effects, stage directions, "
                "narration or quotation marks; ignore any theme or character from earlier talk. Input is speech-to-text, so guess sensibly at misheard words. If the answer "
                "needs the laptop, the internet, recent events, or facts you're not sure of, reply with only: PASS")


def vision_source(text: str) -> str | None:
    t = text.lower().strip(" ?.!")
    if not VISION.search(t):
        from batsy.tools.screen import camera_pid

        return "webcam" if camera_pid() and CAMERA_TALK.search(t) else None
    return "screen" if SCREEN_WORDS.search(t) else "webcam"


def who(cfg) -> dict:
    """{user, about, github} for the prompts: who Batsy is talking to."""
    name = cfg.get("user.name") or os.environ.get("BATSY_USER_NAME") or getpass.getuser()
    try:
        from batsy import projects

        gh = cfg.get("github.user") or projects.index(cfg, wait=False).get("user") or ""
    except Exception:
        gh = cfg.get("github.user") or ""
    return {"user": name, "about": cfg.get("user.about") or "a developer", "github": gh}


def is_complex(text: str) -> bool:
    return bool(COMPLEX.search(text.lower())) or len(text.split()) > 22


# Asking for code to be written: goes to Codex as a coding job, never to the skill learner (which would
# answer "that's a coding task, I can't make it a skill" and write nothing).
CODE_WORK = re.compile(
    r"^(?:(?:hey|ok|okay|so|now|please|can you|could you|would you|will you|i want you to|i need you to|help me|"
    r"i want to|i need to|let's|lets)\s+)*"
    r"(?:write|create|make|build|generate|code|develop|program|scaffold|set up|setup|start|implement|draft)\b"
    r"(?:\s+(?!that\b|which\b|to\b)[\w+#.-]+){0,5}?\s+"
    r"(?:script|program|code|function|class|module|library|package|app|application|website|web ?site|web ?app|"
    r"landing page|web ?page|api|server|backend|frontend|bot|cli|tool|game|extension|plugin|notebook|component|"
    r"regex|query|dockerfile|makefile|workflow|unit tests?|tests?|scraper|parser|dashboard|cron job|addon|add-on|"
    r"shader|macro|snippet|algorithm)s?\b")


def is_code_work(text: str) -> bool:
    t = re.sub(r"[^a-z0-9+#.' -]", " ", text.lower())
    return bool(CODE_WORK.search(re.sub(r"\s+", " ", t).strip())) and not re.search(r"\bskills?\b", t)


CODE_DIR = WORKSPACE / "code"
CODE_NOTE = """

(This is a coding job. Make a folder for it under {code_dir}/ with a short, clear name, unless they named an \
existing project or folder: then work there. Write the real, complete code, run it or its tests on a small example \
and fix any errors before you finish. In the spoken reply say only the file name and how to run it, never the \
full path. Then add [[card: <file name> | what it does; how to run it]] and [[open: <absolute path of the main \
file>]] so it opens in their editor.)"""


class LocalBrain:
    def __init__(self, cfg, registry: ToolRegistry):
        self.cfg = cfg
        self.registry = registry
        self.on_delta = None
        self.chat_style = False
        self.brain_preference = "auto"
        self.memory = get_store()
        self.lock = threading.Lock()
        self.visual: Callable[[str, str], None] | None = None
        self.url = cfg.get("local.url").rstrip("/") + "/api/chat"
        self.model = cfg.get("local.model")
        self.system = SYSTEM.format(**who(cfg))
        self.tools = [
            {"type": "function", "function": {"name": name, "description": desc,
                                              "parameters": {"type": "object", "properties": props, "required": req}}}
            for name, (desc, props, req) in LOCAL_TOOLS.items()
            if name in registry.tools or name in ("ask_codex", "look")  # these two are handled by the brain itself
        ]
        self._codex_installed = bool(shutil.which("codex"))
        self.skills = None            # SkillBook, set by the app
        self.last_skill_run: str | None = None
        self.last_doc = None          # (path, time) of the file worked on last, for follow-ups
        threading.Thread(target=self._warm_up, daemon=True, name="warm-up").start()

    @property
    def codex_ok(self) -> bool:
        """Codex is installed, auto-routing is on, and Batsy's Codex budget isn't used up."""
        from batsy import codex_adapter

        allowed = self.cfg.get("local.auto_codex") or self.brain_preference == "codex"
        return (self._codex_installed and allowed and self.brain_preference != "local"
                and codex_adapter.over_budget(self.cfg) is None)

    # --- Ollama -------------------------------------------------------------
    def _options(self) -> dict:
        return {"num_ctx": self.cfg.get("local.context"), "temperature": 0.6, "num_predict": 400}

    def _warm_up(self) -> None:
        """Load the model and pre-read the system prompt so the first real reply is quick."""
        body = {"model": self.model, "stream": False, "think": False, "keep_alive": self.cfg.get("local.keep_alive"),
                "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": "hi"}],
                "tools": self.tools, "options": {**self._options(), "num_predict": 1}}
        try:
            self._post(body).read()
            log.info("local model %s ready", self.model)
        except Exception as e:
            log.warning("local model warm-up failed: %s", e)

    def _post(self, body: dict):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=300)

    def _stream(self, messages: list[dict], chunker: SpeechChunker, abort: threading.Event):
        body = {"model": self.model, "stream": True, "think": False, "keep_alive": self.cfg.get("local.keep_alive"),
                "messages": messages, "tools": self.tools, "options": self._options()}
        content, calls, stats, fed = "", [], {}, 0
        with self._post(body) as resp:
            for line in resp:
                if abort.is_set():
                    return None
                if not line.strip():
                    continue
                part = json.loads(line)
                msg = part.get("message", {})
                if msg.get("content"):
                    content += msg["content"]
                    # Small models sometimes write a tool call as text: never speak from the first '{"' on.
                    end = content.find('{"')
                    end = len(content) - content.endswith("{") if end < 0 else end
                    if end > fed:
                        chunker.feed(content[fed:end])
                        fed = end
                calls += msg.get("tool_calls") or []
                if part.get("done"):
                    stats = part
        self._log_usage(stats)
        if not calls:
            calls = self._calls_in_text(content)
        return content, calls

    def status(self, text: str) -> None:
        """What Batsy is doing right now, for the overlay ("Asking Codex"...). Replaced by the voice app."""

    def _quick(self, text: str, emit, on_marker, abort: threading.Event) -> bool:
        """Fast answer with a tiny prompt and no tools, spoken as it streams. False = the small model passed
        (replied PASS: it needs the full path), and nothing was said."""
        self.status("Thinking with the local model")
        recent = self.memory.build_context(text, self.cfg, recent=1, related=0)[-400:]
        messages = [{"role": "system", "content": QUICK_SYSTEM.format(**who(self.cfg))},
                    {"role": "user", "content": (f"{recent}\n\n" if recent else "") + text}]
        body = {"model": self.model, "stream": True, "think": False, "keep_alive": self.cfg.get("local.keep_alive"),
                "messages": messages, "options": {**self._options(), "num_predict": 80}}
        chunker, content, started = SpeechChunker(emit, on_marker, self.on_delta), "", False
        try:
            with self._post(body) as resp:
                for line in resp:
                    if abort.is_set():
                        return True
                    if not line.strip():
                        continue
                    part = json.loads(line)
                    content += part.get("message", {}).get("content", "")
                    if not started:  # hold the first few characters back: it may be "PASS"
                        if "PASS" in content.upper()[:12]:
                            return False
                        if len(content.strip()) < 8 and not part.get("done"):
                            continue
                        started = True
                        chunker.feed(content)
                    else:
                        chunker.feed(part.get("message", {}).get("content", ""))
                    if part.get("done"):
                        self._log_usage(part)
        except (urllib.error.URLError, ValueError) as e:
            log.warning("quick answer failed: %s", e)
            return False
        if not started:
            return False
        chunker.flush()
        return True

    @staticmethod
    def _calls_in_text(content: str) -> list[dict]:
        """{"name": "runshell", "parameters": {...}} written as text -> a real tool call."""
        start = content.find('{"')
        if start < 0:
            return []
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[start:])
        except ValueError:
            return []
        if not isinstance(obj, dict) or "name" not in obj:
            return []
        want = re.sub(r"[^a-z]", "", str(obj["name"]).lower())
        name = next((n for n in [*LOCAL_TOOLS, "look", "ask_codex"] if n.replace("_", "") == want), None)
        if name is None:
            return []
        return [{"function": {"name": name, "arguments": obj.get("parameters") or obj.get("arguments") or {}}}]

    def _log_usage(self, s: dict) -> None:
        from batsy.usage import USAGE_FILE

        row = {"t": int(time.time()), "model": f"local:{self.model}", "in": s.get("prompt_eval_count", 0),
               "out": s.get("eval_count", 0), "cache_read": 0, "cache_write": 0}
        log.info("local usage %s (%.1fs)", row, s.get("total_duration", 0) / 1e9)
        with open(USAGE_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")

    # --- the turn -----------------------------------------------------------
    def note_announcement(self, text: str) -> None:
        self.memory.add_exchange("alert", "", text, [])

    def ask(self, text: str, source: str, say, abort: threading.Event, on_tool=None) -> str:
        with self.lock:
            return self._ask(text, source, say, abort, on_tool)

    def _ask(self, text, source, say, abort, on_tool) -> str:
        m = self.cfg.get("local")
        recent = max(m["recent_exchanges"], self.cfg.get("chat.history_in_context")) if self.chat_style else m["recent_exchanges"]
        context = self.memory.build_context(text, self.cfg, recent=recent, related=m["related_exchanges"])
        stamp = time.strftime("%a %d %b %Y %H:%M")
        prompt = (f"{context}\n\n" if context else "") + f"[Now {stamp}] {text}"
        system_prompt = self.system + ("\nFor this typed chat reply, answer fully with useful Markdown and fenced code blocks. Avoid filler."
                                       if self.chat_style else "")
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        spoken, actions = [], []
        if self.chat_style and self.brain_preference == "codex":
            if not self._codex_installed:
                message = "Codex CLI is not installed, so I cannot use the Codex brain."
                say(message)
                spoken.append(message)
                return self._finish(source, text, spoken, actions, abort)
            self.route = "codex"
            self._codex(text, context, lambda s: (spoken.append(s), say(s)),
                         lambda kind, arg: self.visual(kind, arg) if self.visual and not abort.is_set() else None,
                         on_tool, actions, force=True)
            return self._finish(source, text, spoken, actions, abort)

        def emit(sentence: str) -> None:
            spoken.append(sentence)
            say(sentence)

        def on_marker(kind: str, arg: str) -> None:
            actions.append(f"showed {kind}: {arg[:60]}")
            if self.visual and not abort.is_set():
                threading.Thread(target=self.visual, args=(kind, arg), daemon=True).start()

        from batsy import docs
        from batsy.learning import ACTION, norm

        from batsy import model3d

        explicit = ASK_CODEX.match(text.strip(" .!?"))
        if explicit and self._codex_installed:  # "ask Codex to ...": straight to Codex, nothing local
            request = re.sub(r"\banyway\b", "", explicit.group(1), flags=re.I).strip()
            emit("Asking Codex.")
            self._codex(request, context, emit, on_marker, on_tool, actions,
                         force=bool(re.search(r"\banyway\b", text, re.I)))  # "anyway" ignores Batsy's budget
            return self._finish(source, text, spoken, actions, abort)

        if self.codex_ok and re.search(r"\bscreen ?saver\b", text.lower()):
            emit("Let me redesign that.")
            from batsy import codex_adapter
            from batsy.saver import SAVER_BRIEF, load

            self.status("Redesigning the screensaver with Codex")
            self.route = "codex"
            answer = codex_adapter.run(SAVER_BRIEF.format(request=text, spec=json.dumps(load())), "redesigning screensaver",
                                     args=["--allowedTools", "Bash(batsy saver:*)"], timeout=300)
            chunker = SpeechChunker(emit, on_marker, self.on_delta)
            chunker.feed(answer)
            chunker.flush()
            actions.append("redesigned the screensaver")
            return self._finish(source, text, spoken, actions, abort)

        from batsy.tools.screen import camera_pid

        if camera_pid() and self.codex_ok and vision_source(text):  # the live camera is on screen: it's about that
            self.route = "look"
            self._look("webcam", text, context, emit, on_marker, on_tool, actions)
            return self._finish(source, text, spoken, actions, abort)

        if self.codex_ok and (model3d.VIEWER.active() and THREE_D_TALK.search(text.lower())
                               or DESIGN.search(text.lower())):
            self.route = "3d"
            self._codex_3d(text, context, emit, on_marker, on_tool, actions)
            return self._finish(source, text, spoken, actions, abort)

        doc = self._doc_target(text)
        if doc is not None:
            self.route = "doc"
            self._doc(text, doc, context, emit, on_marker, on_tool, actions)
            return self._finish(source, text, spoken, actions, abort)

        src = vision_source(text)
        if src and self.codex_ok:
            self.route = "look"
            self._look(src, text, context, emit, on_marker, on_tool, actions)
            return self._finish(source, text, spoken, actions, abort)

        if self.codex_ok and is_code_work(text):
            # "create a python script that...", "build me a flask API": real code, written and tested by Codex
            # Code in a project folder. Never the skill learner: a skill is a voice shortcut, not the thing asked for.
            self.route = "codex"
            emit("On it, I'll write that.")
            self._codex(text + CODE_NOTE.format(code_dir=CODE_DIR), context, emit, on_marker, on_tool, actions)
            return self._finish(source, text, spoken, actions, abort)

        if self.codex_ok and self.skills and ACTION.match(norm(text)):
            # a new kind of action: Codex writes code for it once; next time it runs without any AI.
            # Read back what we heard first: a misheard word ("plot" for "Codex") must not run anything.
            if not self.registry.ctx.confirm(f"Just to check, you want me to {text.strip().rstrip('.?!')}?"):
                return self._finish(source, text, spoken, actions, abort)
            self.route = "learn"
            emit("One sec.")  # usually an existing skill said differently; only new ones are "learned"
            self.status("Checking my skills with Codex")
            out = self.skills.learn(text, self.registry.ctx.confirm)
            if out["kind"] == "answer":
                emit(out["text"])
            else:
                if out.get("spoken"):
                    emit(out["spoken"])
                if out.get("ok") and not out.get("alias"):
                    emit("I've learned that, so next time it'll be instant.")
                    self.last_skill_run = out["name"]
                    actions.append(f"learned skill {out['name']}")
            return self._finish(source, text, spoken, actions, abort)

        self.route = "codex"
        if self.codex_ok and ai_route(text) == "codex":
            self._codex(text, context, emit, on_marker, on_tool, actions)
            return self._finish(source, text, spoken, actions, abort)

        if len(text.split()) <= 14 and not NEEDS_TOOLS.search(text.lower()) and not COMMAND.match(text.lower()):
            self.route = "quick"
            if self._quick(text, emit, on_marker, abort):
                return self._finish(source, text, spoken, actions, abort)

        self.route = "full"
        for _ in range(m["max_steps"]):
            chunker = SpeechChunker(emit, on_marker, self.on_delta)
            self.status("Thinking with the local model")
            try:
                result = self._stream(messages, chunker, abort)
            except urllib.error.URLError as e:
                emit("My local brain isn't running. Start it with: systemctl start ollama.")
                log.error("ollama: %s", e)
                break
            if result is None:
                break
            chunker.flush()
            content, calls = result
            messages.append({"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})})
            if not calls:
                break
            handed_off = False
            for call in calls:
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if name == "look" and self.codex_ok:
                    self._look(args.get("source", "webcam"), args.get("question") or text, context,
                               emit, on_marker, on_tool, actions)
                    handed_off = True
                    break
                if name == "ask_codex" and self.codex_ok:
                    self.route = "codex"
                    self._codex(args.get("request") or text, context, emit, on_marker, on_tool, actions)
                    handed_off = True
                    break
                if on_tool:
                    on_tool(name)
                self.status(f"Running {name.replace('_', ' ')}")
                out, is_error = self.registry.execute(name, args) if name in LOCAL_TOOLS else (f"No tool {name}.", True)
                out = out if isinstance(out, str) else "done"
                actions.append(f"{name} {json.dumps(args)[:80]} -> {'ERROR ' if is_error else ''}{out.strip()[:60]}")
                # every character here must be re-read by a slow CPU: keep it short
                messages.append({"role": "tool", "tool_name": name, "content": truncate(out, m["tool_result_chars"])})
            if abort.is_set() or handed_off:
                break
        self._maybe_learn_from(text, actions)
        return self._finish(source, text, spoken, actions, abort)

    def _maybe_learn_from(self, text: str, actions: list[str]) -> None:
        """The local AI just did something with tools: in the background, have Codex turn it into a skill
        so the same request skips the AI next time."""
        from batsy.learning import norm

        did = [a for a in actions if a.split(" ", 1)[0] in ("run_shell", "system_status", "spotify", "camera")]
        if not (self.codex_ok and self.skills and did) or any("ERROR" in a for a in did):
            return
        if self.skills.match(norm(text)):
            return
        threading.Thread(target=self.skills.learn, args=(text, self.registry.ctx.confirm),
                         kwargs={"execute": False, "happened": "\n".join(did)}, daemon=True,
                         name="learn").start()

    def _finish(self, source, text, spoken, actions, abort) -> str:
        reply = " ".join(spoken)
        self.last_actions = actions  # the learner remembers plain answers only, not ones that used tools
        self.memory.add_exchange(source, text, reply + (" (interrupted)" if abort.is_set() else ""), actions)
        return reply

    def _doc_target(self, text: str):
        """The file this request is about ("this pdf", "my physics notes"), or a follow-up edit of the last one."""
        from pathlib import Path

        from batsy import docs

        parsed = docs.parse_request(text)
        if parsed:
            name, kind = parsed
            path = docs.resolve_file(name, kind)
            return path if path else Path("/nonexistent")  # tell the user we couldn't find it
        last = getattr(self, "last_doc", None)
        if last and time.time() - last[1] < 1200 and docs.DOC_VERBS.search(text.lower()) and re.search(
                r"\b(just sent|i sent|you got|just got|just arrived|i shared|i just shared|that file|the file i)\b",
                text.lower()):  # "summarise what I just sent"
            return last[0]
        if last and time.time() - last[1] < 1200 and docs.EDIT_VERBS.search(text.lower()) and \
                re.search(r"\b(it|this|that|there|also|now|again|too|page|line|word|the)\b", text.lower()):
            return last[0]
        return None

    def _doc(self, text, path, context, emit, on_marker, on_tool, actions) -> None:
        """Read the file locally (and remember it), then let Codex do what was asked with Batsy's doc tools."""
        from batsy import docs

        if not path.exists():
            emit("I couldn't find that file. Tell me its name, or open it first and say this again.")
            return
        if not self.codex_ok:
            note = docs.learn(path)
            emit(f"I've read {path.name}, {note}. Codex isn't set up, so I can't mark it up yet.")
            return
        emit(f"Looking at {path.stem.replace('_', ' ')}.")
        if on_tool:
            on_tool("doc")
        try:
            note = docs.learn(path)
        except Exception as e:
            log.warning("could not read %s: %s", path, e)
            note = "unreadable locally"
        self.last_doc = (path, time.time())
        actions.append(f"worked on file {path}")
        self.status("Reading the document with Codex")
        answer = speak_stream(lambda on_text: ask_codex_docs(text, path, docs.info(path), note, context,
                                                              on_text=on_text), emit, on_marker)
        self.memory.set_document_summary(str(path), answer[:600])

    def _codex_3d(self, text, context, emit, on_marker, on_tool, actions) -> None:
        """Codex works on 3D: it can see the viewer, drive it, and create or edit models in code."""
        emit("Let me work on that.")
        if on_tool:
            on_tool("3d")
        self.status("Working on the 3D model with Codex")
        speak_stream(lambda on_text: ask_codex_3d(text, context, on_text=on_text), emit, on_marker)
        actions.append("3D work by Codex")

    def _look(self, source, question, context, emit, on_marker, on_tool, actions) -> None:
        """One frame from the camera window (or a screenshot) -> Codex looks at it and answers."""
        from batsy.tools.screen import capture

        img = WORKSPACE / ".vision" / f"{source}.jpg"
        self.status("Looking through the camera" if source == "webcam" else "Reading the screen")
        err = capture(source, img, self.cfg)
        if err:
            emit(f"I couldn't see the {source}: {err[:120]}")
            return
        actions.append(f"looked at {source}")
        self._codex(f"{question}\n\n(Read the image at {img}: it's the user's {'screen' if source == 'screen' else 'webcam, showing them and what they hold'} right now.)",
                     context, emit, on_marker, on_tool, actions)

    def _codex(self, request, context, emit, on_marker, on_tool, actions, force: bool = False) -> None:
        """Answer with Codex on the user's Pro plan; its reply goes straight to speech and screen."""
        if on_tool:
            on_tool("ask_codex")
        log.info("routing to Codex: %s", request[:120])
        self.status("Asking Codex" if "(Read the image" not in request else "Asking Codex about what I see")
        speak_stream(lambda on_text: ask_codex(request, context, force=force, on_text=on_text), emit, on_marker, self.on_delta)
        actions.append("answered by Codex")

    def reset(self) -> None:
        with self.lock:
            self.memory.new_session()


ASK_CODEX = re.compile(r"(?:hey |ok )?ask (?:codex|cloud|clawed)(?: to| about| if| whether)?[,\s]+(.+)", re.I | re.S)
CODEX_BRIEF = """You are the expert brain behind Batsy, a voice assistant on the user's Fedora laptop. Your reply is \
spoken aloud. The user, {user}, is {about}: talk to them at that level, in their vocabulary.{github} You can work anywhere on this laptop: read, edit and create files in any folder (use absolute paths; \
home is {home}, Batsy's own code is {code}: to change Batsy itself, edit those files in place, never copies \
elsewhere, then tell them to say "restart yourself") and run any command. Risky commands are confirmed with the user by voice \
automatically; for root use pkexec, never sudo. When asked to do something, do it fully, then say briefly what you did. Answer exactly what was asked and stop: usually one short sentence, two at most, unless they \
ask for detail, an explanation or steps. No extra facts, background, tips or follow-up offers they didn't ask \
for ("what is this?" -> "That's a red banana."). No markdown or lists in the spoken part. Be precise and correct.
Only when they ask to see something, or for steps/lists/code, add a screen marker (not spoken): [[show: exact \
name]] for a picture, [[pull: exact name]] for a person/place/thing with its facts, [[card: Title | line; line]] \
for steps, lists, numbers or code summaries. Put any code or files under {workspace} and just say where.{vault}
{context}
User: {request}"""


DOC_BRIEF = """You are the hands and eyes of Batsy, a voice assistant on the user's Fedora laptop. The user said: \
"{request}"

It's about this file: {path}
{info} ({note}). Never modify the original. Put anything you create in {out_dir}.

Read it yourself with Read (PDF pages and images both work). Batsy's document tools (run with Bash):
  batsy doc text FILE                  full text (OCR for images/scans)
  batsy doc find FILE "exact words"    exact positions of text, as [{{page, bbox:[x0,y0,x1,y1]}}]
  batsy doc render FILE --page N --grid  PNG of the page with a pixel grid, for placing shapes; Read it to look
  batsy doc annotate FILE 'JSON'       mark up a copy. JSON: {{"actions": [...]}}, each action has a type, one of \
highlight, underline, strike, circle, box, note, label, arrow or redact. Give exact "text" (found for you, best \
for words) or "bbox" in render pixels (for drawings and objects). Optional: "page", "color" (red, yellow, green, \
blue, orange, purple, pink, black), "occurrence"; label/note take "text"; arrow takes "from"/"to". Returns the \
output path and anything not found.
For anything else (crop, rotate, convert, extract tables, fill forms, merge...), write and run Python with \
{python} (PyMuPDF as pymupdf, Pillow, pytesseract available).

After marking something up, render the output and look at it. Fix anything misplaced before you finish.
Final reply: two or three spoken sentences about what you found or did, no markdown. Then [[open: OUTPUT_PATH]] \
for every file you made, and optionally [[card: Title | line; line]] for key facts.
{context}"""


def ask_codex_docs(request: str, path, info: dict, note: str, context: str = "", timeout: int = 600,
                    on_text=None) -> str:
    """Codex reads the file and does the job, using `batsy doc ...` and Python, then checks its own work."""
    import sys

    from batsy import codex_adapter
    from batsy.docs import OUT_DIR

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prompt = DOC_BRIEF.format(request=request, path=path, info=json.dumps(info), note=note, out_dir=OUT_DIR,
                              python=sys.executable, context=context)
    return codex_adapter.run(prompt, f"file: {path.name}", cwd=OUT_DIR, timeout=timeout, on_text=on_text, args=[
        "--permission-mode", "acceptEdits", "--add-dir", str(path.parent), "--add-dir", str(OUT_DIR),
        "--allowedTools", f"Read,Write,Edit,Glob,Grep,Bash(batsy doc:*),Bash({sys.executable}:*)"])


THREE_D_BRIEF = """You are the 3D engineer behind Batsy, a voice assistant on the user's Fedora laptop. The user said: \
"{request}"

Batsy's 3D viewer (a window on their screen) state: {state}
Tools (run with Bash):
  batsy 3d info                    loaded models: size, bounds, volume, mass, watertight, parts
  batsy 3d screenshot              PNG of the viewer: Read it to see exactly what the user sees
  batsy 3d cmd 'JSON'              drive the viewer. Commands: load {{path, keep}}, view {{view: top|bottom|front|\
back|left|right|iso}}, rotate {{axis, deg}}, move {{dx, dy, dz}}, scale {{factor | to_mm + axis}}, mirror {{axis}}, \
center, drop, style {{style: solid|wireframe|edges|xray|opaque}}, color {{color}}, dims {{on}}, section {{axis, at 0-1}}, \
explode, measure, spin {{on}}, zoom {{factor}}, export {{format, path}}, message {{text}} (shown in the viewer)
To create or change geometry, write and run Python with {python}. trimesh, manifold3d (booleans, extrusions, \
fillets via offsets), numpy and scipy are available; work in millimetres. Save results in {out_dir}, then load them \
into the viewer (keep=true to show next to the original). Never overwrite the user's original file.
For printability questions, check watertightness, wall thickness, overhangs and the size against a typical \
220 x 220 x 250 mm printer bed.
Final reply: two or three spoken sentences, no markdown. Add [[card: Title | line; line]] for key numbers.
{context}"""


def ask_codex_3d(request: str, context: str = "", timeout: int = 900, on_text=None) -> str:
    import sys

    from batsy import codex_adapter
    from batsy.model3d import VIEWER
    from batsy.viewer3d import OUT

    OUT.mkdir(parents=True, exist_ok=True)
    try:
        state = json.dumps(VIEWER.send({"cmd": "info"})) if VIEWER.is_open() else "closed (load a file to open it)"
    except OSError:
        state = "not responding"
    prompt = THREE_D_BRIEF.format(request=request, state=state[:3000], python=sys.executable, out_dir=OUT,
                                  context=context)
    return codex_adapter.run(prompt, "3D: " + request[:40], cwd=OUT, timeout=timeout, on_text=on_text, args=[
        "--permission-mode", "acceptEdits", "--add-dir", str(OUT), "--add-dir", os.path.expanduser("~"),
        "--allowedTools", f"Read,Write,Edit,Glob,Grep,Bash(batsy 3d:*),Bash({sys.executable}:*)"])


def speak_stream(ask, emit, on_marker=None, on_delta=None) -> str:
    """Run ask(on_text) and speak Codex's answer sentence by sentence as it's written, not after it ends."""
    chunker, streamed = SpeechChunker(emit, on_marker, on_delta), []

    def on_text(chunk: str) -> None:
        streamed.append(chunk)
        chunker.feed(chunk)

    answer = ask(on_text)
    if not streamed:  # nothing streamed (over budget, an error): speak the message itself
        chunker.feed(answer)
    chunker.flush()
    return answer


def ask_codex(prompt: str, context: str = "", timeout: int = 900, force: bool = False, on_text=None) -> str:
    """One request to Codex (the official CLI, on the user's Pro plan). It may work anywhere on the laptop:
    the whole filesystem, any command (risky ones voice-confirmed by batsy.guard), and the web."""
    from pathlib import Path

    from batsy import codex_adapter

    home = Path.home()
    from batsy.config import Config

    me = who(codex_adapter.CFG or Config.load())
    github = (f" Their GitHub username is {me['github']} (use gh if it's installed, or the GitHub API with curl; "
              f"GITHUB_TOKEN may be set). Their projects are listed under things you remember.") if me["github"] else ""
    from batsy.vault import get as get_vault

    vault = get_vault(codex_adapter.CFG or Config.load())
    vault_context = vault.claude_hint() if vault else ""
    full = CODEX_BRIEF.format(workspace=WORKSPACE, context=context + vault_context, request=prompt, home=home,
                               code=Path(__file__).resolve().parent.parent, user=me["user"], about=me["about"],
                               github=github)
    return codex_adapter.run(full, "ask: " + prompt[:40], cwd=home, timeout=timeout, force=force, on_text=on_text, args=[
        "--permission-mode", "acceptEdits", "--add-dir", "/",
        "--allowedTools", "Read,Write,Edit,MultiEdit,Glob,Grep,Bash,WebSearch,WebFetch,TodoWrite"])
