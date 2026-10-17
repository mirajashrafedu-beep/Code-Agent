# run_agent.py
"""
Orchestrator for AgenticCoder: run tasks, grade them, repair them, compare models, remember results.

Commands:  run | bench | interactive | history | presets | doctor | selftest
Everything generic (sandbox, backends, syntax/manifest/HTML checks) lives in agent.py and is imported,
never re-implemented. This file adds: declarative acceptance rules, presets, scoring, repair attempts
that resume the agent's conversation, a model pool, a SQLite run history, reports, and a REPL.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import importlib.metadata
import json
import logging
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from agent import (
    META_DIR, AgentConfig, AgenticCoder, Backend, Check, CommandRunner, Events,
    ScriptedBackend, ToolError, Workspace, make_backend,
)

DEFAULT_MODEL = "DuoNeural/LFM2.5-8B-A1B-Hermes-Agentic-Coder-Abliterated-v2"
SUBCOMMANDS = {"run", "bench", "interactive", "history", "presets", "doctor", "selftest"}
log = logging.getLogger("run_agent")

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-").lower()[:60] or "x"


def human(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"


def torch_module():
    """torch is optional: only local Hugging Face models need it."""
    try:
        import torch

        return torch
    except Exception:
        return None


def free_gpu_memory() -> None:
    gc.collect()
    torch = torch_module()
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def workspace_files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*")
        if p.is_file() and META_DIR not in p.relative_to(root).parts and ".git" not in p.parts and "node_modules" not in p.parts
    )


# ---------------------------------------------------------------------------
# Acceptance rules: declarative, JSON-serializable, so presets can be plain data
# ---------------------------------------------------------------------------


@dataclass
class RuleContext:
    ws: Workspace
    runner: CommandRunner


RULES: dict[str, Callable[..., Awaitable[tuple[bool, str]]]] = {}


def rule(kind: str):
    def register(fn):
        RULES[kind] = fn
        return fn

    return register


def _read(ctx: RuleContext, path: str) -> str | None:
    try:
        p = ctx.ws.safe(path)
    except ToolError:
        return None
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def _dig(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj.get(part) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj


def _load_json(ctx: RuleContext, path: str) -> Any:
    text = _read(ctx, path)
    if text is None:
        raise ValueError(f"{path} missing")
    return json.loads(text)


@rule("exists")
async def _exists(ctx: RuleContext, path: str, min_bytes: int = 20) -> tuple[bool, str]:
    p = ctx.ws.safe(path)
    if not p.is_file():
        return False, "missing"
    return p.stat().st_size >= min_bytes, f"{p.stat().st_size} bytes"


@rule("contains")
async def _contains(ctx: RuleContext, path: str, pattern: str, regex: bool = False, absent: bool = False) -> tuple[bool, str]:
    text = _read(ctx, path)
    if text is None:
        return False, f"{path} missing"
    found = bool(re.search(pattern, text)) if regex else pattern in text
    return found != absent, ("pattern present" if found else "pattern absent")


@rule("json_equals")
async def _json_equals(ctx: RuleContext, path: str, key: str, value: Any) -> tuple[bool, str]:
    actual = _dig(_load_json(ctx, path), key)
    return actual == value, f"{key}={actual!r}"


@rule("json_has")
async def _json_has(ctx: RuleContext, path: str, key: str, items: list[Any]) -> tuple[bool, str]:
    actual = _dig(_load_json(ctx, path), key) or []
    missing = [i for i in items if i not in actual]
    return not missing, f"missing {missing}" if missing else "all present"


@rule("max_lines")
async def _max_lines(ctx: RuleContext, path: str, limit: int) -> tuple[bool, str]:
    text = _read(ctx, path)
    n = len(text.splitlines()) if text is not None else -1
    return 0 <= n <= limit, f"{n} lines (limit {limit})"


@rule("any_files")
async def _any_files(ctx: RuleContext, glob: str, min_count: int = 1) -> tuple[bool, str]:
    found = [p for p in ctx.ws.root.rglob(glob) if p.is_file() and META_DIR not in p.parts]
    return len(found) >= min_count, f"{len(found)} match(es)"


@rule("command")
async def _command(ctx: RuleContext, cmd: str, expect_exit: int = 0, expect_stdout: str = "", timeout: int = 30) -> tuple[bool, str]:
    code, text = await ctx.runner.run(cmd, timeout)
    body = text.split("\n", 1)[1] if "\n" in text else ""
    if code != expect_exit:
        return False, f"exit {code}: {body[:200]}"
    if expect_stdout and not re.search(expect_stdout, body, re.M):
        return False, f"output did not match /{expect_stdout}/: {body[:160]}"
    return True, "ok"


# Compact constructors so presets read like specifications.
def exists(path: str, **kw: Any) -> dict[str, Any]: return {"kind": "exists", "path": path, **kw}
def has(path: str, pattern: str, **kw: Any) -> dict[str, Any]: return {"kind": "contains", "path": path, "pattern": pattern, **kw}
def lacks(path: str, pattern: str, **kw: Any) -> dict[str, Any]: return {"kind": "contains", "path": path, "pattern": pattern, "absent": True, **kw}
def jeq(path: str, key: str, value: Any, **kw: Any) -> dict[str, Any]: return {"kind": "json_equals", "path": path, "key": key, "value": value, **kw}
def jhas(path: str, key: str, items: list[Any], **kw: Any) -> dict[str, Any]: return {"kind": "json_has", "path": path, "key": key, "items": items, **kw}
def cmd(command: str, **kw: Any) -> dict[str, Any]: return {"kind": "command", "cmd": command, **kw}


def rule_label(spec: dict[str, Any]) -> str:
    if spec.get("label"):
        return spec["label"]
    target = spec.get("path") or spec.get("cmd") or spec.get("glob", "")
    extra = spec.get("pattern") or spec.get("key") or ""
    prefix = "no " if spec.get("absent") else ""
    return f"{spec['kind']}:{target}" + (f":{prefix}{extra}" if extra else "")


async def run_rules(ctx: RuleContext, specs: list[dict[str, Any]]) -> list[Check]:
    checks: list[Check] = []
    for spec in specs:
        args = {k: v for k, v in spec.items() if k not in {"kind", "label"}}
        fn = RULES.get(spec.get("kind", ""))
        if fn is None:
            checks.append(Check(rule_label(spec), False, f"unknown rule kind {spec.get('kind')!r}"))
            continue
        try:
            ok, detail = await fn(ctx, **args)
        except (ToolError, ValueError, TypeError, KeyError) as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks.append(Check(rule_label(spec), ok, detail))
    return checks


# ---------------------------------------------------------------------------
# Presets: a task plus the rules that define "done"
# ---------------------------------------------------------------------------


@dataclass
class Preset:
    name: str
    task: str
    rules: list[dict[str, Any]] = field(default_factory=list)
    tags: tuple[str, ...] = ()
    difficulty: str = "medium"

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "Preset":
        return cls(name, d["task"], list(d.get("rules", [])), tuple(d.get("tags", ())), d.get("difficulty", "medium"))


FINISH_NOTE = " Write all files into the workspace directory, then verify them."

BUILTIN_PRESETS: list[Preset] = [
    Preset(
        "tab-timer",
        "Create a complete Manifest V3 Chrome Extension that tracks the user's active tab time per website. "
        "Requirements: 1) manifest.json with manifest_version 3, minimal permissions (tabs, storage, idle, alarms), "
        "a service worker background, and a popup action. 2) background.js service worker that detects the active tab "
        "(tabs.onActivated, tabs.onUpdated, windows.onFocusChanged), pauses when the browser loses focus or the user is idle, "
        "and saves seconds per hostname in chrome.storage.local so data survives service-worker restarts. "
        "3) popup.html with a clean, readable layout, an empty state, and a reset button. "
        "4) popup.js that reads storage, sorts sites by time, formats durations as 1h 2m 3s, refreshes live, and never uses "
        "innerHTML with untrusted data. No inline scripts, no remote code, no placeholders." + FINISH_NOTE,
        [
            *[exists(f) for f in ("manifest.json", "background.js", "popup.html", "popup.js")],
            jeq("manifest.json", "manifest_version", 3),
            jhas("manifest.json", "permissions", ["tabs", "storage"]),
            has("background.js", "chrome.storage.local", label="background persists to storage"),
            has("background.js", "tabs.onActivated"),
            has("background.js", "windows.onFocusChanged"),
            lacks("background.js", r"\bdocument\.|\bwindow\.", regex=True, label="background has no DOM access"),
            lacks("popup.html", r"<script(?![^>]*\bsrc=)", regex=True, label="popup.html has no inline scripts"),
            has("popup.js", "textContent", label="popup renders with textContent"),
            lacks("popup.js", r"innerHTML\s*=|eval\(", regex=True, label="popup avoids innerHTML and eval"),
            cmd("node --check background.js"), cmd("node --check popup.js"),
        ],
        ("browser", "javascript"), "hard",
    ),
    Preset(
        "todo-api",
        "Create a Node.js REST API for a todo app using only the standard library (http module). Files: server.js, store.js, "
        "test.js. Support GET/POST/PUT/DELETE /todos, JSON validation, proper status codes, and persistence to data.json. "
        "test.js must exercise every route and exit non-zero on failure." + FINISH_NOTE,
        [
            *[exists(f) for f in ("server.js", "store.js", "test.js")],
            has("server.js", "createServer"), has("server.js", r"40[04]", regex=True, label="server returns 400/404"),
            cmd("node --check server.js"), cmd("node --check store.js"), cmd("node test.js", timeout=60),
        ],
        ("node", "api"), "medium",
    ),
    Preset(
        "snake-game",
        "Build a polished snake game as ONE self-contained index.html file with inline CSS and JavaScript: canvas rendering, "
        "requestAnimationFrame loop, score and high score saved in localStorage (inside try/catch), keyboard and touch controls, "
        "pause and restart, and a game-over screen." + FINISH_NOTE,
        [
            exists("index.html", min_bytes=1500), has("index.html", "requestAnimationFrame"), has("index.html", "localStorage"),
            has("index.html", "keydown"), has("index.html", "touchstart"),
            lacks("index.html", r"eval\(|<script[^>]+src=[\"']https?:", regex=True, label="no eval or remote scripts"),
        ],
        ("browser", "game"), "medium",
    ),
    Preset(
        "wordcount-cli",
        "Create a Python command-line tool wc.py (standard library only) that counts lines, words, and characters of files or stdin, "
        "with --lines/--words/--chars flags, --json output, proper exit codes, and a helpful --help. Also write test_wc.py using unittest "
        "that covers empty input, unicode, and missing files." + FINISH_NOTE,
        [
            exists("wc.py"), exists("test_wc.py"), has("wc.py", "argparse"),
            cmd("python wc.py --help", expect_stdout="(?i)usage"), cmd("python -m unittest -q", timeout=60),
        ],
        ("python", "cli"), "easy",
    ),
    Preset(
        "notes-api",
        "Create a FastAPI app in main.py for notes with POST /notes, GET /notes, GET /notes/{id}, DELETE /notes/{id}. Use pydantic "
        "models, correct status codes (201, 404), and an in-memory store guarded by a lock. Include requirements.txt." + FINISH_NOTE,
        [
            exists("main.py"), exists("requirements.txt", min_bytes=5), has("main.py", "FastAPI"), has("main.py", "HTTPException"),
            has("main.py", r"status_code\s*=\s*201", regex=True, label="create returns 201"), cmd("python -m py_compile main.py"),
        ],
        ("python", "api"), "medium",
    ),
    Preset(
        "sqlite-inventory",
        "Create inventory.py (standard library only): an SQLite-backed inventory with add, remove, adjust-stock, and low-stock report "
        "functions. Use parameterized queries only, enable foreign keys, wrap writes in transactions, and finish with a __main__ demo "
        "that creates a temporary database, exercises every function, and prints the report." + FINISH_NOTE,
        [
            exists("inventory.py"), has("inventory.py", "sqlite3"), lacks("inventory.py", r"execute\(\s*f[\"']", regex=True, label="no f-string SQL"),
            has("inventory.py", "foreign_keys"), cmd("python inventory.py", timeout=60),
        ],
        ("python", "database"), "medium",
    ),
    Preset(
        "md-to-html",
        "Create converter.py (standard library only) that converts a Markdown subset (headings, bold, italic, inline code, fenced code "
        "blocks, links, unordered lists) to safe HTML with proper escaping, plus test_converter.py using unittest, including an XSS case." + FINISH_NOTE,
        [exists("converter.py"), exists("test_converter.py"), has("converter.py", "html"), cmd("python -m unittest -q", timeout=60)],
        ("python", "text"), "medium",
    ),
]


def load_presets(extra_files: list[str]) -> dict[str, Preset]:
    presets = {p.name: p for p in BUILTIN_PRESETS}
    for file in extra_files:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
        items = data.items() if isinstance(data, dict) else ((d["name"], d) for d in data)
        presets.update({name: Preset.from_dict(name, d) for name, d in items})
    return presets


# ---------------------------------------------------------------------------
# Environment diagnostics
# ---------------------------------------------------------------------------


def _version(cmdline: list[str]) -> str | None:
    if shutil.which(cmdline[0]) is None:
        return None
    try:
        return subprocess.run(cmdline, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _package(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def environment() -> dict[str, Any]:
    torch = torch_module()
    gpus = []
    if torch is not None and torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(i)
            gpus.append({"name": torch.cuda.get_device_name(i), "total_gb": round(total / 1e9, 1), "free_gb": round(free / 1e9, 1)})
    ram = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        m = re.search(r"MemTotal:\s+(\d+) kB", meminfo.read_text())
        ram = round(int(m.group(1)) / 1e6, 1) if m else None
    return {
        "python": platform.python_version(), "platform": platform.platform(), "node": _version(["node", "--version"]),
        "torch": getattr(torch, "__version__", None), "transformers": _package("transformers"), "ollama": _package("ollama"),
        "gpus": gpus, "ram_gb": ram, "disk_free_gb": round(shutil.disk_usage(".").free / 1e9, 1),
        "agent_prompt": Path(__file__).with_name("agent_prompt.py").is_file(),
    }


def estimate_model_gb(model: str) -> float | None:
    """Weights in 16-bit: parameters (billions, from the name) x 2 bytes. Remote backends need no local memory."""
    if model.startswith(("ollama:", "openai:")):
        return None
    sizes = [float(s) for s in re.findall(r"(\d+(?:\.\d+)?)\s*[bB](?![A-Za-z])", model)]
    return max(sizes) * 2 if sizes else None


def preflight(model: str, env: dict[str, Any]) -> list[str]:
    warnings = []
    need = estimate_model_gb(model)
    total = sum(g["total_gb"] for g in env["gpus"])
    if need and not env["gpus"]:
        warnings.append(f"no GPU found but the model needs about {need:.0f} GB; generation on CPU will be very slow")
    elif need and need > total * 0.9:
        warnings.append(f"model needs about {need:.0f} GB but GPUs total {total:.0f} GB; expect out-of-memory (try a smaller or quantized model)")
    if env["node"] is None:
        warnings.append("node is not installed: JavaScript syntax checks and Node presets will fail")
    if env["disk_free_gb"] < 5:
        warnings.append(f"only {env['disk_free_gb']} GB of disk free")
    if sys.version_info < (3, 10):
        warnings.append("Python 3.10 or newer is required")
    return warnings


# ---------------------------------------------------------------------------
# Results, scoring, and run history
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    run_id: str
    model: str
    preset: str
    workspace: str
    task: str
    attempts: int = 0
    steps: int = 0
    finished: bool = False
    stop_reason: str = ""
    summary: str = ""
    duration_s: float = 0.0
    tokens: int = 0
    files: dict[str, int] = field(default_factory=dict)
    changes: dict[str, list[str]] = field(default_factory=dict)
    rule_checks: list[dict[str, Any]] = field(default_factory=list)
    verify_checks: list[dict[str, Any]] = field(default_factory=list)
    score: int = 0
    ok: bool = False


def _fraction(checks: list[Any]) -> float:
    passed = [c["ok"] if isinstance(c, dict) else c.ok for c in checks]
    return sum(passed) / len(passed) if passed else 1.0


def compute_score(finished: bool, rules: list[Check], verify: list[Check], steps: int, max_steps: int) -> int:
    """0-100: rules 55%, finishing 20%, static verification 15%, step efficiency 10%."""
    efficiency = max(0.0, 1 - steps / max(max_steps, 1))
    return round(100 * (0.55 * _fraction(rules) + 0.20 * finished + 0.15 * _fraction(verify) + 0.10 * efficiency))


class History:
    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, ts TEXT, run_id TEXT, model TEXT, preset TEXT, ok INTEGER, "
            "score INTEGER, steps INTEGER, attempts INTEGER, duration REAL, tokens INTEGER, stop_reason TEXT, workspace TEXT)"
        )
        self.db.commit()

    def add(self, r: RunResult) -> None:
        self.db.execute(
            "INSERT INTO runs (ts, run_id, model, preset, ok, score, steps, attempts, duration, tokens, stop_reason, workspace) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), r.run_id, r.model, r.preset, int(r.ok), r.score, r.steps, r.attempts,
             r.duration_s, r.tokens, r.stop_reason, r.workspace),
        )
        self.db.commit()

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def leaderboard(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT model, COUNT(*) AS runs, ROUND(AVG(score),1) AS avg_score, ROUND(100.0*AVG(ok),0) AS pass_pct, "
            "ROUND(AVG(duration),1) AS avg_s, ROUND(AVG(steps),1) AS avg_steps FROM runs GROUP BY model ORDER BY avg_score DESC"
        ).fetchall()


# ---------------------------------------------------------------------------
# Execution: attempts, repair by resuming the agent's conversation, grading
# ---------------------------------------------------------------------------


@dataclass
class Options:
    max_steps: int = 30
    max_new_tokens: int = 3072
    temperature: float = 0.2
    context_tokens: int = 7000
    cmd_timeout: int = 30
    timeout: float = 3600.0
    attempts: int = 2
    repair_steps: int = 12
    clean: bool = False
    verbose_agent: bool = True
    stream: bool = True
    base_url: str = ""
    api_key: str = ""

    @classmethod
    def from_args(cls, a: argparse.Namespace) -> "Options":
        attempts = a.retries + 1 if getattr(a, "retries", None) is not None else a.attempts
        return cls(
            max_steps=a.max_steps, max_new_tokens=a.max_new_tokens, temperature=a.temperature, context_tokens=a.context_tokens,
            cmd_timeout=a.cmd_timeout, timeout=a.timeout, attempts=max(1, attempts), repair_steps=a.repair_steps,
            clean=getattr(a, "clean", False), verbose_agent=not a.quiet, stream=not a.no_stream and not a.quiet,
            base_url=a.base_url, api_key=a.api_key,
        )


@dataclass
class RunSpec:
    model: str
    preset: Preset
    workspace: Path
    run_id: str


class BackendPool:
    """Keeps one model loaded at a time and frees GPU memory when the model changes."""

    def __init__(self, opts: Options, factory: Callable[[AgentConfig], Backend] = make_backend) -> None:
        self.opts, self.factory, self.model, self.backend = opts, factory, None, None

    async def get(self, model: str) -> Backend:
        if self.backend is not None and self.model == model:
            return self.backend
        await self.release()
        cfg = AgentConfig(model_name=model, base_url=self.opts.base_url, api_key=self.opts.api_key, context_tokens=self.opts.context_tokens)
        log.info("Loading model %s", model)
        self.backend = await asyncio.to_thread(self.factory, cfg)
        self.model = model
        return self.backend

    async def release(self) -> None:
        if self.backend is not None:
            await self.backend.aclose()
        self.backend = self.model = None
        free_gpu_memory()


def add_followup(workspace: Path, text: str) -> bool:
    """Append review feedback to the agent's saved conversation so the next run resumes with it."""
    path = workspace / META_DIR / "state.json"
    if not path.is_file():
        return False
    saved = json.loads(path.read_text(encoding="utf-8"))
    msgs = saved["messages"]
    if msgs and msgs[-1]["role"] == "user":  # keep roles alternating for strict chat templates
        msgs[-1]["content"] += "\n\n" + text
    else:
        msgs.append({"role": "user", "content": text})
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return True


