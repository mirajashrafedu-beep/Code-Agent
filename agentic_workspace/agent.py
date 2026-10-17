# agent.py
"""
AgenticCoder: a local autonomous coding agent.

Layers (each defined once, top to bottom):
  config/state/events -> Workspace (sandboxed files) -> CommandRunner -> Verifier
  -> ToolBox (auto-schema tools) -> tool-call parser -> model backends
  -> ContextManager -> AgenticCoder (the loop) -> CLI

Backends: local Hugging Face (default), "ollama:<model>", "openai:<model>" (any OpenAI-compatible server).
Compatible with the earlier API: AgenticCoder(model_name, workspace, max_steps, ...).run(task).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
import threading
import time
import typing
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Configuration and state
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED = frozenset({
    "python", "python3", "pytest", "node", "npm", "npx", "tsc", "ruff", "mypy", "black",
    "go", "cargo", "rustc", "gcc", "g++", "javac", "java",
    "ls", "cat", "head", "tail", "wc", "grep", "echo", "pwd", "diff", "sort", "uniq",
})
META_DIR = ".agent"
IGNORED_DIRS = {META_DIR, ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


@dataclass
class AgentConfig:
    model_name: str
    workspace: str = "workspace"
    max_steps: int = 25
    max_new_tokens: int = 3072
    temperature: float = 0.2
    cmd_timeout: int = 30
    timeout_s: float = 3600.0
    context_tokens: int = 7000
    keep_last_messages: int = 8
    max_file_bytes: int = 1_000_000
    max_output_chars: int = 4000
    allowed_commands: frozenset = DEFAULT_ALLOWED
    stall_warn: int = 3
    stall_stop: int = 5
    max_error_streak: int = 8
    finish_gate_retries: int = 2
    base_url: str = ""
    api_key: str = ""
    system_prompt: str = ""
    stream: bool = True
    verbose: bool = True
    log_trajectory: bool = True


@dataclass
class AgentState:
    task: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    written: dict[str, int] = field(default_factory=dict)
    plan: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    finished: bool = False
    summary: str = ""
    stop_reason: str = ""
    gate_attempts: int = 0
    invalid_streak: int = 0
    error_streak: int = 0
    metrics: dict[str, Any] = field(default_factory=lambda: {"tokens_out": 0, "tool_calls": 0, "tool_errors": 0})
    changes: dict[str, list[str]] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    raw: str = ""
    repaired: bool = False


@dataclass
class ToolResult:
    ok: bool
    text: str


class ToolError(Exception):
    """An expected, model-readable failure."""


# ---------------------------------------------------------------------------
# Events: one bus feeds the console, the trajectory log, and any user callback
# ---------------------------------------------------------------------------


class Events:
    def __init__(self) -> None:
        self._subs: list[Callable[[str, dict[str, Any]], None]] = []

    def subscribe(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        self._subs.append(fn)

    def emit(self, kind: str, **data: Any) -> None:
        for fn in self._subs:
            try:
                fn(kind, data)
            except Exception:
                pass  # observers must never break the agent


class ConsoleReporter:
    def __init__(self, stream: bool) -> None:
        self.stream = stream
        self._streaming = False

    def _end_stream(self) -> None:
        if self._streaming:
            sys.stdout.write("\n")
            self._streaming = False

    def __call__(self, kind: str, d: dict[str, Any]) -> None:
        if kind == "token" and self.stream:
            sys.stdout.write(d["text"])
            sys.stdout.flush()
            self._streaming = True
            return
        self._end_stream()
        if kind == "loading":
            print(f"[agent] loading {d['model']} ...")
        elif kind == "start":
            print(f"[agent] model={d['model']} workspace={d['workspace']}")
        elif kind == "step":
            note = f" (trimmed {d['trimmed']} msgs)" if d["trimmed"] else ""
            print(f"\n[step {d['n']}] context ~{d['tokens']} tokens{note}")
        elif kind == "tool_call":
            print(f"  -> {d['name']}({_short(json.dumps(d['arguments'], ensure_ascii=False), 110)})")
        elif kind == "tool_result":
            print(f"     {'ok ' if d['ok'] else 'ERR'} {_short(d['text'], 120)}")
        elif kind == "warning":
            print(f"  ! {d['text']}")
        elif kind == "done":
            print(f"\n[agent] {d['stop_reason']} after {d['steps']} steps, {d['duration_s']}s")
            if d["summary"]:
                print(f"[agent] summary: {d['summary']}")
            for kind_, files in d["changes"].items():
                if files:
                    print(f"[agent] {kind_}: {', '.join(files)}")


class TrajectoryLogger:
    """Append-only JSONL record of everything that happened, for debugging and fine-tuning data."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def __call__(self, kind: str, d: dict[str, Any]) -> None:
        if kind == "token":
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **d}, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _short(text: str, n: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.65), int(limit * 0.35)
    return f"{text[:head]}\n... [{len(text) - limit} chars truncated] ...\n{text[-tail:]}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Workspace: sandboxed file operations with snapshots, undo, and change tracking
# ---------------------------------------------------------------------------


