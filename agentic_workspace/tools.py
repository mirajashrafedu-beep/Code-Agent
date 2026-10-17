# tools.py
import fnmatch
import inspect
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import types
import typing
from pathlib import Path
from typing import Any, Callable, get_type_hints

WORKSPACE_ROOT = Path(os.environ.get("AGENT_WORKSPACE", "./output")).resolve()
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 6000
MAX_TIMEOUT = 120
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}

ALLOWED_COMMANDS = {
    "python", "python3", "pytest", "node", "npm", "npx", "tsc", "eslint", "ruff", "mypy", "black",
    "ls", "cat", "head", "tail", "wc", "grep", "echo", "pwd", "diff", "sort", "uniq",
}
BLOCKED_FLAGS = {"-c", "-e", "--eval", "--exec", "-p", "--print"}
SHELL_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "&", "`", "$("}

# NOTE: these are guardrails, not a security boundary. Run untrusted agents inside a container or VM.


class ToolError(Exception):
    """Raised for expected, model-readable failures."""


# ---------------- helpers ----------------
def _safe_path(rel: str) -> Path:
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise ToolError("invalid path")
    p = (WORKSPACE_ROOT / rel).resolve()
    if p != WORKSPACE_ROOT and not p.is_relative_to(WORKSPACE_ROOT):
        raise ToolError(f"path escapes workspace: {rel}")
    return p


def _rel(p: Path) -> str:
    return p.relative_to(WORKSPACE_ROOT).as_posix() or "."


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.6), int(limit * 0.4)
    return f"{text[:head]}\n... [{len(text) - limit} chars truncated] ...\n{text[-tail:]}"


def _atomic_write(path: Path, text: str, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if append and path.exists():
        text = path.read_text(encoding="utf-8") + text
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError(f"file too large (limit {MAX_FILE_BYTES} bytes)")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _quick_validate(path: Path, text: str) -> str:
    try:
        if path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".py":
            compile(text, str(path), "exec")
    except (json.JSONDecodeError, SyntaxError) as e:
        return f" WARNING: {type(e).__name__}: {e}. Fix this file."
    return ""


def _is_binary(path: Path) -> bool:
    with path.open("rb") as f:
        return b"\x00" in f.read(1024)


# ---------------- registry with auto-generated schemas ----------------
_REGISTRY: dict[str, Callable[..., str]] = {}
_SCHEMAS: list[dict] = []
_SPECS: dict[str, dict] = {}
_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def _json_type(tp: Any) -> dict:
    origin = typing.get_origin(tp)
    if origin in (typing.Union, getattr(types, "UnionType", None)):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _json_type(args[0]) if args else {"type": "string"}
    if origin is list:
        args = typing.get_args(tp)
        return {"type": "array", "items": _json_type(args[0]) if args else {"type": "string"}}
    return {"type": _JSON_TYPES.get(tp, "string")}


def _parse_doc(doc: str | None) -> tuple[str, dict[str, str]]:
    doc = inspect.cleandoc(doc or "")
    desc, _, args_block = doc.partition("Args:")
    params = {m.group(1): m.group(2).strip() for m in re.finditer(r"^\s*(\w+)\s*:\s*(.+)$", args_block, re.M)}
    return desc.strip(), params


def tool(fn: Callable[..., str]) -> Callable[..., str]:
    hints = get_type_hints(fn)
    desc, pdocs = _parse_doc(fn.__doc__)
    props: dict[str, dict] = {}
    required: list[str] = []
    for name, p in inspect.signature(fn).parameters.items():
        schema = _json_type(hints.get(name, str))
        if name in pdocs:
            schema["description"] = pdocs[name]
        if p.default is inspect.Parameter.empty:
            required.append(name)
        elif p.default is not None:
            schema["default"] = p.default
        props[name] = schema
    _REGISTRY[fn.__name__] = fn
    _SPECS[fn.__name__] = {"props": props, "required": required}
    _SCHEMAS.append(
        {
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }
    )
    return fn


# ---------------- tools ----------------
@tool
def write_file(filename: str, content: str, append: bool = False) -> str:
    """Create or overwrite a file in the workspace. Parent folders are created automatically.

    Args:
        filename: Relative file path, e.g. 'src/popup.js'
        content: The complete file content to write
        append: If true, append to the file instead of overwriting
    """
    path = _safe_path(filename)
    if path.is_dir():
        raise ToolError(f"{filename} is a directory")
    existed = path.exists()
    _atomic_write(path, content, append)
    final = path.read_text(encoding="utf-8")
    return (
        f"{'Updated' if existed else 'Created'} {_rel(path)} "
        f"({len(final.splitlines())} lines, {len(final.encode('utf-8'))} bytes)." + _quick_validate(path, final)
    )


@tool
def read_file(filename: str, start_line: int = 1, end_line: int = 0, numbered: bool = False) -> str:
    """Read a file from the workspace, optionally only a line range.

    Args:
        filename: Relative path to the file
        start_line: First line to return (1-based)
        end_line: Last line to return, 0 means until the end
        numbered: Prefix each line with its line number
    """
    path = _safe_path(filename)
    if not path.is_file():
        raise ToolError(f"{filename} does not exist")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError("file too large to read, use start_line/end_line or search_files")
    if _is_binary(path):
        raise ToolError("binary file, cannot display")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1)
    end = end_line if end_line > 0 else len(lines)
    chunk = lines[start - 1 : end]
    if numbered:
        chunk = [f"{i}\t{line}" for i, line in enumerate(chunk, start)]
    return _truncate("\n".join(chunk)) or "(empty file)"