def build_hint(finished: bool, stop_reason: str, failed: list[Check]) -> str:
    lines = ["[acceptance review] Your work was checked and is NOT complete."]
    if not finished:
        lines.append(f"- The run stopped early ({stop_reason}); continue from where you left off.")
    lines += [f"- {c.name}: {c.detail}" for c in failed[:10]]
    lines.append("Fix these with targeted edits, re-run the relevant checks, then call finish.")
    return "\n".join(lines)


async def execute(spec: RunSpec, backend: Backend, opts: Options, events: Events | None = None) -> RunResult:
    if opts.clean and spec.workspace.exists():
        shutil.rmtree(spec.workspace)
    agent = AgenticCoder(
        spec.model, str(spec.workspace), opts.max_steps, opts.max_new_tokens, opts.temperature, opts.cmd_timeout,
        backend=backend, events=events, timeout_s=opts.timeout, context_tokens=opts.context_tokens,
        verbose=opts.verbose_agent, stream=opts.stream,
    )
    (agent.ws.meta / "state.json").unlink(missing_ok=True)  # never resume a conversation from an older run
    ctx = RuleContext(agent.ws, agent.runner)
    result = RunResult(spec.run_id, spec.model, spec.preset.name, str(agent.ws.root), spec.preset.task)
    task, resume, started = spec.preset.task, False, time.monotonic()

    for attempt in range(1, opts.attempts + 1):
        result.attempts = attempt
        state = await agent.run(task, resume=resume)
        result.tokens += state.metrics["tokens_out"]
        rule_checks = await run_rules(ctx, spec.preset.rules)
        failed = [c for c in rule_checks if not c.ok]
        if (state.finished and not failed) or attempt == opts.attempts:
            break
        if "out of memory" in state.stop_reason.lower():
            opts_note = "GPU out of memory: halving max_new_tokens and trimming context for the next attempt"
            log.warning(opts_note)
            agent.cfg.max_new_tokens = max(256, agent.cfg.max_new_tokens // 2)
            agent.cfg.context_tokens = int(agent.cfg.context_tokens * 0.75)
            free_gpu_memory()
        hint = build_hint(state.finished, state.stop_reason, failed)
        resume = add_followup(agent.ws.root, hint)
        if resume:
            agent.cfg.max_steps = state.steps + opts.repair_steps
        else:
            task, agent.cfg.max_steps = f"{spec.preset.task}\n\n{hint}", opts.max_steps
        log.info("Attempt %d incomplete (%d rule(s) failing); %s", attempt, len(failed), "resuming with feedback" if resume else "restarting with feedback")

    files = [f for f in workspace_files(agent.ws.root)]
    verify_checks = await agent.verifier.run(files)
    result.steps, result.finished, result.stop_reason, result.summary = state.steps, state.finished, state.stop_reason, state.summary
    result.files, result.changes = dict(state.written), agent.ws.changes()
    result.rule_checks = [asdict(c) for c in rule_checks]
    result.verify_checks = [asdict(c) for c in verify_checks]
    result.duration_s = round(time.monotonic() - started, 1)
    result.score = compute_score(state.finished, rule_checks, verify_checks, state.steps, opts.max_steps)
    result.ok = state.finished and _fraction(rule_checks) == 1.0 and _fraction(verify_checks) == 1.0
    (agent.ws.meta / "result.json").write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_table(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()  # noqa: E731
    return "\n".join([line(headers), line(["-" * w for w in widths]), *map(line, cells)])


def render_markdown(r: RunResult) -> str:
    mark = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    parts = [
        f"# Run {r.run_id}: {r.preset}", "",
        f"- **Result:** {mark(r.ok)} (score {r.score}/100)", f"- **Model:** `{r.model}`",
        f"- **Attempts / steps / tokens:** {r.attempts} / {r.steps} / {r.tokens}",
        f"- **Duration:** {human(r.duration_s)}", f"- **Stopped because:** {r.stop_reason}", "",
        "## Acceptance rules", "", *[f"- {mark(c['ok'])} {c['name']}" + ("" if c["ok"] else f" - {c['detail']}") for c in r.rule_checks], "",
    ]
    failing = [c for c in r.verify_checks if not c["ok"]]
    parts += ["## Static verification", "", *(f"- FAIL {c['name']} - {c['detail']}" for c in failing)] if failing else ["## Static verification", "", "All checks passed."]
    changed = [f"{kind}: {', '.join(files)}" for kind, files in r.changes.items() if files]
    parts += ["", "## Files", "", *[f"- {c}" for c in changed], "", "## Agent summary", "", r.summary or "(none)", ""]
    return "\n".join(parts)


def print_result(r: RunResult) -> None:
    print("\n" + "=" * 68)
    print(f"{'PASS' if r.ok else 'NEEDS REVIEW'}  {r.preset}  score={r.score}/100  steps={r.steps}  attempts={r.attempts}  {human(r.duration_s)}")
    print("=" * 68)
    print_checks(r.rule_checks + [c for c in r.verify_checks if not c["ok"]])
    if r.summary:
        print(f"\nAgent summary: {r.summary}")


def archive_workspace(root: Path, dest: Path) -> Path:
    dest = dest.with_suffix(".zip") if dest.suffix != ".zip" else dest
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in workspace_files(root):
            z.write(root / rel, rel)
    return dest


# ---------------------------------------------------------------------------
# Matrix runner: models x presets x repeats, one model loaded at a time
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    models: list[str]
    presets: list[Preset]
    repeats: int = 1
    out_dir: Path = Path("runs")
    fixed_workspace: Path | None = None


async def run_matrix(plan: Plan, opts: Options, history: History, pool: BackendPool | None = None) -> list[RunResult]:
    pool, results = pool or BackendPool(opts), []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        for model in plan.models:
            backend = await pool.get(model)
            for preset in plan.presets:
                for rep in range(1, plan.repeats + 1):
                    workspace = plan.fixed_workspace or plan.out_dir / stamp / slug(model) / f"{preset.name}-{rep}"
                    spec = RunSpec(model, preset, workspace.resolve(), f"{stamp}-{len(results) + 1}")
                    result = await execute(spec, backend, opts)
                    (Path(result.workspace) / META_DIR / "report.md").write_text(render_markdown(result), encoding="utf-8")
                    history.add(result)
                    results.append(result)
                    print_result(result)
    finally:
        await pool.release()
    return results


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

REPL_HELP = """commands: /files  /show <path>  /undo <path>  /presets  /preset <name>  /history  /help  /quit
anything else is sent to the agent as a task (the workspace persists between tasks)"""


async def interactive(args: argparse.Namespace, presets: dict[str, Preset], history: History, opts: Options) -> int:
    pool = BackendPool(opts)
    agent = AgenticCoder(
        args.model, args.workspace, opts.max_steps, opts.max_new_tokens, opts.temperature, opts.cmd_timeout,
        backend=await pool.get(args.model), timeout_s=opts.timeout, context_tokens=opts.context_tokens,
        verbose=opts.verbose_agent, stream=opts.stream,
    )
    print(f"workspace: {agent.ws.root}\n{REPL_HELP}")
    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, "task> ")).strip()
            except EOFError:
                break
            cmdword, _, rest = line.partition(" ")
            try:
                if not line:
                    continue
                if cmdword in {"/quit", "/exit"}:
                    break
                elif cmdword == "/help":
                    print(REPL_HELP)
                elif cmdword == "/files":
                    print("\n".join(workspace_files(agent.ws.root)) or "(empty)")
                elif cmdword == "/show":
                    print(agent.ws.read(rest.strip(), numbered=True))
                elif cmdword == "/undo":
                    print(agent.ws.undo(rest.strip()))
                elif cmdword == "/presets":
                    print(preset_table(presets))
                elif cmdword == "/history":
                    print(history_table(history.recent(10)))
                elif cmdword == "/preset":
                    preset = presets[rest.strip()]
                    await agent.run(preset.task)
                    print_checks(await run_rules(RuleContext(agent.ws, agent.runner), preset.rules))
                else:
                    await agent.run(line)
            except (ToolError, KeyError) as e:
                print(f"error: {e}")
    finally:
        await pool.release()
    return 0


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def print_checks(checks: list[Any]) -> None:
    for c in checks:
        name, ok, detail = (c["name"], c["ok"], c["detail"]) if isinstance(c, dict) else (c.name, c.ok, c.detail)
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + ("" if ok else f"  ({detail[:100]})"))


