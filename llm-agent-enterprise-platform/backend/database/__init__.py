"""Multi-database layer.

One place to connect, health-check, and close every engine.
Existing imports (get_db, get_redis, ...) keep working unchanged.

Usage in main.py:

    from database import database_lifespan
    app = FastAPI(lifespan=lambda app: database_lifespan())
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Callable, Iterable

from .clickhouse import (
    close_clickhouse_client,
    get_clickhouse_client,
    init_clickhouse_tables,
    ping_clickhouse,
)
from .errors import DatabaseConfigError
from .mongodb import (
    close_mongo_client,
    get_mongo_db,
    init_mongo_beanie,
    ping_mongo,
    register_document_models,
)
from .postgres import (
    Base,
    async_session_maker,
    dispose_engine,
    get_db,
    get_engine,
    init_postgres,
    ping_postgres,
    session_scope,
)
from .redis import close_redis, get_redis, init_redis, ping_redis, subscribe
from .s3_minio import S3MinioClient, close_s3, get_s3_client, init_s3, ping_s3
from .vector import close_qdrant, get_qdrant, init_qdrant_schema, ping_qdrant

logger = logging.getLogger(__name__)

STARTUP_ATTEMPTS = 5          # containers start in parallel, so retry
STARTUP_BASE_DELAY_S = 0.5    # doubles after each failed attempt
CALL_TIMEOUT_S = 10.0         # limit for one connect attempt
HEALTH_TIMEOUT_S = 3.0        # limit for one health ping


class DatabaseStartupError(RuntimeError):
    """Raised when a required database cannot be reached at startup."""


@dataclass(frozen=True)
class DatabaseEngine:
    """Describes how to connect to, ping, and close one engine."""

    name: str
    required: bool                      # False = app runs in degraded mode
    connect: Callable[[], Any]          # init schema / verify connection
    ping: Callable[[], Any]             # cheap liveness check
    close: Callable[[], Any] | None = None


@dataclass
class EngineStatus:
    name: str
    required: bool
    ok: bool
    latency_ms: float | None = None
    error: str | None = None


async def _call(fn: Callable[[], Any]) -> Any:
    """Run a sync or async callable without blocking the event loop."""
    if inspect.iscoroutinefunction(fn):
        return await fn()
    result = await asyncio.to_thread(fn)
    if inspect.isawaitable(result):
        result = await result
    return result


# ---- ClickHouse is synchronous, so its wrappers run in a thread ------------

async def _ping_clickhouse() -> None:
    await _call(ping_clickhouse)


async def _close_clickhouse() -> None:
    await _call(close_clickhouse_client)


async def _close_mongo() -> None:
    close_mongo_client()


# ---- registry: the single list of engines ----------------------------------

ENGINES: tuple[DatabaseEngine, ...] = (
    DatabaseEngine("postgres", True, init_postgres, ping_postgres, dispose_engine),
    DatabaseEngine("redis", True, init_redis, ping_redis, close_redis),
    DatabaseEngine("qdrant", True, init_qdrant_schema, ping_qdrant, close_qdrant),
    DatabaseEngine("minio", True, init_s3, ping_s3, close_s3),
    DatabaseEngine("mongodb", False, init_mongo_beanie, ping_mongo, _close_mongo),
    DatabaseEngine("clickhouse", False, init_clickhouse_tables, _ping_clickhouse, _close_clickhouse),
)


def _select(only: Iterable[str] | None) -> tuple[DatabaseEngine, ...]:
    if only is None:
        return ENGINES
    wanted = set(only)
    unknown = wanted - {e.name for e in ENGINES}
    if unknown:
        raise ValueError(f"Unknown database engine(s): {sorted(unknown)}")
    return tuple(e for e in ENGINES if e.name in wanted)


# ---- lifecycle -------------------------------------------------------------

async def _connect_with_retry(eng: DatabaseEngine) -> EngineStatus:
    start = time.perf_counter()
    last_error = ""
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(_call(eng.connect), timeout=CALL_TIMEOUT_S)
            latency = (time.perf_counter() - start) * 1000
            logger.info("%s connected in %.0f ms", eng.name, latency)
            return EngineStatus(eng.name, eng.required, True, round(latency, 1))
        except DatabaseConfigError as exc:  # retrying cannot fix bad config
            logger.error("%s misconfigured: %s", eng.name, exc)
            return EngineStatus(eng.name, eng.required, False, error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - reported in the status
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s attempt %d/%d failed: %s",
                           eng.name, attempt, STARTUP_ATTEMPTS, last_error)
            if attempt < STARTUP_ATTEMPTS:
                await asyncio.sleep(STARTUP_BASE_DELAY_S * 2 ** (attempt - 1))
    return EngineStatus(eng.name, eng.required, False, error=last_error)


async def init_databases(only: Iterable[str] | None = None) -> list[EngineStatus]:
    """Connect every engine concurrently.

    Raises DatabaseStartupError if a required engine fails.
    Optional engines that fail are logged and reported as not ok.
    """
    statuses = await asyncio.gather(*(_connect_with_retry(e) for e in _select(only)))
    failed_required = [s for s in statuses if s.required and not s.ok]
    if failed_required:
        details = "; ".join(f"{s.name} ({s.error})" for s in failed_required)
        raise DatabaseStartupError(f"Required databases unavailable: {details}")
    for s in statuses:
        if not s.ok:
            logger.error("optional database %s is down, running degraded", s.name)
    return list(statuses)


async def close_databases(only: Iterable[str] | None = None) -> None:
    """Close every engine concurrently. Never raises; failures are logged."""
    engines = [e for e in _select(only) if e.close is not None]
    results = await asyncio.gather(
        *(asyncio.wait_for(_call(e.close), timeout=CALL_TIMEOUT_S) for e in engines),
        return_exceptions=True,
    )
    for eng, result in zip(engines, results):
        if isinstance(result, Exception):
            logger.error("closing %s failed: %s", eng.name, result)


async def _check_one(eng: DatabaseEngine) -> EngineStatus:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(_call(eng.ping), timeout=HEALTH_TIMEOUT_S)
        return EngineStatus(eng.name, eng.required, True,
                            round((time.perf_counter() - start) * 1000, 1))
    except Exception as exc:  # noqa: BLE001
        return EngineStatus(eng.name, eng.required, False,
                            error=f"{type(exc).__name__}: {exc}")


async def check_health() -> dict[str, Any]:
    """Ping every engine. Overall status is ok, degraded, or down."""
    statuses = await asyncio.gather(*(_check_one(e) for e in ENGINES))
    if any(s.required and not s.ok for s in statuses):
        overall = "down"
    elif any(not s.ok for s in statuses):
        overall = "degraded"
    else:
        overall = "ok"
    return {"status": overall, "engines": {s.name: asdict(s) for s in statuses}}


@asynccontextmanager
async def database_lifespan() -> AsyncIterator[None]:
    """Connect on startup and close on shutdown, even if startup fails."""
    try:
        await init_databases()
        yield
    finally:
        await close_databases()


__all__ = [
    # postgres
    "get_db", "async_session_maker", "session_scope", "Base",
    "get_engine", "init_postgres", "ping_postgres", "dispose_engine",
    # vector
    "get_qdrant", "init_qdrant_schema", "ping_qdrant", "close_qdrant",
    # redis
    "get_redis", "init_redis", "ping_redis", "close_redis", "subscribe",
    # mongodb
    "get_mongo_db", "init_mongo_beanie", "close_mongo_client", "ping_mongo",
    "register_document_models",
    # object store
    "get_s3_client", "S3MinioClient", "init_s3", "ping_s3", "close_s3",
    # time series
    "get_clickhouse_client", "init_clickhouse_tables",
    "close_clickhouse_client", "ping_clickhouse",
    # lifecycle
    "ENGINES", "DatabaseEngine", "EngineStatus", "DatabaseStartupError", "DatabaseConfigError",
    "init_databases", "close_databases", "check_health", "database_lifespan",
]