@tool
def edit_file(filename: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Replace exact text in an existing file. old_str must match exactly and be unique unless replace_all is set.

    Args:
        filename: Relative path to the file
        old_str: Exact text to find, including whitespace
        new_str: Replacement text
        replace_all: Replace every occurrence instead of requiring a unique match
    """
    path = _safe_path(filename)
    if not path.is_file():
        raise ToolError(f"{filename} does not exist")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_str) if old_str else 0
    if count == 0:
        raise ToolError("old_str not found, read the file again and copy the text exactly")
    if count > 1 and not replace_all:
        raise ToolError(f"old_str matches {count} places, add more context or set replace_all=true")
    new_text = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    _atomic_write(path, new_text)
    return f"Edited {_rel(path)}: {count if replace_all else 1} replacement(s)." + _quick_validate(path, new_text)


@tool
def list_files(directory: str = ".", max_depth: int = 4) -> str:
    """List files in the workspace as a tree with sizes.

    Args:
        directory: Relative directory to list
        max_depth: How many folder levels to descend
    """
    root = _safe_path(directory)
    if not root.is_dir():
        raise ToolError(f"{directory} is not a directory")
    out: list[str] = []
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS) if depth < max_depth else []
        for f in sorted(files):
            p = Path(current) / f
            out.append(f"{_rel(p)}  ({p.stat().st_size} B)")
            if len(out) >= 300:
                return "\n".join(out) + "\n... [list truncated at 300 files]"
    return "\n".join(out) or "(no files)"


@tool
def search_files(pattern: str, directory: str = ".", glob: str = "*", max_results: int = 50) -> str:
    """Search file contents with a regular expression, like grep -rn.

    Args:
        pattern: Regular expression to search for
        directory: Relative directory to search in
        glob: Only search files whose name matches, e.g. '*.js'
        max_results: Maximum matching lines to return
    """
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}")
    root = _safe_path(directory)
    hits: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in sorted(files):
            p = Path(current) / f
            if not fnmatch.fnmatch(f, glob) or p.stat().st_size > MAX_FILE_BYTES or _is_binary(p):
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{_rel(p)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + "\n... [max_results reached]"
    return "\n".join(hits) or "No matches."


@tool
def delete_file(filename: str) -> str:
    """Delete a single file from the workspace.

    Args:
        filename: Relative path to the file
    """
    path = _safe_path(filename)
    if not path.is_file():
        raise ToolError(f"{filename} is not a file")
    path.unlink()
    return f"Deleted {_rel(path)}."


def _validate_command(args: list[str]) -> None:
    if not args:
        raise ToolError("empty command")
    if args[0] not in ALLOWED_COMMANDS:
        raise ToolError(f"command '{args[0]}' not allowed. Allowed: {sorted(ALLOWED_COMMANDS)}")
    if shutil.which(args[0]) is None:
        raise ToolError(f"'{args[0]}' is not installed on this machine")
    for a in args[1:]:
        if a in SHELL_TOKENS:
            raise ToolError(f"shell operator '{a}' is not supported, run one command per call")
        if a in BLOCKED_FLAGS and args[0] in {"python", "python3", "node"}:
            raise ToolError(f"flag '{a}' is blocked, write the code to a file and run the file")
        if not a.startswith("-") and (os.path.isabs(a) or ".." in Path(a).parts):
            raise ToolError(f"argument '{a}' points outside the workspace")


@tool
def execute_command(cmd: str, timeout: int = 15) -> str:
    """Run one allow-listed command in the workspace (tests, linters, syntax checks). No pipes or shell operators.

    Args:
        cmd: Command line, e.g. 'node --check background.js' or 'pytest -q'
        timeout: Seconds before the command is killed
    """
    try:
        args = shlex.split(cmd)
    except ValueError as e:
        raise ToolError(f"cannot parse command: {e}")
    _validate_command(args)
    timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(WORKSPACE_ROOT),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    posix = os.name == "posix"
    start = time.perf_counter()
    proc = subprocess.Popen(
        args, cwd=WORKSPACE_ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=posix,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL) if posix else proc.kill()
        proc.communicate()
        raise ToolError(f"command timed out after {timeout}s and was killed")
    elapsed = time.perf_counter() - start
    body = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part) or "(no output)"
    return f"exit_code={proc.returncode} ({elapsed:.1f}s)\n{_truncate(body)}"


# ---------------- dispatcher ----------------
_ALIASES = {"path": "filename", "file": "filename", "file_path": "filename", "command": "cmd", "text": "content"}


def _coerce(value: Any, schema: dict) -> Any:
    t = schema.get("type")
    try:
        if t == "integer" and not isinstance(value, bool):
            return int(value)
        if t == "number":
            return float(value)
        if t == "boolean" and isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        if t == "string" and not isinstance(value, str):
            return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    except (TypeError, ValueError):
        pass
    return value


def execute_tool(name: str, arguments: dict | str | None = None) -> str:
    """Validate, coerce and run a tool call. Always returns a string the model can read."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'. Available: {sorted(_REGISTRY)}"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as e:
            return f"Error: arguments are not valid JSON: {e}"
    if not isinstance(arguments, dict):
        arguments = {}

    spec = _SPECS[name]
    args: dict[str, Any] = {}
    for key, value in arguments.items():
        target = key if key in spec["props"] else _ALIASES.get(key)
        if target in spec["props"] and target not in args:
            args[target] = _coerce(value, spec["props"][target])
    missing = [r for r in spec["required"] if r not in args]
    if missing:
        return f"Error: missing required argument(s): {missing}. Expected: {list(spec['props'])}"

    try:
        return fn(**args)
    except ToolError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


TOOL_DEFINITIONS = list(_SCHEMAS)
TOOL_MAP = dict(_REGISTRY)


if __name__ == "__main__":
    print(execute_tool("write_file", {"filename": "demo/hello.py", "content": "print('hi')\n"}))
    print(execute_tool("edit_file", {"filename": "demo/hello.py", "old_str": "hi", "new_str": "hello"}))
    print(execute_tool("execute_command", {"cmd": "python demo/hello.py"}))
    print(execute_tool("write_file", {"filename": "../evil.txt", "content": "x"}))
    print(execute_tool("list_files", {}))
    print(json.dumps(TOOL_DEFINITIONS[0], indent=2))