def preset_table(presets: dict[str, Preset]) -> str:
    return format_table(["name", "difficulty", "rules", "tags"], [[p.name, p.difficulty, len(p.rules), ",".join(p.tags)] for p in presets.values()])


def history_table(rows: list[sqlite3.Row]) -> str:
    return format_table(
        ["when", "model", "preset", "ok", "score", "steps", "tries", "time"],
        [[r["ts"], r["model"][-34:], r["preset"], "yes" if r["ok"] else "no", r["score"], r["steps"], r["attempts"], human(r["duration"])] for r in rows],
    ) if rows else "No runs recorded yet."


def pick_presets(spec: str, presets: dict[str, Preset]) -> list[Preset]:
    names = list(presets) if spec == "all" else [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in presets]
    if unknown:
        raise SystemExit(f"unknown preset(s): {', '.join(unknown)}. Available: {', '.join(presets)}")
    return [presets[n] for n in names]


async def cmd_run(args, presets, history, opts) -> int:
    if args.task:
        preset = Preset("custom", args.task)
    else:
        preset = pick_presets(args.preset or "tab-timer", presets)[0]
    for warning in preflight(args.model, environment()):
        log.warning("preflight: %s", warning)
    results = await run_matrix(Plan([args.model], [preset], 1, fixed_workspace=Path(args.workspace)), opts, history)
    r = results[0]
    Path(args.report).write_text(json.dumps(asdict(r), indent=2, ensure_ascii=False), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(r), encoding="utf-8")
    if args.archive:
        print(f"archive: {archive_workspace(Path(r.workspace), Path(args.archive))}")
    log.info("Report saved to %s", args.report)
    return 0 if r.ok else 1


