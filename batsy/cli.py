"""Command line entry point.

  batsy            run the voice assistant (wake word "Batsy")
  batsy chat       type to Batsy (talks to the running voice daemon if there is one)
  batsy ask TEXT   one-off request
  batsy doctor     check microphone, models, API key and tools
  batsy reset      archive the conversation and start fresh
  batsy usage      tokens used today / this week / all time
  batsy spotify-login   connect Batsy to your Spotify account (one time)
"""
import argparse
import fcntl
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from batsy.config import CONFIG_DIR, DATA_DIR, MODELS_DIR, Config


def _setup_logging(verbose: bool) -> None:
    handlers = [logging.FileHandler(DATA_DIR / "batsy.log")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "openai", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _require_key() -> None:
    return


def _single_instance():
    fh = open(DATA_DIR / "batsy.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Batsy is already running. Use `batsy chat` to type to it.")
    return fh


def cmd_run(cfg, args) -> None:
    _require_key()
    if getattr(args, "worker", False):  # the supervised process that does the work
        _crash_reporting()
        from batsy.app import Batsy

        Batsy(cfg, voice=True).run_voice()
        return
    lock = _single_instance()  # noqa: F841 - held for the life of the process
    _supervise(args)


def _crash_reporting() -> None:
    """Native crashes (audio, speech models, Qt) leave a trace in crash.log; thread errors go to batsy.log."""
    import faulthandler

    faulthandler.enable(open(DATA_DIR / "crash.log", "a"))
    log = logging.getLogger("batsy")
    threading.excepthook = lambda a: log.error("thread %s crashed", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _supervise(args) -> None:
    """Run Batsy in a child process and bring it back if it ever dies. Backs off if it keeps crashing,
    and tells you (desktop notification) instead of looping silently."""
    import signal
    import subprocess
    import time

    log = logging.getLogger("batsy")
    argv = [sys.executable, "-m", "batsy"] + (["-v"] if args.verbose else []) + ["run", "--worker"]
    crashes: list[float] = []
    child = None

    def stop(*_):
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    while True:
        started = time.time()
        child = subprocess.Popen(argv)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            stop()
        if code == 0:
            return
        now = time.time()
        crashes = [t for t in crashes if now - t < 600] + [now]
        delay = [1, 2, 5, 15, 30, 60][min(len(crashes) - 1, 5)] if now - started < 120 else 1
        log.error("Batsy stopped unexpectedly (exit %s); restarting in %ss (see crash.log)", code, delay)
        if len(crashes) == 3:
            subprocess.run(["notify-send", "-a", "Batsy", "Batsy keeps restarting",
                            f"It crashed {len(crashes)} times in 10 minutes. Details: {DATA_DIR / 'batsy.log'}"],
                           stderr=subprocess.DEVNULL, check=False)
        time.sleep(delay)


def _local_turn(app, text: str) -> None:
    def say(sentence):
        print(f"Batsy: {sentence}")
        app.speaker.say(sentence)

    if not app.local_command(text):
        app.run_turn(text, "typed", say, threading.Event(), app._confirm_console)
    app.speaker.wait_idle()


def cmd_chat(cfg, args) -> None:
    from batsy.app import ask_daemon

    if ask_daemon("", speak=False):
        print("Connected to the running Batsy. Ctrl+D to quit.")
        remote = True
    else:
        _require_key()
        lock = _single_instance()  # noqa: F841
        from batsy.app import Batsy

        app = Batsy(cfg, voice=False, speak=args.speak)
        remote = False
        print("Batsy (text mode). Ctrl+D to quit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if remote:
            ask_daemon(text, speak=args.speak)
        else:
            _local_turn(app, text)


def cmd_stop(cfg, args) -> None:
    """Stop the conversation: Batsy stops talking, cancels what it's doing and stops listening (for a hotkey)."""
    from batsy.app import ask_daemon

    if not ask_daemon("stop"):
        print("Batsy isn't running.")


def cmd_mode(cfg, args) -> None:
    from batsy.app import ask_daemon
    if not ask_daemon("", mode=args.mode):
        sys.exit("Batsy isn't running. Start it with `batsy run` before switching modes.")


def cmd_listen(cfg, args) -> None:
    """Listen now, no wake word: push-to-talk (bound to Super+B)."""
    from batsy.app import listen_now

    if not listen_now():
        print("Batsy isn't running.")


def cmd_ask(cfg, args) -> None:
    from batsy.app import ask_daemon

    text = " ".join(args.text)
    if ask_daemon(text, speak=args.speak):
        return
    _require_key()
    lock = _single_instance()  # noqa: F841
    from batsy.app import Batsy

    _local_turn(Batsy(cfg, voice=False, speak=args.speak), text)


def cmd_reset(cfg, args) -> None:
    from batsy.app import ask_daemon
    from batsy.memory_store import get_store

    if not ask_daemon("new conversation"):
        get_store().new_session()
    print("Fresh conversation. Everything stays stored locally and searchable.")


def cmd_usage(cfg, args) -> None:
    from batsy import usage

    st = usage.stats()
    for label, key in (("today", "today"), ("last 7 days", "week"), ("all time", "all")):
        t = st[key]
        print(f"{label:12} {t['requests']:5} requests | {t['tokens']:>10,} tokens (input {t['in']:,}, cached "
              f"{t['cache_read']:,}, cache write {t['cache_write']:,}, output {t['out']:,}) | ≈ ${t['cost']:.2f}")


def cmd_vocab(cfg, args) -> None:
    from batsy.vocab import get_vocab

    v = get_vocab()
    if args.forget:
        print("Removed." if v.forget(args.forget) else "No such phrase.")
        return
    for r in v.list():
        print(f"{'learned' if r['learned'] else 'built-in':8} {r['intent']:8} used {r['uses']:3}x  {r['template']}")


def cmd_skills(cfg, args) -> None:
    from batsy.learning import SkillBook, codex_cli

    book = SkillBook(codex_cli)
    if args.forget:
        print("Removed." if book.forget(args.forget) else "No such skill.")
        return
    rows = book.listing()
    if not rows:
        print("No skills learned yet.")
    for r in rows:
        state = "DISABLED" if r["disabled"] else ("not a learned skill" if r["problem"] else "ok")
        print(f"{r['name']:28} {state:10} worked {r['successes']}x, failed {r['failures']}x")
        for t in r["triggers"][:4]:
            print(f"    “{t}”")


def cmd_vault(cfg, args) -> None:
    """`batsy vault ...`: the Obsidian memory vault. Also how Claude Code saves memories (same code path as voice)."""
    from batsy import vault

    text = " ".join(args.text).strip()
    if args.path:
        cfg.data["vault"] = {**cfg.data["vault"], "path": args.path, "enabled": True}
    v = vault.get(cfg)
    if v is None:
        sys.exit("The vault is switched off or couldn't be opened ([vault] in ~/.config/batsy/config.toml).")
    act = args.action
    if act == "init":
        if not args.no_skills:
            names, msg = v.install_skills()
            print(msg + (f": {', '.join(names)}" if names else ""))
        st = v.status()
        print(f"Vault ready at {st['path']} ({st['notes']} notes, {st['facts']} facts).")
        if args.path:
            print(f'To keep using it, set in ~/.config/batsy/config.toml:\n[vault]\npath = "{args.path}"')
        print("Open it in Obsidian: 'Open folder as vault', or run: batsy vault open")
        if not st["obsidian_cli"]:
            print("Optional: turn on the Obsidian CLI (Settings → General → Command line interface) for Claude Code "
                  "to rename and move notes with links kept intact.")
    elif act == "status":
        st = v.status()
        print(f"{st['path']}\n{st['notes']} notes, {st['facts']} facts: "
              + ", ".join(f"{n} {k}" for k, n in sorted(st["kinds"].items())))
        print("skills: " + (", ".join(st["skills"]) or "none (run: batsy vault init)"))
        print("obsidian CLI: " + ("found" if st["obsidian_cli"] else "not found (optional)"))
        if st["orphans"]:
            print("notes with no links: " + ", ".join(st["orphans"][:10]))
    elif act == "search":
        for h in v.search(text, limit=8, include_daily=True):
            print(f"{h.score:5.1f}  {h.note.title} ({h.note.kind})" + (f"  <- {h.via}" if h.via else ""))
    elif act == "remember":
        if not text:
            sys.exit('Usage: batsy vault remember "fact" [--about "Note"] [--kind person|project|place|setup|preference|topic]')
        print(v.remember(text, about=args.about, kind=args.kind)[1])
    elif act == "forget":
        print(f"Forgotten (from {w})." if (w := v.forget(text)) else "No fact with that id.")
    elif act == "read":
        print(v.read(text) or "No such note.")
    elif act == "link":
        if len(args.text) != 2:
            sys.exit('Usage: batsy vault link "Note A" "Note B"  (quote each name)')
        print(v.link(*args.text))
    elif act == "open":
        v.open_in_obsidian()


def cmd_projects(cfg, args) -> None:
    from batsy import projects

    if args.scan or args.find or args.update:
        idx = projects.build_index(cfg) if args.scan else projects.index(cfg)
        if args.scan:
            kinds: dict[str, int] = {}
            for c in idx["candidates"]:
                kinds[c["source"]] = kinds.get(c["source"], 0) + 1
            print(f"GitHub user: {idx['user'] or '(unknown: set [github] user)'}; "
                  f"{len(idx['candidates'])} projects ({', '.join(f'{v} {k}' for k, v in kinds.items())})")
    if args.find:
        r = projects.identify(args.find, cfg)
        print(("Best: " if r["confident"] else "Unsure; best guess: ") + (projects.label(r["best"]) if r["best"] else "none"))
        for o in r["options"][1:]:
            print("   or:", projects.label(o))
        return
    if args.update:
        store = projects.Store()
        p = store.find(args.update)
        if p is None:
            r = projects.identify(args.update, cfg)
            p = dict(r["best"], title=r["best"]["name"]) if r["best"] else None
        if p is None:
            print("No project like that.")
            return
        spoken, card = projects.summary(p, projects.activity(p))
        print(spoken + "\n\n" + card)
        return
    rows = projects.Store().all()
    if not rows:
        print('No tracked projects yet. Say "Batsy, I\'m doing a project on ..."')
    for p in rows:
        print(f"{p['title']:32} {p.get('repo') or '-':40} {projects.short_path(p['path']) if p.get('path') else '-'}")


def cmd_spotify_login(cfg, args) -> None:
    from batsy.spotify import Spotify, login

    login(cfg)
    devs = Spotify(cfg).devices()
    print("Spotify devices online now:", ", ".join(d["name"] for d in devs) or "none (open Spotify on the iPad)")


def cmd_doctor(cfg, args) -> None:
    def check(label, ok, hint=""):
        print(f"  [{'ok' if ok else '!!'}] {label}" + ("" if ok else f"  -> {hint}"))

    print("Batsy doctor")
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(cfg.get("local.url") + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
        check(f"local model {cfg.get('local.model')} (Ollama)", any(n.startswith(cfg.get("local.model")) for n in names),
              f"ollama pull {cfg.get('local.model')}")
    except Exception:
        check("Ollama running", False, "sudo systemctl enable --now ollama")
    from batsy import codex_adapter

    ok, detail = codex_adapter.login_status()
    check("Codex CLI", codex_adapter.available(), "install the Codex CLI")
    check("Codex ChatGPT login", ok, detail or "run: codex login")
    try:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        check(f"microphone: {dev['name']}", True)
    except Exception as e:
        check("microphone", False, f"{e} (dnf install portaudio; pip install sounddevice)")
    vosk_dir = MODELS_DIR / cfg.get("voice.vosk_model")
    check(f"wake word model {vosk_dir.name}", vosk_dir.exists(), "run install.sh")
    from batsy import inbox

    ok, fix = inbox.check()
    check("Tailscale file sharing (send files to and from this laptop)", ok, fix)
    try:
        import faster_whisper  # noqa: F401
        check("faster-whisper speech recognition", True)
    except Exception:
        check("faster-whisper", False, "pip install faster-whisper (falls back to Vosk)")
    voice = MODELS_DIR / "piper" / f"{cfg.get('voice.piper_voice')}.onnx"
    check(f"piper voice {voice.name}", voice.exists(), "run install.sh (falls back to espeak-ng)")
    for tool, hint in [("arduino-cli", "install.sh installs it to ~/.local/bin"),
                       ("espeak-ng", "dnf install espeak-ng"), ("pw-play", "dnf install pipewire-utils"),
                       ("notify-send", "dnf install libnotify"), ("gnome-screenshot", "dnf install gnome-screenshot"),
                       ("ffmpeg", "dnf install ffmpeg-free"), ("ydotool", "dnf install ydotool (optional)")]:
        check(tool, bool(shutil.which(tool) or os.path.exists(os.path.expanduser(f"~/.local/bin/{tool}"))), hint)
    from batsy.spotify import Spotify, SpotifyError

    sp = Spotify(cfg)
    if not sp.ready():
        check("Spotify", False, "set [spotify] client_id in config.toml, then run: batsy spotify-login")
    else:
        try:
            devs = sp.devices()
            check(f"Spotify devices: {', '.join(d['name'] for d in devs) or 'none online'}", bool(devs),
                  "open Spotify on the iPad")
        except SpotifyError as e:
            check("Spotify", False, str(e))
    try:
        import cv2  # noqa: F401
        check("opencv (camera window)", True)
    except Exception:
        check("opencv (camera window)", False, "pip install opencv-python")
    from batsy.camera_view import find_camera, list_cameras

    cams = list_cameras()
    check(f"cameras: {'; '.join(cams) or 'none'} -> using /dev/video{find_camera(cfg.get('camera.name'))}", bool(cams),
          "plug in the webcam")
    try:
        import grp

        check("serial access (dialout group)", "dialout" in [grp.getgrgid(g).gr_name for g in os.getgroups()],
              "sudo usermod -aG dialout $USER, then log out and back in")
    except KeyError:
        pass


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "3d":  # `batsy 3d ...`: drive the 3D viewer (used by Codex)
        import json

        from batsy.model3d import VIEWER, find_model

        Config.load()
        args = sys.argv[2:] or ["info"]
        if args[0] == "open":
            path = Path(args[1]).expanduser() if len(args) > 1 else find_model("this model")
            reply = VIEWER.open(path) if path else {"error": "no model found"}
        elif args[0] == "cmd":
            reply = VIEWER.send(json.loads(args[1]))
        else:
            reply = VIEWER.send({"cmd": args[0]})
        print(json.dumps(reply, indent=1))
        if args[0] == "open" and VIEWER.proc:  # started from a terminal: keep the window until it's closed
            VIEWER.proc.wait()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":  # `batsy selftest [--gui --voice --codex --arduino --all]`
        from batsy import selftest

        selftest.main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "saver":  # `batsy saver get|set|preview`
        from batsy import saver

        Config.load()
        saver.cli(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "doc":  # `batsy doc ...`: document tools (used by Codex)
        from batsy import docs

        Config.load()
        docs.cli(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="batsy", description="Batsy voice assistant")
    parser.add_argument("-v", "--verbose", action="store_true", help="log to the terminal too")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="voice assistant (default)")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("chat", help="type to Batsy")
    p.add_argument("--speak", action="store_true", help="also speak replies aloud")
    p = sub.add_parser("ask", help="one-off request")
    p.add_argument("text", nargs="+")
    p.add_argument("--speak", action="store_true")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("reset", help="start a fresh conversation")
    sub.add_parser("stop", help="stop the conversation now (bind it to a keyboard shortcut)")
    p = sub.add_parser("mode", help="switch between voice and chat mode")
    p.add_argument("mode", choices=["voice", "chat", "toggle", "status"])
    sub.add_parser("listen", help="listen now without the wake word (push-to-talk; bind it to a shortcut)")
    sub.add_parser("usage", help="show token usage")
    sub.add_parser("spotify-login", help="connect your Spotify account")
    p = sub.add_parser("skills", help="list (or --forget) skills Batsy learned")
    p.add_argument("--forget", metavar="NAME")
    p = sub.add_parser("vocab", help="list (or --forget) the phrases Batsy knows for showing things")
    p.add_argument("--forget", metavar="PHRASE")
    p = sub.add_parser("vault", help="the Obsidian memory vault: init, status, search, remember, forget, read, link, open")
    p.add_argument("action", choices=["init", "status", "search", "remember", "forget", "read", "link", "open"])
    p.add_argument("text", nargs="*", help="fact, search words, note title or fact id (link takes two note names)")
    p.add_argument("--about", metavar="NOTE", help="remember: the note the fact belongs to (default: Me)")
    p.add_argument("--kind", metavar="KIND", help="remember: type of a new note (person, project, place, setup, preference, topic)")
    p.add_argument("--path", metavar="DIR", help="use this vault folder (e.g. an existing Obsidian vault)")
    p.add_argument("--no-skills", action="store_true", help="init: don't install obsidian-skills")
    p = sub.add_parser("projects", help="your tracked projects; --scan GitHub + folders, --find TEXT, --update NAME")
    p.add_argument("--scan", action="store_true", help="rebuild the index of your GitHub repos and project folders")
    p.add_argument("--find", metavar="TEXT", help="which project a description matches")
    p.add_argument("--update", metavar="NAME", help="what's happened on a project this week")
    args = parser.parse_args()

    cfg = Config.load()
    _setup_logging(args.verbose or args.cmd in (None, "run") and sys.stderr.isatty())
    {"run": cmd_run, None: cmd_run, "chat": cmd_chat, "ask": cmd_ask,
     "doctor": cmd_doctor, "reset": cmd_reset, "usage": cmd_usage,
     "spotify-login": cmd_spotify_login, "vocab": cmd_vocab, "skills": cmd_skills,
     "projects": cmd_projects, "stop": cmd_stop, "mode": cmd_mode, "listen": cmd_listen, "vault": cmd_vault}[args.cmd](cfg, args)
