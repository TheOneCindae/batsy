"""Run OpenAI Codex CLI requests without embedding credentials in Batsy."""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from batsy import activity
from batsy.config import DATA_DIR, WORKSPACE

log = logging.getLogger("batsy.codex")
USAGE_FILE = DATA_DIR / "usage.jsonl"
_running: dict[subprocess.Popen, str] = {}
_lock = threading.Lock()
CFG = None
announce = None


def available() -> bool:
    return bool(shutil.which("codex"))


def login_status(timeout: int = 10) -> tuple[bool, str]:
    """Return whether the installed CLI has a usable ChatGPT login."""
    exe = shutil.which("codex")
    if not exe:
        return False, "Codex CLI is not installed"
    try:
        result = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    message = (result.stdout or result.stderr).strip()
    return result.returncode == 0, message or "Codex login status is unavailable"


def _rows() -> list[dict]:
    if not USAGE_FILE.exists():
        return []
    rows = []
    for line in USAGE_FILE.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("model", "")).startswith("codex"):
            rows.append(row)
    return rows


def _tokens(row: dict) -> int:
    return sum(int(row.get(key, 0) or 0) for key in ("in", "out", "cache_read", "cache_write"))


def limits(cfg) -> dict:
    result = dict(cfg.get("codex_budget")) if cfg is not None else {
        "session_hours": 5, "session_tokens": 1_500_000, "weekly_tokens": 10_000_000, "warn_at": 0.8
    }
    path = DATA_DIR / "codex_limits.json"
    if path.exists():
        try:
            result.update(json.loads(path.read_text()))
        except (OSError, ValueError):
            log.warning("Ignoring invalid Codex limits file: %s", path)
    return result


def budget(cfg) -> dict:
    lim = limits(cfg)
    rows = _rows()
    now = time.time()
    session_start = max((row["t"] for row in rows if now - row["t"] < lim["session_hours"] * 3600), default=None)
    session_used = sum(_tokens(row) for row in rows if session_start is not None and row["t"] >= session_start)
    local = time.localtime(now)
    week_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday - local.tm_wday, 0, 0, 0, 0, 0, -1))
    week_used = sum(_tokens(row) for row in rows if row["t"] >= week_start)

    def pack(used: int, limit: int, reset: float | None) -> dict:
        return {"used": used, "limit": limit, "resets_at": reset,
                "pct": round(100 * used / limit, 1) if limit else None}

    return {
        "session": pack(session_used, lim["session_tokens"],
                        session_start + lim["session_hours"] * 3600 if session_start else None),
        "week": pack(week_used, lim["weekly_tokens"], week_start + 7 * 86400),
        "requests_week": sum(1 for row in rows if row["t"] >= week_start),
    }


def over_budget(cfg) -> str | None:
    result = budget(cfg)
    for key, label in (("session", "this session's"), ("week", "this week's")):
        item = result[key]
        if item["limit"] and item["used"] >= item["limit"]:
            reset = time.strftime("%-I:%M %p" if key == "session" else "%A",
                                  time.localtime(item["resets_at"]))
            return f"I've used {label} Codex budget, so I'll use the local model until {reset}."
    return None


def set_limit(which: str, tokens: int) -> None:
    """Compatibility hook for the existing voice budget command."""
    path = DATA_DIR / "codex_limits.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[f"{which}_tokens"] = int(tokens)
    path.write_text(json.dumps(data))


def _start(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    with _lock:
        _running[proc] = threading.current_thread().name
    return proc


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def cancel(thread: str = "turn") -> int:
    with _lock:
        processes = [proc for proc, owner in _running.items() if owner == thread and proc.poll() is None]
    for proc in processes:
        _kill(proc)
    return len(processes)


def _model(cfg) -> str | None:
    return (cfg.get("codex.model") if cfg is not None else None) or None


def _record(label: str, elapsed: float, output: str) -> None:
    row = {"t": int(time.time()), "model": "codex", "label": label, "in": 0, "out": 0,
           "cache_read": 0, "cache_write": 0, "secs": round(elapsed, 1), "chars": len(output)}
    with _lock, open(USAGE_FILE, "a") as stream:
        stream.write(json.dumps(row) + "\n")
    log.info("codex %s: %s", label, row)


def run(prompt: str, label: str, args: list[str] | None = None, cwd=WORKSPACE, timeout: int = 300,
        force: bool = False, cfg=None, on_text=None) -> str:
    """Run `codex exec` in the requested project directory and return its final response."""
    cfg = cfg or CFG
    exe = shutil.which("codex")
    if not exe:
        return "Codex CLI isn't installed. Install it from https://chatgpt.com/codex."
    if not force and cfg is not None:
        reason = over_budget(cfg)
        if reason:
            return reason
    cwd = Path(cwd).expanduser()
    cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PATH=f"{Path(os.sys.executable).parent}:{Path.home() / '.local/bin'}:"
                                f"{os.environ.get('PATH', '')}")
    command = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "--approve-for-me",
               "--color", "never", "--output-last-message"]
    with tempfile.NamedTemporaryFile(prefix="batsy-codex-", suffix=".txt", delete=False) as output_file:
        output_path = Path(output_file.name)
    command.append(str(output_path))
    model = _model(cfg)
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    started = time.monotonic()
    with activity.agent(label):
        proc = _start(command, cwd, env)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            stdout, stderr = proc.communicate()
            return "That took too long."
        finally:
            with _lock:
                _running.pop(proc, None)
    try:
        output = output_path.read_text().strip() if output_path.exists() else ""
    finally:
        output_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = (stderr or stdout).strip()[-500:]
        return detail or "Codex did not answer."
    if not output:
        output = (stdout or "").strip()
    _record(label, time.monotonic() - started, output)
    if on_text:
        on_text(output)
    return output[:3000] or "Codex did not answer."