async def cmd_bench(args, presets, history, opts) -> int:
    models = [m.strip() for m in args.models.split(",") if m.strip()] or [args.model]
    plan = Plan(models, pick_presets(args.presets, presets), args.repeat, Path(args.out))
    results = await run_matrix(plan, opts, history)
    print("\n" + format_table(
        ["model", "preset", "ok", "score", "steps", "tries", "time"],
        [[r.model[-34:], r.preset, "yes" if r.ok else "no", r.score, r.steps, r.attempts, human(r.duration_s)] for r in results],
    ))
    summary = Path(args.out) / "last_bench.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nall-time leaderboard ({args.db}):\n" + format_table(
        ["model", "runs", "avg_score", "pass_%", "avg_s", "avg_steps"], [list(r) for r in history.leaderboard()]))
    return 0


def cmd_doctor(args) -> int:
    env = environment()
    print(json.dumps(env, indent=2))
    warnings = preflight(args.model, env)
    need = estimate_model_gb(args.model)
    print(f"\nmodel: {args.model}" + (f" (about {need:.0f} GB of weights in 16-bit)" if need else " (remote or size unknown)"))
    print("\n".join(f"WARNING: {w}" for w in warnings) or "All preflight checks passed.")
    return 1 if warnings else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", default=DEFAULT_MODEL, help="HF repo id, 'ollama:<model>', or 'openai:<model>'")
    common.add_argument("--base-url", default="", help="server URL for ollama/openai backends")
    common.add_argument("--api-key", default="")
    common.add_argument("--max-steps", type=int, default=30)
    common.add_argument("--max-new-tokens", type=int, default=3072)
    common.add_argument("--temperature", type=float, default=0.2)
    common.add_argument("--context-tokens", type=int, default=7000, help="conversation budget before old turns are trimmed")
    common.add_argument("--cmd-timeout", type=int, default=30)
    common.add_argument("--timeout", type=float, default=3600, help="seconds per attempt")
    common.add_argument("--attempts", type=int, default=2, help="total attempts; later ones resume with review feedback")
    common.add_argument("--retries", type=int, default=None, help="extra attempts (alias: attempts = retries + 1)")
    common.add_argument("--repair-steps", type=int, default=12, help="extra steps granted to each repair attempt")
    common.add_argument("--db", default="runs.db", help="SQLite run history")
    common.add_argument("--preset-file", action="append", help="JSON file with extra presets (repeatable)")
    common.add_argument("--config", help="JSON file of default option values")
    common.add_argument("--quiet", action="store_true")
    common.add_argument("--no-stream", action="store_true")
    common.add_argument("-v", "--verbose", action="store_true")

    p = argparse.ArgumentParser(prog="run_agent.py", description="Run, grade, repair, and compare autonomous coding runs")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", parents=[common], help="one task in one workspace (default command)")
    run.add_argument("--preset", help="preset name (default: tab-timer)")
    run.add_argument("--task", help="custom task text (no acceptance rules)")
    run.add_argument("--workspace", default="workspace")
    run.add_argument("--clean", action="store_true", help="delete the workspace first")
    run.add_argument("--report", default="run_report.json")
    run.add_argument("--markdown", help="also write a Markdown report here")
    run.add_argument("--archive", help="zip the finished workspace to this path")
    bench = sub.add_parser("bench", parents=[common], help="models x presets x repeats with a leaderboard")
    bench.add_argument("--models", default="", help="comma-separated model list (default: --model)")
    bench.add_argument("--presets", default="all", help="'all' or comma-separated names")
    bench.add_argument("--repeat", type=int, default=1)
    bench.add_argument("--out", default="runs")
    ia = sub.add_parser("interactive", parents=[common], help="REPL that keeps one workspace across tasks")
    ia.add_argument("--workspace", default="workspace")
    hist = sub.add_parser("history", parents=[common], help="show recorded runs")
    hist.add_argument("--limit", type=int, default=20)
    hist.add_argument("--leaderboard", action="store_true")
    pr = sub.add_parser("presets", parents=[common], help="list presets or show one")
    pr.add_argument("--show")
    sub.add_parser("doctor", parents=[common], help="check GPU, memory, node, disk for the chosen model")
    sub.add_parser("selftest", parents=[common], help="offline test with a scripted model")
    return p, sub


