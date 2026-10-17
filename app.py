"""
Local Coder Backend - resilient FastAPI interface for Ollama.
Features: connection monitor, auto-start, retry with backoff, heartbeats for proxies/tunnels,
fair request queueing, client-disconnect cancellation, API-key auth, rate limiting,
model pulling with progress, photo input for vision models, file upload (PDF, DOCX, PPTX,
XLSX, ZIP, any text/code file) and an optional (auth-protected) ngrok tunnel.

Optional installs for more formats:  pip install pypdf defusedxml
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import io
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import ollama
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

try:  # safer XML parsing for untrusted Office files
    from defusedxml import ElementTree as ET
except ImportError:
    from xml.etree import ElementTree as ET

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("coder")


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
    ollama_host: str | None = os.environ.get("OLLAMA_URL") or None
    num_ctx: int = int(os.environ.get("OLLAMA_CTX", "32768"))
    max_ctx: int = int(os.environ.get("OLLAMA_MAX_CTX", "131072"))
    default_predict: int = int(os.environ.get("DEFAULT_MAX_TOKENS", "-1"))
    keep_alive: str = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
    bind: str = os.environ.get("BIND_HOST", "127.0.0.1")
    port: int = int(os.environ.get("PORT", "8000"))
    reload: bool = _bool("RELOAD", False)
    api_key: str = os.environ.get("API_KEY", "")
    cors: tuple = tuple(o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip())
    max_concurrent: int = int(os.environ.get("MAX_CONCURRENT", "2"))
    queue_timeout: float = float(os.environ.get("QUEUE_TIMEOUT", "120"))
    max_generation: float = float(os.environ.get("MAX_GENERATION_SECONDS", "0"))
    rate_per_min: int = int(os.environ.get("RATE_PER_MIN", "60"))
    trust_proxy: bool = _bool("TRUST_PROXY", False)
    allow_open_tunnel: bool = _bool("ALLOW_OPEN_TUNNEL", False)
    retries: int = int(os.environ.get("CONNECT_RETRIES", "3"))
    ping_interval: float = float(os.environ.get("PING_INTERVAL", "10"))
    health_interval: float = float(os.environ.get("HEALTH_INTERVAL", "15"))
    health_timeout: float = float(os.environ.get("HEALTH_TIMEOUT", "5"))
    autostart: bool = _bool("OLLAMA_AUTOSTART", False)
    hf_prefix: str = os.environ.get("HF_PREFIX", "hf.co/")
    max_messages: int = int(os.environ.get("MAX_MESSAGES", "0"))  # 0 = unlimited
    trim_history: bool = _bool("TRIM_HISTORY", False)  # False = send full history
    max_body: int = int(os.environ.get("MAX_BODY_BYTES", "0"))  # 0 = unlimited
    chars_per_token: float = 3.0  # conservative estimate for code
    max_images: int = int(os.environ.get("MAX_IMAGES", "8"))
    max_image_b64: int = int(os.environ.get("MAX_IMAGE_BYTES", "12000000"))
    max_upload: int = int(os.environ.get("MAX_UPLOAD_BYTES", "25000000"))
    max_text: int = int(os.environ.get("MAX_UPLOAD_CHARS", "300000"))


S = Settings()

# NOTE: these only work if the HF repo ships GGUF files (Ollama's hf.co registry requirement).
EXTRA_MODELS = [
    "DuoNeural/LFM2.5-8B-A1B-Hermes-Agentic-Coder-Abliterated-v2",
    "LLM-OS-Models/Fabliq-8B-Agent-Reasoning",
    "ByteDance-Seed/BAGEL-7B-MoT",  # multimodal, no GGUF -> pull will likely fail
]
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,199}$")

CHAT_PROMPT = (
    "You are a helpful, accurate and concise assistant. Answer in the language the "
    "user writes in. When you show code, put it in fenced code blocks."
)

CODE_PROMPT = """You are an expert autonomous software engineer specializing in advanced, production-quality software development.
Target language/stack: {language}.
Rules:
- Reply with complete, working, production-quality code in fenced code blocks (```lang).
- Never use placeholders such as TODO or "...".
- If the task needs several files, use one fenced block per file and put the file name in a comment on the first line (for example "# app.py" or "// server.js").
- For anything that runs in a browser, produce ONE self-contained HTML file with inline CSS and JavaScript.
- Add short helpful comments, input validation and error handling.
- After the code add at most three short lines explaining how to run it. No long explanations.
"""

AUTO_LANGUAGE = (
    "choose the best language and stack for the task (HTML+CSS+JS for websites and "
    "browser games; Python for scripts, data, bots and APIs)"
)

STATIC_HTML_PATH = Path(__file__).parent / "index.html"

client = ollama.AsyncClient(
    host=S.ollama_host,
    timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=None),
)
gate = asyncio.Semaphore(S.max_concurrent)
STARTED_AT = time.time()
_active = 0  # generations currently holding a slot
_pulling = False


# ---------------- connection state ----------------
@dataclass
class ConnState:
    ok: bool = False
    latency_ms: float | None = None
    checked_at: float = 0.0
    error: str = ""
    failures: int = 0
    installed: list[str] = field(default_factory=list)


state = ConnState()
_ollama_proc: subprocess.Popen | None = None
_tunnel_url: str | None = None


def g(obj: Any, key: str, default: Any = None) -> Any:
    """Read a key from a dict or an Ollama response object."""
    if obj is None:
        return default
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError):
        return getattr(obj, key, default)


def model_names(data: Any) -> list[str]:
    names = []
    for m in g(data, "models", []) or []:
        name = g(m, "model") or g(m, "name")
        if name:
            names.append(name)
    return names


async def ping() -> bool:
    t0 = time.perf_counter()
    try:
        data = await asyncio.wait_for(client.list(), timeout=S.health_timeout)
        state.ok, state.error, state.failures = True, "", 0
        state.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        state.installed = model_names(data)
    except Exception as exc:
        state.ok, state.latency_ms = False, None
        state.error = f"{type(exc).__name__}: {exc}"[:300]
        state.failures += 1
    state.checked_at = time.time()
    return state.ok


async def ensure_ollama() -> bool:
    """Ping Ollama; if it is down and autostart is on, launch `ollama serve` and wait for it."""
    global _ollama_proc
    if await ping():
        return True
    if not S.autostart or not shutil.which("ollama"):
        return False
    if _ollama_proc is None or _ollama_proc.poll() is not None:
        log.info("Starting ollama serve ...")
        _ollama_proc = subprocess.Popen(
            ["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
    for _ in range(30):
        await asyncio.sleep(1)
        if await ping():
            log.info("Ollama is up (%.0f ms)", state.latency_ms or 0)
            return True
    return False


async def monitor_loop() -> None:
    while True:
        try:
            was_ok = state.ok
            ok = await ping()
            if ok != was_ok:
                log.info("Ollama connection %s%s", "RESTORED" if ok else "LOST", "" if ok else f": {state.error}")
            if not ok and state.failures >= 3 and S.autostart:
                await ensure_ollama()
            await asyncio.sleep(S.health_interval if ok else min(S.health_interval, 5))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("monitor loop error")
            await asyncio.sleep(5)


def open_tunnel() -> str | None:
    token = os.environ.get("NGROK_AUTHTOKEN")
    if not token:
        return None
    if not S.api_key and not S.allow_open_tunnel:
        log.error("Refusing to open a public tunnel without API_KEY (set API_KEY, or ALLOW_OPEN_TUNNEL=1 to override).")
        return None
    try:
        from pyngrok import ngrok

        ngrok.set_auth_token(token)
        return ngrok.connect(S.port).public_url
    except Exception as exc:
        log.warning("Tunnel failed: %s", exc)
        return None


def _stop_ollama() -> None:
    if _ollama_proc and _ollama_proc.poll() is None:
        _ollama_proc.terminate()
        try:
            _ollama_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _ollama_proc.kill()
            _ollama_proc.wait()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tunnel_url
    if S.bind not in ("127.0.0.1", "localhost", "::1") and not S.api_key:
        log.warning("Listening on %s with no API_KEY: anyone on the network can use this server.", S.bind)
    await ensure_ollama()
    monitor = asyncio.create_task(monitor_loop())
    _tunnel_url = await asyncio.to_thread(open_tunnel)
    if _tunnel_url:
        log.info("Public URL: %s", _tunnel_url)
    try:
        yield
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
        if _tunnel_url:
            with suppress(Exception):
                from pyngrok import ngrok

                ngrok.kill()
        await asyncio.to_thread(_stop_ollama)


app = FastAPI(title="Local Coder", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(S.cors),
    allow_credentials=S.cors != ("*",),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    # one consistent error shape for the UI ("error"), keep "detail" for compatibility
    return JSONResponse(
        {"error": exc.detail, "detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


# ---------------- security ----------------
_hits: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    if S.trust_proxy or _tunnel_url:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            # rightmost entry = the one added by our (single) trusted proxy; leftmost is spoofable
            return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _prune_hits(now: float) -> None:
    for ip in [ip for ip, q in _hits.items() if not q or now - q[-1] > 60]:
        del _hits[ip]


async def guard(request: Request) -> None:
    # rate-limit FIRST so failed key guesses are throttled too
    now = time.monotonic()
    if len(_hits) > 1000:
        _prune_hits(now)
    q = _hits[client_ip(request)]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= S.rate_per_min:
        raise HTTPException(429, "Rate limit exceeded.", headers={"Retry-After": str(int(60 - (now - q[0])) + 1)})
    q.append(now)

    if S.api_key:
        auth = request.headers.get("authorization", "")
        supplied = request.headers.get("x-api-key") or (auth[7:] if auth.lower().startswith("bearer ") else "")
        if not hmac.compare_digest(supplied.strip().encode(), S.api_key.encode()):
            raise HTTPException(401, "Invalid or missing API key.")


async def read_json(request: Request) -> dict:
    cl = request.headers.get("content-length")
    if S.max_body > 0 and cl and cl.isdigit() and int(cl) > S.max_body:
        raise HTTPException(413, "Request body too large.")
    buf = bytearray()
    async for part in request.stream():
        buf += part
        if S.max_body > 0 and len(buf) > S.max_body:
            raise HTTPException(413, "Request body too large.")
    try:
        data = json.loads(buf or b"{}")
    except ValueError:
        raise HTTPException(400, "Body must be valid JSON.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Body must be a JSON object.")
    return data


# ---------------- helpers ----------------
def resolve_model(name: str) -> str:
    """HF repo ids from EXTRA_MODELS are pulled through Ollama's hf.co registry."""
    name = name.strip()
    return f"{S.hf_prefix}{name}" if name in EXTRA_MODELS else name


def short_name(installed: str) -> str:
    """Map an installed 'hf.co/org/repo[:latest]' name back to its EXTRA_MODELS id."""
    for extra in EXTRA_MODELS:
        full = resolve_model(extra)
        if installed in (full, f"{full}:latest"):
            return extra
    return installed


_B64 = re.compile(r"[A-Za-z0-9+/=\s]+")


def clean_images(raw: Any) -> list[str]:
    """Accept data-URLs or bare base64 strings; return bare base64 (what Ollama expects)."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        data = item.split(",", 1)[1] if item.startswith("data:") and "," in item else item
        data = data.strip()
        if data and len(data) <= S.max_image_b64 and _B64.fullmatch(data):
            out.append(data)
    return out[: S.max_images]


def clean_messages(raw: Any, max_chars: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        imgs = clean_images(m.get("images")) if role == "user" else []
        if role in ("user", "assistant") and isinstance(content, str) and (content.strip() or imgs):
            item: dict[str, Any] = {"role": role, "content": content}
            if imgs:
                item["images"] = imgs
            out.append(item)
    if S.max_messages > 0:
        out = out[-S.max_messages:]
    if max_chars <= 0:  # unlimited input
        return out
    while len(out) > 1 and sum(len(x["content"]) for x in out) > max_chars:
        out.pop(0)
    while len(out) > 1 and out[0]["role"] == "assistant":
        out.pop(0)
    if out and len(out[-1]["content"]) > max_chars:  # a single oversized message: keep its tail
        out[-1] = {**out[-1], "content": "[...truncated...]\n" + out[-1]["content"][-max_chars:]}
    return out


def bounded(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def explain(exc: BaseException, model: str) -> str:
    if isinstance(exc, ollama.ResponseError):
        if exc.status_code == 404:
            return f"Model '{model}' is not installed. Run: ollama pull {resolve_model(model)} (or POST /api/models/pull)."
        msg = exc.error or "Ollama returned an error."
        if "image" in msg.lower() or "vision" in msg.lower():
            msg += " (Photos need a vision model such as llava, llama3.2-vision, qwen2.5vl or gemma3.)"
        return msg
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError)):
        return "Cannot reach Ollama. Start it with: ollama serve (or set OLLAMA_AUTOSTART=1)."
    if isinstance(exc, httpx.ReadTimeout):
        return "Ollama stopped responding (read timeout)."
    return f"{type(exc).__name__}: {exc}"


# ---------------- file text extraction ----------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _runs(node: Any, ns: str, para: str) -> str:
    return "\n".join("".join(t.text or "" for t in p.iter(ns + "t")) for p in node.iter(ns + para))


def _docx(z: zipfile.ZipFile) -> str:
    return _runs(ET.fromstring(z.read("word/document.xml")), _W, "p")


def _pptx(z: zipfile.ZipFile) -> str:
    names = sorted(
        (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.findall(r"\d+", n)[0]),
    )
    return "\n\n".join(
        f"--- Slide {i} ---\n" + _runs(ET.fromstring(z.read(n)), _A, "p") for i, n in enumerate(names, 1)
    )


def _xlsx(z: zipfile.ZipFile) -> str:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_X + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_X + "t")))
    out: list[str] = []
    for n in sorted(x for x in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", x)):
        out.append(f"--- {n.rsplit('/', 1)[-1]} ---")
        for row in ET.fromstring(z.read(n)).iter(_X + "row"):
            cells = []
            for c in row.iter(_X + "c"):
                v = c.find(_X + "v")
                if c.get("t") == "s" and v is not None and v.text and int(v.text) < len(shared):
                    cells.append(shared[int(v.text)])
                elif c.get("t") == "inlineStr":
                    cells.append("".join(t.text or "" for t in c.iter(_X + "t")))
                else:
                    cells.append(v.text if v is not None and v.text else "")
            out.append(",".join(cells))
    return "\n".join(out)


def _pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("PDF support needs: pip install pypdf")
    reader = PdfReader(io.BytesIO(raw))
    return "\n\n".join(f"--- Page {i} ---\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, 1))


def extract_text(name: str, raw: bytes) -> str:
    """Return readable text from PDF, DOCX, PPTX, XLSX, ZIP listings, or any text/code file."""
    if name.lower().endswith(".pdf") or raw[:5] == b"%PDF-":
        return _pdf(raw)
    if zipfile.is_zipfile(io.BytesIO(raw)):
        z = zipfile.ZipFile(io.BytesIO(raw))
        if sum(i.file_size for i in z.infolist()) > 200_000_000:
            raise ValueError("Archive is too large once unpacked.")
        names = set(z.namelist())
        if "word/document.xml" in names:
            return _docx(z)
        if "ppt/presentation.xml" in names:
            return _pptx(z)
        if "xl/workbook.xml" in names:
            return _xlsx(z)
        return "Archive contents:\n" + "\n".join(f"{i.filename} ({i.file_size} bytes)" for i in z.infolist()[:500])
    if b"\x00" in raw[:8192]:
        raise ValueError("This binary file type is not supported (no text could be extracted).")
    return raw.decode("utf-8", errors="replace")


# ---------------- streaming core ----------------
async def stream_chat(model: str, payload: list[dict], options: dict, rid: str):
    global _active
    t0 = time.perf_counter()
    acq: asyncio.Future | None = None
    producer: asyncio.Task | None = None
    counted = False
    try:
        # Keep ONE acquire request in the semaphore's FIFO queue (re-creating it on each
        # heartbeat would send us to the back of the line and could starve us).
        acq = asyncio.ensure_future(gate.acquire())
        while True:
            done, _ = await asyncio.wait({acq}, timeout=S.ping_interval)
            if done:
                break
            waited = time.perf_counter() - t0
            if waited > S.queue_timeout:
                yield ndjson({"error": "Server is busy, try again shortly."})
                return
            yield ndjson({"queued": True, "waited": round(waited, 1)})
        _active += 1
        counted = True

        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        started = False

        async def produce() -> None:
            nonlocal started
            attempt = 0
            try:
                while True:
                    try:
                        stream = await client.chat(
                            model=resolve_model(model), messages=payload, stream=True,
                            keep_alive=S.keep_alive, options=options,
                        )
                        async for chunk in stream:
                            started = True
                            await queue.put(("chunk", chunk))
                        await queue.put(("end", None))
                        return
                    except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError) as exc:
                        if started or attempt >= S.retries:
                            raise
                        attempt += 1
                        delay = min(0.5 * 2 ** attempt, 8)
                        log.warning("[%s] connect failed (%s), retry %d/%d in %.1fs", rid, exc, attempt, S.retries, delay)
                        await ensure_ollama()
                        await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(("error", exc))

        producer = asyncio.create_task(produce())
        deadline = time.monotonic() + S.max_generation if S.max_generation > 0 else float("inf")
        final = None
        while True:
            if time.monotonic() > deadline:
                log.warning("[%s] generation exceeded %.0fs, aborting", rid, S.max_generation)
                yield ndjson({"error": f"Generation exceeded the {int(S.max_generation)}s limit."})
                return
            try:
                kind, data = await asyncio.wait_for(queue.get(), timeout=S.ping_interval)
            except asyncio.TimeoutError:
                yield ndjson({"ping": True})  # keeps tunnels/proxies alive while the model loads
                continue

            if kind == "error":
                log.warning("[%s] stream error: %s", rid, data)
                yield ndjson({"error": explain(data, model)})
                return
            if kind == "end":
                break

            piece = g(g(data, "message"), "content")
            if piece:
                yield ndjson({"t": piece})
            if g(data, "done"):
                final = data

        tokens = g(final, "eval_count")
        eval_ns = g(final, "eval_duration")
        yield ndjson({
            "done": True,
            "request_id": rid,
            "model": model,
            "tokens": tokens,
            "tokens_per_s": round(tokens / (eval_ns / 1e9), 1) if tokens and eval_ns else None,
            "total_ms": round((time.perf_counter() - t0) * 1000),
        })
    finally:
        # Runs on normal end, error, and client disconnect (Starlette cancels the generator).
        # Cancelling the producer closes the HTTP stream so Ollama stops generating.
        if producer and not producer.done():
            producer.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await producer
        if acq is not None:
            if acq.done() and not acq.cancelled():
                gate.release()  # also covers the "acquired just as we gave up" race
            elif not acq.done():
                acq.cancel()
        if counted:
            _active -= 1


# ---------------- routes ----------------
@app.get("/", response_class=HTMLResponse)
async def index():
    if STATIC_HTML_PATH.exists():
        return HTMLResponse(content=STATIC_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h3>index.html not found.</h3>", status_code=404)


@app.get("/api/health")
async def health():
    return {"status": "alive", "uptime_s": round(time.time() - STARTED_AT)}


@app.get("/api/status", dependencies=[Depends(guard)])
async def status():
    if time.time() - state.checked_at > 5:
        await ping()
    installed = sorted({short_name(n) for n in state.installed})
    return {
        "ok": state.ok,
        "models": sorted(set(EXTRA_MODELS) | set(installed)),
        "installed": installed,
        "default_model": S.model,
        "latency_ms": state.latency_ms,
        "checked_at": state.checked_at,
        "error": state.error or None,
        "queue_slots_free": max(0, S.max_concurrent - _active),
        "public_url": _tunnel_url,
    }


@app.get("/api/ps", dependencies=[Depends(guard)])
async def loaded_models():
    try:
        data = await client.ps()
        return {"loaded": [{"name": g(m, "model"), "vram_bytes": g(m, "size_vram")} for m in (g(data, "models", []) or [])]}
    except Exception as exc:
        return JSONResponse({"error": explain(exc, "")}, status_code=503)


@app.post("/api/models/pull", dependencies=[Depends(guard)])
async def pull_model(request: Request):
    body = await read_json(request)
    model = str(body.get("model") or "").strip()
    if not model:
        return JSONResponse({"error": "Provide a model name."}, status_code=400)
    if not MODEL_RE.match(model):
        return JSONResponse({"error": "Invalid model name."}, status_code=400)

    async def generate():
        global _pulling
        if _pulling:
            yield ndjson({"error": "Another model pull is already in progress."})
            return
        _pulling = True
        try:
            async for p in await client.pull(resolve_model(model), stream=True):
                done, total = g(p, "completed"), g(p, "total")
                yield ndjson({
                    "status": g(p, "status"),
                    "completed": done,
                    "total": total,
                    "percent": round(done / total * 100, 1) if done is not None and total else None,
                })
            await ping()
            yield ndjson({"done": True})
        except Exception as exc:
            yield ndjson({"error": explain(exc, model)})
        finally:
            _pulling = False

    return StreamingResponse(generate(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/upload", dependencies=[Depends(guard)])
async def upload(request: Request):
    """Extract text from an uploaded file. Body: {"name": "report.pdf", "data": "<base64>"}."""
    body = await read_json(request)
    name = Path(str(body.get("name") or "file")).name[:200] or "file"
    try:
        raw = base64.b64decode(str(body.get("data") or ""))
    except Exception:
        return JSONResponse({"error": "Invalid file data."}, status_code=400)
    if not raw:
        return JSONResponse({"error": "The file is empty."}, status_code=400)
    if len(raw) > S.max_upload:
        return JSONResponse({"error": f"File too large (max {S.max_upload // 1_000_000} MB)."}, status_code=413)
    try:
        text = (await asyncio.to_thread(extract_text, name, raw)).strip()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=415)
    except Exception as exc:
        log.warning("upload %s failed: %s", name, exc)
        return JSONResponse({"error": f"Could not read {name} ({type(exc).__name__})."}, status_code=422)
    if not text:
        return JSONResponse({"error": "No readable text found in this file."}, status_code=422)
    return {"name": name, "text": text[: S.max_text], "truncated": len(text) > S.max_text, "chars": len(text)}


@app.post("/api/chat", dependencies=[Depends(guard)])
async def chat(request: Request):
    body = await read_json(request)

    model = str(body.get("model") or S.model).strip()
    if not MODEL_RE.match(model):
        return JSONResponse({"error": "Invalid model name."}, status_code=400)
    mode = "code" if body.get("mode") == "code" else "chat"
    # sanitise: this string is placed inside the system prompt
    language = " ".join(re.sub(r"[^\w\s+#.\-/,()]", "", str(body.get("language") or "auto")).split())[:80] or "auto"

    if mode == "code":
        lang = AUTO_LANGUAGE if language.lower() == "auto" else language
        system, default_temp = CODE_PROMPT.format(language=lang), 0.2
    else:
        system, default_temp = CHAT_PROMPT, 0.7

    # custom instructions from the UI arrive as "system" messages
    raw_msgs = body.get("messages")
    extra = "\n".join(
        str(m.get("content", ""))[:4000] for m in (raw_msgs if isinstance(raw_msgs, list) else [])
        if isinstance(m, dict) and m.get("role") == "system"
    ).strip()
    if extra:
        system += "\n\nThe user's custom instructions:\n" + extra

    num_ctx = int(bounded(body.get("num_ctx"), 1024, S.max_ctx, S.num_ctx))
    raw_predict = body.get("max_tokens")
    # -1 = generate until the model stops (no output cap)
    predict = S.default_predict if raw_predict is None else int(bounded(raw_predict, -1, 10_000_000, S.default_predict))
    options = {
        "temperature": bounded(body.get("temperature"), 0.0, 1.5, default_temp),
        "num_ctx": num_ctx,
        "num_predict": predict,
    }

    # budget the history so prompt + reply fit in the context window (otherwise Ollama
    # silently truncates, possibly dropping the system prompt)
    if S.trim_history:
        input_tokens = max(num_ctx - max(predict, 0), num_ctx // 4)
        max_chars = max(1000, int(input_tokens * S.chars_per_token) - len(system))
    else:
        max_chars = 0  # unlimited input
    messages = clean_messages(raw_msgs, max_chars)
    if not messages or messages[-1]["role"] != "user":
        return JSONResponse({"error": "Send at least one user message."}, status_code=400)

    rid = uuid.uuid4().hex[:8]
    n_img = sum(len(m.get("images", [])) for m in messages)
    log.info("[%s] %s mode=%s msgs=%d images=%d ctx=%d", rid, model, mode, len(messages), n_img, num_ctx)
    payload = [{"role": "system", "content": system}] + messages

    return StreamingResponse(
        stream_chat(model, payload, options, rid),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": rid},
    )


if __name__ == "__main__":
    import uvicorn

    target = f"{Path(__file__).stem}:app" if S.reload else app
    uvicorn.run(target, host=S.bind, port=S.port, reload=S.reload, timeout_keep_alive=75)