class Workspace:
    def __init__(self, root: str, cfg: AgentConfig) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.meta = self.root / META_DIR
        self.meta.mkdir(exist_ok=True)
        self.snapshots = self.meta / "snapshots"
        self.baseline: dict[str, str | None] = {}  # rel path -> original hash, None if the file did not exist

    # -- paths --------------------------------------------------------------
    def safe(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
            raise ToolError("invalid path")
        p = (self.root / rel).resolve()
        if p != self.root and not p.is_relative_to(self.root):
            raise ToolError(f"path escapes workspace: {rel}")
        if p == self.meta or self.meta in p.parents:
            raise ToolError("the .agent folder is reserved")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix() or "."

    def _is_binary(self, p: Path) -> bool:
        with p.open("rb") as f:
            return b"\x00" in f.read(1024)

    def _walk(self, start: Path):
        for current, dirs, files in os.walk(start):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
            for f in sorted(files):
                yield Path(current) / f

    # -- change tracking ----------------------------------------------------
    def _remember(self, p: Path) -> None:
        rel = self.rel(p)
        if rel in self.baseline:
            return
        if p.is_file():
            data = p.read_bytes()
            self.baseline[rel] = sha(data)
            dest = self.snapshots / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        else:
            self.baseline[rel] = None

    def changes(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {"added": [], "modified": [], "deleted": []}
        for rel, original in sorted(self.baseline.items()):
            p = self.root / rel
            current = sha(p.read_bytes()) if p.is_file() else None
            if original is None and current is not None:
                out["added"].append(rel)
            elif original is not None and current is None:
                out["deleted"].append(rel)
            elif original != current:
                out["modified"].append(rel)
        return out

    def touched_files(self) -> list[str]:
        return [r for r in self.baseline if (self.root / r).is_file()]

    # -- writing ------------------------------------------------------------
    def _atomic_write(self, p: Path, text: str) -> None:
        if len(text.encode("utf-8")) > self.cfg.max_file_bytes:
            raise ToolError(f"file too large (limit {self.cfg.max_file_bytes} bytes); split it into modules")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(tmp, p)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    @staticmethod
    def _syntax_warning(p: Path, text: str) -> str:
        try:
            if p.suffix == ".json":
                json.loads(text)
            elif p.suffix == ".py":
                compile(text, str(p), "exec")
        except (json.JSONDecodeError, SyntaxError) as e:
            return f" WARNING: {type(e).__name__}: {e}. Fix this file."
        return ""

    def write(self, rel: str, content: str, append: bool = False) -> str:
        p = self.safe(rel)
        if p.is_dir():
            raise ToolError(f"{rel} is a directory")
        self._remember(p)
        existed = p.exists()
        text = (p.read_text(encoding="utf-8") + content) if append and existed else content
        self._atomic_write(p, text)
        return (
            f"{'Updated' if existed else 'Created'} {self.rel(p)} "
            f"({len(text.splitlines())} lines, {len(text.encode('utf-8'))} bytes)." + self._syntax_warning(p, text)
        )

    def edit(self, rel: str, old: str, new: str, replace_all: bool = False) -> str:
        p = self.safe(rel)
        if not p.is_file():
            raise ToolError(f"{rel} does not exist")
        text = p.read_text(encoding="utf-8")
        count = text.count(old) if old else 0
        if count == 0:
            raise ToolError("old_str not found; read the file again and copy the text exactly")
        if count > 1 and not replace_all:
            raise ToolError(f"old_str matches {count} places; add context or set replace_all=true")
        self._remember(p)
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        self._atomic_write(p, updated)
        return f"Edited {self.rel(p)}: {count if replace_all else 1} replacement(s)." + self._syntax_warning(p, updated)

    def delete(self, rel: str) -> str:
        p = self.safe(rel)
        if not p.is_file():
            raise ToolError(f"{rel} is not a file")
        self._remember(p)
        p.unlink()
        return f"Deleted {self.rel(p)}."

    def undo(self, rel: str) -> str:
        p = self.safe(rel)
        key = self.rel(p)
        if key not in self.baseline:
            raise ToolError(f"{rel} has no recorded changes in this run")
        if self.baseline[key] is None:
            p.unlink(missing_ok=True)
            return f"Removed {key} (it did not exist before this run)."
        shutil.copyfile(self.snapshots / key, p)
        return f"Restored {key} to its original content."

    # -- reading ------------------------------------------------------------
    def read(self, rel: str, start: int = 1, end: int = 0, numbered: bool = False) -> str:
        p = self.safe(rel)
        if not p.is_file():
            raise ToolError(f"{rel} does not exist")
        if p.stat().st_size > self.cfg.max_file_bytes:
            raise ToolError("file too large to read whole; use a line range or search_files")
        if self._is_binary(p):
            raise ToolError("binary file")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(start, 1)
        chunk = lines[first - 1 : (end if end > 0 else len(lines))]
        if numbered:
            chunk = [f"{i}\t{line}" for i, line in enumerate(chunk, first)]
        return truncate("\n".join(chunk), self.cfg.max_output_chars * 2) or "(empty file)"

    def listing(self, directory: str = ".", max_depth: int = 4) -> str:
        root = self.safe(directory)
        if not root.is_dir():
            raise ToolError(f"{directory} is not a directory")
        out: list[str] = []
        for p in self._walk(root):
            if len(p.relative_to(root).parts) > max_depth + 1:
                continue
            out.append(f"{self.rel(p)}  ({p.stat().st_size} B)")
            if len(out) >= 300:
                out.append("... [list truncated at 300 files]")
                break
        return "\n".join(out) or "(empty workspace)"

    def search(self, pattern: str, directory: str = ".", glob: str = "*", max_results: int = 50) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        import fnmatch

        hits: list[str] = []
        for p in self._walk(self.safe(directory)):
            if not fnmatch.fnmatch(p.name, glob) or p.stat().st_size > self.cfg.max_file_bytes or self._is_binary(p):
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{self.rel(p)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + "\n... [max_results reached]"
        return "\n".join(hits) or "No matches."


# ---------------------------------------------------------------------------
# CommandRunner: allow-listed, shell-free, time-limited subprocesses
# ---------------------------------------------------------------------------

BLOCKED_FLAGS = {"-c", "-e", "--eval", "--exec", "-p", "--print"}
INTERPRETERS = {"python", "python3", "node"}
SHELL_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "&", "`", "$("}


class CommandRunner:
    def __init__(self, ws: Workspace, cfg: AgentConfig) -> None:
        self.ws, self.cfg = ws, cfg

    def check(self, args: list[str]) -> None:
        if not args:
            raise ToolError("empty command")
        if args[0] not in self.cfg.allowed_commands:
            raise ToolError(f"command '{args[0]}' is not allowed. Allowed: {sorted(self.cfg.allowed_commands)}")
        if shutil.which(args[0]) is None:
            raise ToolError(f"'{args[0]}' is not installed on this machine")
        for a in args[1:]:
            if a in SHELL_TOKENS:
                raise ToolError(f"shell operator '{a}' is unsupported; run one command per call")
            if a in BLOCKED_FLAGS and args[0] in INTERPRETERS:
                raise ToolError(f"flag '{a}' is blocked; write the code to a file and run the file")
            if not a.startswith("-") and (os.path.isabs(a) or ".." in Path(a).parts):
                raise ToolError(f"argument '{a}' points outside the workspace")

    async def run(self, command: str, timeout: int = 0) -> tuple[int, str]:
        try:
            args = shlex.split(command)
        except ValueError as e:
            raise ToolError(f"cannot parse command: {e}")
        self.check(args)
        limit = max(1, min(timeout or self.cfg.cmd_timeout, 300))
        env = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(self.ws.root), "LANG": "C.UTF-8",
            "NO_COLOR": "1", "CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        }
        posix = os.name == "posix"
        t0 = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=self.ws.root, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=posix,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL) if posix else proc.kill()
            await proc.communicate()
            raise ToolError(f"command timed out after {limit}s and was killed")
        body = "\n".join(s for s in (out.decode(errors="replace").strip(), err.decode(errors="replace").strip()) if s)
        text = f"exit_code={proc.returncode} ({time.perf_counter() - t0:.1f}s)\n{truncate(body or '(no output)', self.cfg.max_output_chars)}"
        return proc.returncode or 0, text


# ---------------------------------------------------------------------------
# Verifier: automatic checks that gate the finish tool
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class Verifier:
    def __init__(self, ws: Workspace, runner: CommandRunner) -> None:
        self.ws, self.runner = ws, runner

    async def run(self, paths: list[str] | None = None) -> list[Check]:
        rels = paths or self.ws.touched_files()
        checks: list[Check] = []
        for rel in rels:
            p = self.ws.safe(rel)
            if not p.is_file():
                checks.append(Check(f"exists:{rel}", False, "missing"))
                continue
            suffix = p.suffix.lower()
            if suffix == ".py":
                checks.append(self._python(p, rel))
            elif suffix == ".json":
                checks.extend(self._json(p, rel))
            elif suffix in {".js", ".mjs", ".cjs"}:
                checks.append(await self._node(rel))
            elif suffix in {".html", ".htm"}:
                checks.extend(self._html(p, rel))
        return checks

    @staticmethod
    def _python(p: Path, rel: str) -> Check:
        try:
            compile(p.read_text(encoding="utf-8"), rel, "exec")
            return Check(f"python:{rel}", True)
        except SyntaxError as e:
            return Check(f"python:{rel}", False, f"line {e.lineno}: {e.msg}")

    def _json(self, p: Path, rel: str) -> list[Check]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return [Check(f"json:{rel}", False, str(e))]
        checks = [Check(f"json:{rel}", True)]
        if isinstance(data, dict) and "manifest_version" in data:
            checks.extend(self._manifest(p, rel, data))
        return checks

    @staticmethod
    def _manifest(p: Path, rel: str, data: dict[str, Any]) -> list[Check]:
        checks = [Check(f"manifest:{rel}:version_3", data.get("manifest_version") == 3, str(data.get("manifest_version")))]
        refs = [(data.get("background") or {}).get("service_worker"), (data.get("action") or {}).get("default_popup")]
        refs += list((data.get("icons") or {}).values())
        for cs in data.get("content_scripts") or []:
            refs += list(cs.get("js", [])) + list(cs.get("css", []))
        for ref in filter(None, refs):
            checks.append(Check(f"manifest:{rel}:file:{ref}", (p.parent / ref).is_file(), "referenced file missing"))
        return checks

    async def _node(self, rel: str) -> Check:
        if shutil.which("node") is None:
            return Check(f"node:{rel}", True, "skipped, node not installed")
        try:
            code, text = await self.runner.run(f"node --check {shlex.quote(rel)}")
        except ToolError as e:
            return Check(f"node:{rel}", False, str(e))
        return Check(f"node:{rel}", code == 0, "" if code == 0 else _short(text, 300))

    def _html(self, p: Path, rel: str) -> list[Check]:
        html = p.read_text(encoding="utf-8", errors="replace")
        ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', html))
        checks = []
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html):
            if src.startswith(("http://", "https://", "//")):
                continue
            target = p.parent / src
            checks.append(Check(f"html:{rel}:script:{src}", target.is_file(), "script file missing"))
            if target.is_file():
                used = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', target.read_text(encoding="utf-8", errors="replace")))
                missing = sorted(used - ids)
                checks.append(Check(f"html:{rel}:ids_for:{src}", not missing, f"missing ids: {missing}"))
        return checks