def apply_config(sub: argparse._SubParsersAction, argv: list[str]) -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    known, _ = pre.parse_known_args(argv)
    if known.config:
        values = {k.replace("-", "_"): v for k, v in json.loads(Path(known.config).read_text(encoding="utf-8")).items()}
        for sp in sub.choices.values():
            sp.set_defaults(**values)


async def amain(argv: list[str]) -> int:
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in {"-h", "--help"}):
        argv = ["run", *argv]  # keeps `python run_agent.py --clean` working
    parser, sub = build_parser()
    apply_config(sub, argv)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")

    loop, main_task = asyncio.get_running_loop(), asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, main_task.cancel)
        except NotImplementedError:
            pass

    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "selftest":
        return 0 if await selftest() else 1
    presets, opts = load_presets(args.preset_file or []), Options.from_args(args)
    history = None if args.command == "presets" else History(args.db)
    try:
        if args.command == "history":
            print(format_table(["model", "runs", "avg_score", "pass_%", "avg_s", "avg_steps"], [list(r) for r in history.leaderboard()])
                  if args.leaderboard else history_table(history.recent(args.limit)))
            return 0
        if args.command == "presets":
            if args.show:
                p = pick_presets(args.show, presets)[0]
                print(f"{p.name} ({p.difficulty})\n\n{p.task}\n\nrules:\n" + "\n".join(f"  - {rule_label(r)}" for r in p.rules))
            else:
                print(preset_table(presets))
            return 0
        handler = {"run": cmd_run, "bench": cmd_bench, "interactive": interactive}[args.command]
        return await handler(args, presets, history, opts)
    except asyncio.CancelledError:
        log.warning("Cancelled by user")
        return 130