# ---------------------------------------------------------------------------
# ToolBox: tools are declared once; their JSON schemas are generated from signatures
# ---------------------------------------------------------------------------

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def tool(fn: Callable) -> Callable:
    fn._is_tool = True  # type: ignore[attr-defined]
    return fn


def _json_type(tp: Any) -> dict[str, Any]:
    origin = typing.get_origin(tp)
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _json_type(args[0]) if args else {"type": "string"}
    if origin is list:
        args = typing.get_args(tp)
        return {"type": "array", "items": _json_type(args[0]) if args else {"type": "string"}}
    return {"type": _JSON_TYPES.get(tp, "string")}


def schema_for(fn: Callable) -> dict[str, Any]:
    doc = inspect.cleandoc(fn.__doc__ or "")
    desc, _, arg_block = doc.partition("Args:")
    pdocs = {m.group(1): m.group(2).strip() for m in re.finditer(r"^\s*(\w+)\s*:\s*(.+)$", arg_block, re.M)}
    hints = typing.get_type_hints(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, p in inspect.signature(fn).parameters.items():
        prop = _json_type(hints.get(name, str))
        if name in pdocs:
            prop["description"] = pdocs[name]
        if p.default is inspect.Parameter.empty:
            required.append(name)
        props[name] = prop
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": desc.strip(),
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


class ToolBox:
    TOOL_ALIASES = {
        "create_file": "write_file", "execute_command": "run_command", "bash": "run_command", "shell": "run_command",
        "cat": "read_file", "ls": "list_files", "grep": "search_files", "done": "finish", "str_replace": "edit_file",
    }
    ARG_ALIASES = {
        "filename": "path", "file": "path", "file_path": "path", "filepath": "path", "cmd": "command",
        "text": "content", "old": "old_str", "new": "new_str", "message": "summary", "steps": "steps",
    }

    def __init__(self, ws: Workspace, runner: CommandRunner, verifier: Verifier, state: AgentState, cfg: AgentConfig) -> None:
        self.ws, self.runner, self.verifier, self.state, self.cfg = ws, runner, verifier, state, cfg
        self.tools = {n: getattr(self, n) for n, v in vars(type(self)).items() if getattr(v, "_is_tool", False)}
        self.definitions = [schema_for(fn) for fn in self.tools.values()]
        self._schemas = {d["function"]["name"]: d["function"]["parameters"] for d in self.definitions}

    # -- file tools -----------------------------------------------------------
    @tool
    def write_file(self, path: str, content: str, append: bool = False) -> str:
        """Create or overwrite a file in the workspace. Parent folders are created automatically.

        Args:
            path: Relative file path, e.g. 'src/popup.js'
            content: The complete file content
            append: Append to the file instead of overwriting it
        """
        msg = self.ws.write(path, content, append)
        self.state.written[path] = len(content)
        return msg

    @tool
    def edit_file(self, path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
        """Replace exact text in an existing file; old_str must be unique unless replace_all is true.

        Args:
            path: Relative file path
            old_str: Exact text to find, including whitespace
            new_str: Replacement text
            replace_all: Replace every occurrence
        """
        msg = self.ws.edit(path, old_str, new_str, replace_all)
        self.state.written.setdefault(path, 0)
        return msg

    @tool
    def read_file(self, path: str, start_line: int = 1, end_line: int = 0, numbered: bool = False) -> str:
        """Read a file, optionally only a line range.

        Args:
            path: Relative file path
            start_line: First line to return (1-based)
            end_line: Last line to return, 0 means the end
            numbered: Prefix lines with their numbers
        """
        return self.ws.read(path, start_line, end_line, numbered)

    @tool
    def list_files(self, directory: str = ".", max_depth: int = 4) -> str:
        """List files in the workspace with sizes.

        Args:
            directory: Relative directory to list
            max_depth: Folder levels to descend
        """
        return self.ws.listing(directory, max_depth)

    @tool
    def search_files(self, pattern: str, directory: str = ".", glob: str = "*", max_results: int = 50) -> str:
        """Search file contents with a regular expression (like grep -rn).

        Args:
            pattern: Regular expression
            directory: Relative directory to search
            glob: Only search file names matching this pattern, e.g. '*.js'
            max_results: Maximum matching lines
        """
        return self.ws.search(pattern, directory, glob, max_results)

    @tool
    def delete_file(self, path: str) -> str:
        """Delete one file from the workspace.

        Args:
            path: Relative file path
        """
        self.state.written.pop(path, None)
        return self.ws.delete(path)

    @tool
    def undo_changes(self, path: str) -> str:
        """Restore a file to how it was at the start of this run (or remove it if this run created it).

        Args:
            path: Relative file path
        """
        return self.ws.undo(path)

    # -- execution and verification --------------------------------------------
    @tool
    async def run_command(self, command: str, timeout: int = 0) -> str:
        """Run one allow-listed command in the workspace, for example 'node --check popup.js'. No pipes or shell operators.

        Args:
            command: The command line
            timeout: Seconds before the command is killed, 0 uses the default
        """
        return (await self.runner.run(command, timeout))[1]

    @tool
    async def verify(self, paths: list[str] | None = None) -> str:
        """Run automatic checks (syntax, JSON validity, manifest paths, HTML ids) on changed files or given paths.

        Args:
            paths: Relative file paths, omit to check every file changed in this run
        """
        checks = await self.verifier.run(paths)
        self.state.checks = [asdict(c) for c in checks]
        if not checks:
            return "No checkable files yet."
        return "\n".join(f"{'PASS' if c.ok else 'FAIL'} {c.name}" + (f" - {c.detail}" if c.detail and not c.ok else "") for c in checks)

    # -- planning and completion -------------------------------------------------
    @tool
    def plan(self, steps: list[str]) -> str:
        """Set or replace the task plan as an ordered checklist. The plan is shown again after every tool turn.

        Args:
            steps: Short imperative steps
        """
        self.state.plan = [{"text": str(s)[:120], "done": False} for s in steps][:12]
        return "Plan saved.\n" + self.plan_text()

    @tool
    def complete_step(self, index: int) -> str:
        """Mark a plan step as done.

        Args:
            index: Step number, starting at 1
        """
        if not 1 <= index <= len(self.state.plan):
            raise ToolError(f"no step {index}; the plan has {len(self.state.plan)} steps")
        self.state.plan[index - 1]["done"] = True
        return self.plan_text()

    def plan_text(self) -> str:
        return "\n".join(f"[{'x' if s['done'] else ' '}] {i}. {s['text']}" for i, s in enumerate(self.state.plan, 1))

    @tool
    async def finish(self, summary: str) -> str:
        """End the task. Rejected if changed files fail automatic checks. Summarize files, checks run, assumptions, limitations.

        Args:
            summary: Final report
        """
        if self.state.gate_attempts < self.cfg.finish_gate_retries:
            failed = [c for c in await self.verifier.run() if not c.ok]
            if failed:
                self.state.gate_attempts += 1
                raise ToolError("finish rejected, fix these first:\n" + "\n".join(f"- {c.name}: {c.detail}" for c in failed))
        self.state.finished, self.state.summary = True, summary
        return "Finished." + (f" Unchecked plan steps: {sum(not s['done'] for s in self.state.plan)}." if any(not s["done"] for s in self.state.plan) else "")

    # -- dispatch ----------------------------------------------------------------
    @staticmethod
    def _coerce(value: Any, prop: dict[str, Any]) -> Any:
        t = prop.get("type")
        try:
            if t == "integer" and not isinstance(value, bool):
                return int(value)
            if t == "number":
                return float(value)
            if t == "boolean" and isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes"}
            if t == "string" and not isinstance(value, str):
                return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            if t == "array" and isinstance(value, str):
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [value]
        except (TypeError, ValueError):
            pass
        return value

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        name = self.TOOL_ALIASES.get(name, name)
        fn = self.tools.get(name)
        if fn is None:
            return ToolResult(False, f"unknown tool '{name}'. Available: {sorted(self.tools)}")
        schema = self._schemas[name]
        props = schema["properties"]
        args: dict[str, Any] = {}
        for key, value in (arguments if isinstance(arguments, dict) else {}).items():
            target = key if key in props else self.ARG_ALIASES.get(key)
            if target in props and target not in args:
                args[target] = self._coerce(value, props[target])
        missing = [r for r in schema["required"] if r not in args]
        if missing:
            return ToolResult(False, f"missing required argument(s) {missing}; expected {list(props)}")
        try:
            result = fn(**args)
            return ToolResult(True, await result if inspect.isawaitable(result) else result)
        except ToolError as e:
            return ToolResult(False, str(e))
        except Exception as e:
            return ToolResult(False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Tool-call parsing: tolerant of the formats small local models actually emit
# ---------------------------------------------------------------------------

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)
FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _balance(s: str) -> str:
    """Close any strings, objects, and arrays a truncated JSON snippet left open."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            esc = (not esc and ch == "\\")
            if ch == '"' and not esc:
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    return s + ('"' if in_str else "") + "".join(reversed(stack))


def load_json_lenient(s: str) -> tuple[Any, bool]:
    """Return (value, repaired). value is None when nothing parses."""
    variants = [s, re.sub(r",\s*([}\]])", r"\1", s), _balance(re.sub(r",\s*([}\]])", r"\1", s))]
    if '"' not in s:
        variants.append(s.replace("'", '"'))
    for i, v in enumerate(variants):
        try:
            return json.loads(v), i > 0
        except json.JSONDecodeError:
            continue
    return None, False


def scan_objects(text: str):
    """Yield every balanced top-level {...} substring, string-aware."""
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            esc = (not esc and ch == "\\")
            if ch == '"' and not esc:
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield text[start : i + 1]


def _as_call(obj: Any, raw: str, repaired: bool) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    obj = obj.get("function", obj) if isinstance(obj.get("function"), dict) else obj
    name = obj.get("name")
    if not isinstance(name, str):
        return None
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(args, str):
        args, fixed = load_json_lenient(args)
        repaired = repaired or fixed
    return ToolCall(name, args if isinstance(args, dict) else {}, raw[:300], repaired)


def parse_calls(text: str) -> tuple[list[ToolCall], bool]:
    """Return (calls, truncated). A call cut off by the token limit is never executed."""
    text = THINK_RE.sub("", text)
    truncated = text.count("<tool_call>") > text.count("</tool_call>")
    chunks = TOOL_CALL_RE.findall(text) or FENCE_RE.findall(text)
    if not chunks and not truncated:
        chunks = [c for c in scan_objects(text) if '"name"' in c]
    calls: list[ToolCall] = []
    for chunk in chunks:
        obj, repaired = load_json_lenient(chunk)
        call = _as_call(obj, chunk, repaired)
        calls.append(call or ToolCall("__invalid__", {}, chunk[:200]))
    return calls, truncated


# ---------------------------------------------------------------------------
# Model backends: one interface, three engines, plus a scripted one for tests
# ---------------------------------------------------------------------------

STOP_MARKER = "<tool_response>"  # the model must never write tool results itself


def inject_tools(messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Add the tool list to the system message for engines without native tool templates."""
    block = "\n\n<tools>\n" + json.dumps(tools, ensure_ascii=False) + "\n</tools>"
    return [{**messages[0], "content": messages[0]["content"] + block}] + messages[1:]


def cut_hallucinated_results(text: str) -> str:
    return text.split(STOP_MARKER, 1)[0]


class Backend:
    name = "backend"

    async def generate(self, messages: list[dict[str, str]], tools: list[dict[str, Any]], *, max_new_tokens: int,
                       temperature: float, on_token: Callable[[str], None] | None = None) -> str:
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    async def aclose(self) -> None:
        return None


class TransformersBackend(Backend):
    def __init__(self, model_name: str) -> None:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList

        self.name = model_name
        self._stop = threading.Event()
        bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        self.tok = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name, torch_dtype=torch.bfloat16 if bf16 else torch.float16, device_map="auto", trust_remote_code=True
        ).eval()
        if self.tok.pad_token_id is None:
            self.tok.pad_token = self.tok.eos_token
        stop = self._stop

        class _Flag(StoppingCriteria):
            def __call__(self, input_ids, scores, **kw):  # noqa: ANN001
                return stop.is_set()

        self._criteria = StoppingCriteriaList([_Flag()])
        self._torch = torch
        self._supports_stop_strings = hasattr(transformers.GenerationConfig(), "stop_strings")

    def count_tokens(self, text: str) -> int:
        return len(self.tok.encode(text, add_special_tokens=False))

    def _prompt(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> str:
        try:
            return self.tok.apply_chat_template(messages, tools=tools, tokenize=False, add_generation_prompt=True)
        except Exception:
            return self.tok.apply_chat_template(inject_tools(messages, tools), tokenize=False, add_generation_prompt=True)

    async def generate(self, messages, tools, *, max_new_tokens, temperature, on_token=None) -> str:
        from transformers import TextIteratorStreamer

        self._stop.clear()
        inputs = self.tok(self._prompt(messages, tools), return_tensors="pt", add_special_tokens=False).to(self.model.device)
        streamer = TextIteratorStreamer(self.tok, skip_prompt=True, skip_special_tokens=True)
        kwargs: dict[str, Any] = dict(
            **inputs, max_new_tokens=max_new_tokens, do_sample=temperature > 0, temperature=max(temperature, 1e-5),
            top_p=0.9, repetition_penalty=1.05, pad_token_id=self.tok.pad_token_id, streamer=streamer,
            stopping_criteria=self._criteria,
        )
        if self._supports_stop_strings:
            kwargs.update(stop_strings=[STOP_MARKER], tokenizer=self.tok)

        def work() -> None:
            try:
                with self._torch.inference_mode():
                    self.model.generate(**kwargs)
            except Exception as e:  # unblock the reader instead of hanging forever
                print(f"[agent] generation failed: {type(e).__name__}: {e}")
                streamer.end()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        parts: list[str] = []
        it = iter(streamer)
        try:
            while (piece := await asyncio.to_thread(next, it, None)) is not None:
                parts.append(piece)
                if on_token:
                    on_token(piece)
        except asyncio.CancelledError:
            self._stop.set()
            raise
        finally:
            await asyncio.to_thread(thread.join, 10)
        return cut_hallucinated_results("".join(parts))


class OllamaBackend(Backend):
    def __init__(self, model: str, cfg: AgentConfig) -> None:
        import ollama

        self.name, self.cfg = f"ollama:{model}", cfg
        self.model = model
        self.client = ollama.AsyncClient(host=cfg.base_url or None)

    async def generate(self, messages, tools, *, max_new_tokens, temperature, on_token=None) -> str:
        stream = await self.client.chat(
            model=self.model, messages=inject_tools(messages, tools), stream=True, keep_alive="30m",
            options={"temperature": temperature, "num_predict": max_new_tokens, "num_ctx": self.cfg.context_tokens + max_new_tokens},
        )
        parts: list[str] = []
        async for chunk in stream:
            piece = chunk["message"]["content"]
            if piece:
                parts.append(piece)
                if on_token:
                    on_token(piece)
        return cut_hallucinated_results("".join(parts))


class OpenAICompatBackend(Backend):
    """Any server that speaks /v1/chat/completions: vLLM, llama.cpp, LM Studio, and others."""

    def __init__(self, model: str, cfg: AgentConfig) -> None:
        import httpx

        self.name, self.model = f"openai:{model}", model
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        self.client = httpx.AsyncClient(base_url=cfg.base_url or "http://127.0.0.1:8000/v1", headers=headers, timeout=None)

    async def generate(self, messages, tools, *, max_new_tokens, temperature, on_token=None) -> str:
        body = {"model": self.model, "messages": inject_tools(messages, tools), "stream": True,
                "max_tokens": max_new_tokens, "temperature": temperature, "stop": [STOP_MARKER]}
        parts: list[str] = []
        async with self.client.stream("POST", "/chat/completions", json=body) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {(await r.aread()).decode()[:300]}")
            async for line in r.aiter_lines():
                if not line.startswith("data:") or line.endswith("[DONE]"):
                    continue
                piece = (json.loads(line[5:]).get("choices") or [{}])[0].get("delta", {}).get("content")
                if piece:
                    parts.append(piece)
                    if on_token:
                        on_token(piece)
        return cut_hallucinated_results("".join(parts))

    async def aclose(self) -> None:
        await self.client.aclose()


class ScriptedBackend(Backend):
    """Replays fixed replies; lets the whole agent loop be tested without a model or GPU."""

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = deque(replies)

    async def generate(self, messages, tools, *, max_new_tokens, temperature, on_token=None) -> str:
        text = self.replies.popleft() if self.replies else ""
        if on_token and text:
            on_token(text)
        return text


def make_backend(cfg: AgentConfig) -> Backend:
    prefix, _, rest = cfg.model_name.partition(":")
    if prefix == "ollama" and rest:
        return OllamaBackend(rest, cfg)
    if prefix == "openai" and rest:
        return OpenAICompatBackend(rest, cfg)
    return TransformersBackend(cfg.model_name)


# ---------------------------------------------------------------------------
# Context management: keep the conversation inside the model's window
# ---------------------------------------------------------------------------

FALLBACK_PROMPT = """You are an autonomous senior software engineer in a sandboxed workspace.
You complete tasks by calling tools; you do the work, you do not just describe it.
Call tools as <tool_call>{{"name": "<tool>", "arguments": {{...}}}}</tool_call>, one or more per turn.
Tools:
{tools}
Rules:
- Wait for <tool_response> before the next step; never invent tool output; report only what tools confirm.
- Put file contents in write_file or edit_file, never in chat. Keep files under 300 lines.
- Start with a short plan (plan tool), then build, then verify (verify tool, run_command), fix failures, then finish.
- Write complete working code with no placeholders or TODOs.
- Names, ids, and paths must match across files. Stay inside the workspace.
- Between tool calls write at most two short lines."""


def _tool_lines(definitions: list[dict[str, Any]]) -> str:
    lines = []
    for d in definitions:
        f = d["function"]
        lines.append(f"- {f['name']}({', '.join(f['parameters']['properties'])}): {f['description'].split('.')[0]}")
    return "\n".join(lines)


class ContextManager:
    def __init__(self, backend: Backend, cfg: AgentConfig) -> None:
        self.backend, self.cfg, self.removed_total = backend, cfg, 0

    def tokens(self, messages: list[dict[str, str]]) -> int:
        return sum(self.backend.count_tokens(m["content"]) + 8 for m in messages)

    def compact(self, messages: list[dict[str, str]], task: str) -> int:
        """Shrink old turns first, then drop the oldest assistant/user pairs. Returns messages removed."""
        if self.tokens(messages) <= self.cfg.context_tokens:
            return 0
        keep = self.cfg.keep_last_messages
        for m in messages[2:-keep]:
            m["content"] = truncate(m["content"], 400)
        removed = 0
        while self.tokens(messages) > self.cfg.context_tokens and len(messages) > 2 + keep:
            del messages[2:4]  # one assistant turn and its tool response, so roles keep alternating
            removed += 2
        if removed:
            self.removed_total += removed
            messages[1]["content"] = (
                f"{task}\n\n[context note: {self.removed_total} older messages were trimmed. "
                "Rely on the plan, list_files, and read_file for current state.]"
            )
        return removed


# ---------------------------------------------------------------------------
# AgenticCoder: the agent loop
# ---------------------------------------------------------------------------


class AgenticCoder:
    def __init__(
        self,
        model_name: str,
        workspace: str = "workspace",
        max_steps: int = 25,
        max_new_tokens: int = 3072,
        temperature: float = 0.2,
        cmd_timeout: int = 30,
        *,
        backend: Backend | None = None,
        events: Events | None = None,
        **overrides: Any,
    ) -> None:
        self.cfg = AgentConfig(
            model_name=model_name, workspace=workspace, max_steps=max_steps, max_new_tokens=max_new_tokens,
            temperature=temperature, cmd_timeout=cmd_timeout, **overrides,
        )
        self.ws = Workspace(self.cfg.workspace, self.cfg)
        self.runner = CommandRunner(self.ws, self.cfg)
        self.verifier = Verifier(self.ws, self.runner)
        self.events = events or Events()
        if self.cfg.verbose:
            self.events.subscribe(ConsoleReporter(self.cfg.stream))
        if self.cfg.log_trajectory:
            self.events.subscribe(TrajectoryLogger(self.ws.meta / "trajectory.jsonl"))
        self.events.emit("loading", model=self.cfg.model_name)
        self.backend = backend or make_backend(self.cfg)
        self.ctx = ContextManager(self.backend, self.cfg)
        self.state = AgentState()

    # -- helpers -------------------------------------------------------------
    def _system_prompt(self, task: str, definitions: list[dict[str, Any]]) -> str:
        if self.cfg.system_prompt:
            return self.cfg.system_prompt
        try:
            from agent_prompt import build_system_prompt

            return build_system_prompt(task, definitions, max_tokens=int(self.cfg.context_tokens * 0.35))
        except ImportError:
            return FALLBACK_PROMPT.format(tools=_tool_lines(definitions))

    def _save(self, state: AgentState) -> None:
        snapshot = {k: getattr(state, k) for k in ("task", "messages", "plan", "steps", "written")}
        (self.ws.meta / "state.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    def _resume(self, state: AgentState) -> bool:
        path = self.ws.meta / "state.json"
        if not path.is_file():
            return False
        saved = json.loads(path.read_text(encoding="utf-8"))
        for key in ("messages", "plan", "steps", "written"):
            setattr(state, key, saved[key])
        return True

    def _finalize(self, state: AgentState, started: float) -> None:
        state.changes = self.ws.changes()
        state.stop_reason = state.stop_reason or ("finished" if state.finished else "max_steps")
        duration = round(time.monotonic() - started, 1)
        report = {
            "task": state.task, "model": self.cfg.model_name, "steps": state.steps, "finished": state.finished,
            "stop_reason": state.stop_reason, "summary": state.summary, "changes": state.changes,
            "plan": state.plan, "checks": state.checks, "metrics": state.metrics, "duration_s": duration,
        }
        (self.ws.meta / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        self.events.emit("done", stop_reason=state.stop_reason, steps=state.steps, duration_s=duration,
                         summary=state.summary, changes=state.changes)

    # -- the loop --------------------------------------------------------------
    async def run(self, task: str, resume: bool = False) -> AgentState:
        cfg = self.cfg
        state = self.state = AgentState(task=task)
        resumed = resume and self._resume(state)
        box = ToolBox(self.ws, self.runner, self.verifier, state, cfg)
        if not resumed:
            state.messages = [
                {"role": "system", "content": self._system_prompt(task, box.definitions)},
                {"role": "user", "content": task},
            ]
        self.events.emit("start", model=self.backend.name, workspace=str(self.ws.root))
        started = time.monotonic()
        try:
            await self._loop(state, box, started + cfg.timeout_s)
        except asyncio.CancelledError:
            state.stop_reason = "cancelled"
            raise
        finally:
            self._finalize(state, started)
        return state

    async def _loop(self, state: AgentState, box: ToolBox, deadline: float) -> None:
        cfg = self.cfg
        signatures: deque[str] = deque(maxlen=cfg.stall_stop)
        idle = 0
        while state.steps < cfg.max_steps and not state.finished:
            if time.monotonic() > deadline:
                state.stop_reason = "timeout"
                return
            state.steps += 1
            trimmed = self.ctx.compact(state.messages, state.task)
            self.events.emit("step", n=state.steps, trimmed=trimmed, tokens=self.ctx.tokens(state.messages))
            try:
                reply = await self.backend.generate(
                    state.messages, box.definitions, max_new_tokens=cfg.max_new_tokens,
                    temperature=min(cfg.temperature + 0.1 * state.invalid_streak, 0.7),
                    on_token=lambda t: self.events.emit("token", text=t),
                )
            except Exception as e:
                hint = " (out of GPU memory: lower max_new_tokens or context_tokens)" if "out of memory" in str(e).lower() else ""
                state.stop_reason = f"backend_error: {type(e).__name__}: {e}{hint}"[:300]
                return
            state.metrics["tokens_out"] += self.backend.count_tokens(reply)
            clean = THINK_RE.sub("", reply).strip()
            state.messages.append({"role": "assistant", "content": clean or "(no output)"})

            calls, truncated = parse_calls(clean)
            if not calls:
                idle += 1
                state.invalid_streak += 1
                if idle >= 3:
                    state.stop_reason = "no_tool_calls"
                    return
                note = ("Your last reply was cut off inside a tool call, so it was NOT run. Resend it with smaller files (under 150 lines each)."
                        if truncated else "No valid tool call found. Reply with <tool_call>{...}</tool_call>. Call finish when done.")
                state.messages.append({"role": "user", "content": note})
                continue
            idle = 0

            blocks: list[str] = []
            for call in calls:
                if call.name == "__invalid__":
                    result = ToolResult(False, f"invalid tool call JSON: {call.raw[:120]}")
                    state.invalid_streak += 1
                else:
                    self.events.emit("tool_call", name=call.name, arguments=call.arguments)
                    result = await box.dispatch(call.name, call.arguments)
                    state.invalid_streak = 0
                state.metrics["tool_calls"] += 1
                state.metrics["tool_errors"] += not result.ok
                state.error_streak = 0 if result.ok else state.error_streak + 1
                self.events.emit("tool_result", name=call.name, ok=result.ok, text=result.text)
                blocks.append("<tool_response>\n" + json.dumps(
                    {"name": call.name, "ok": result.ok, "result": truncate(result.text, cfg.max_output_chars)}, ensure_ascii=False
                ) + "\n</tool_response>")
                signatures.append(call.name + json.dumps(call.arguments, sort_keys=True, default=str))
                if state.finished:
                    return

            repeats = sum(1 for s in reversed(signatures) if s == signatures[-1]) if signatures else 0
            if repeats >= cfg.stall_stop:
                state.stop_reason = "stalled"
                return
            if state.error_streak >= cfg.max_error_streak:
                state.stop_reason = "too_many_errors"
                return
            if repeats >= cfg.stall_warn:
                blocks.append("[warning] You repeated the same call several times with no progress. Change your approach.")
                self.events.emit("warning", text="repeated tool call detected")
            if truncated:
                blocks.append("[warning] A later tool call was cut off and NOT run. Resend it with smaller files.")
            status = f"[status] step {state.steps}/{cfg.max_steps}, files written: {len(state.written)}"
            blocks.append(status + ("\nPlan:\n" + box.plan_text() if state.plan else ""))
            state.messages.append({"role": "user", "content": "\n".join(blocks)})
            self._save(state)


# ---------------------------------------------------------------------------
# CLI and offline self-test
# ---------------------------------------------------------------------------


async def selftest() -> bool:
    """Drive the real loop with scripted replies: truncation, aliases, a failing gate, an edit, then success."""
    call = lambda name, **args: "<tool_call>" + json.dumps({"name": name, "arguments": args}) + "</tool_call>"  # noqa: E731
    replies = [
        '<tool_call>{"name": "write_file", "arguments": {"path": "x.py", "content": "abc',  # cut off: must not run
        call("plan", steps=["write script", "verify"]) + call("create_file", filename="hello.py", content="print('hi'\n"),
        call("finish", summary="premature"),  # gate must reject: hello.py has a syntax error
        call("edit_file", path="hello.py", old_str="print('hi'", new_str="print('hi')") + call("run_command", command="python hello.py"),
        call("finish", summary="hello.py prints hi; verified by running it"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        agent = AgenticCoder("scripted", workspace=tmp, backend=ScriptedBackend(replies), verbose=False, log_trajectory=False)
        state = await agent.run("Create hello.py that prints hi")
        root = Path(tmp)
        checks = {
            "finished": state.finished,
            "gate rejected once": state.gate_attempts == 1,
            "truncated call not executed": not (root / "x.py").exists(),
            "file fixed": (root / "hello.py").read_text() == "print('hi')\n",
            "change tracking": state.changes["added"] == ["hello.py"],
            "report written": (root / META_DIR / "report.json").is_file(),
            "schema generated": any(d["function"]["name"] == "edit_file" for d in ToolBox(agent.ws, agent.runner, agent.verifier, state, agent.cfg).definitions),
        }
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return all(checks.values())


def main() -> int:
    ap = argparse.ArgumentParser(description="Autonomous local coding agent")
    ap.add_argument("--model", default="DuoNeural/LFM2.5-8B-A1B-Hermes-Agentic-Coder-Abliterated-v2",
                    help="HF repo id, 'ollama:<model>', or 'openai:<model>'")
    ap.add_argument("--task", help="what to build")
    ap.add_argument("--workspace", default="workspace")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--base-url", default="", help="server URL for ollama/openai backends")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--resume", action="store_true", help="continue from workspace/.agent/state.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="run the offline scripted test and exit")
    args = ap.parse_args()

    if args.selftest:
        return 0 if asyncio.run(selftest()) else 1
    if not args.task:
        ap.error("--task is required (or use --selftest)")
    agent = AgenticCoder(args.model, args.workspace, args.max_steps, base_url=args.base_url, api_key=args.api_key, verbose=not args.quiet)
    state = asyncio.run(agent.run(args.task, resume=args.resume))
    return 0 if state.finished else 1


if __name__ == "__main__":
    raise SystemExit(main())