# ---------------------------------------------------------------------------
# Offline self-test: scripted model, real loop, real grading, real repair
# ---------------------------------------------------------------------------


async def selftest() -> bool:
    import contextlib
    import io

    call = lambda name, **a: "<tool_call>" + json.dumps({"name": name, "arguments": a}) + "</tool_call>"  # noqa: E731
    preset = Preset("hello", "Create hello.py that prints hi", [exists("hello.py", min_bytes=5), has("hello.py", "print"), cmd("python hello.py", expect_stdout=r"^hi$")])
    replies = [
        call("write_file", path="hello.py", content="print('hello')\n"), call("finish", summary="done"),  # attempt 1: wrong output
        call("edit_file", path="hello.py", old_str="hello", new_str="hi"), call("finish", summary="fixed to print hi"),  # attempt 2
    ]
    opts = Options(attempts=2, verbose_agent=False, stream=False)
    history = History(":memory:")
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        pool = BackendPool(opts, factory=lambda cfg: ScriptedBackend(replies))
        results = await run_matrix(Plan(["scripted"], [preset], fixed_workspace=Path(tmp) / "w"), opts, history, pool)
        r, root = results[0], Path(tmp) / "w"
        final_text, has_md = (root / "hello.py").read_text(), (root / META_DIR / "report.md").is_file()
        zip_path = archive_workspace(root, Path(tmp) / "out")
        cfg = AgentConfig(model_name="x")
        ws = Workspace(str(Path(tmp) / "rules"), cfg)
        (ws.root / "m.json").write_text('{"manifest_version": 3, "permissions": ["tabs"]}')
        rule_ok = [c.ok for c in await run_rules(RuleContext(ws, CommandRunner(ws, cfg)), [
            jeq("m.json", "manifest_version", 3), jhas("m.json", "permissions", ["tabs", "storage"]),
            {"kind": "nope"}, exists("missing.txt"), lacks("m.json", "eval")])]
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
    checks.update({
        "repair attempt fixed the task": r.ok and r.attempts == 2 and final_text == "print('hi')\n",
        "score computed": r.score > 60,
        "history roundtrip": history.recent(1)[0]["preset"] == "hello" and len(history.leaderboard()) == 1,
        "markdown report": "hello.py" in render_markdown(r) and has_md,
        "rule engine": rule_ok == [True, False, False, False, True],
        "archive excludes .agent": names == ["hello.py"],
        "model size estimate": estimate_model_gb(DEFAULT_MODEL) == 16.0 and estimate_model_gb("ollama:x:7b") is None,
        "table formatting": format_table(["a", "b"], [[1, "xx"]]).splitlines()[0] == "a  b",
    })
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return all(checks.values())


def main() -> int:
    try:
        return asyncio.run(amain(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # e.g. `run_agent.py history | head`
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())