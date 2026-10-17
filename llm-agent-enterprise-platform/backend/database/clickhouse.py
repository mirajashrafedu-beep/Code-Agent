"""ClickHouse telemetry layer.

- Settings come from the environment and are validated once.
- One shared client per process (thread-safe, created lazily).
- Schema setup is idempotent and raises on failure, so the lifecycle
  layer in database/__init__.py can retry and report the engine as down.
- Batch insert helpers are the write path for the telemetry worker.

All functions here are synchronous. Call them with asyncio.to_thread
from async code.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Sequence

import clickhouse_connect

if TYPE_CHECKING:
    from clickhouse_connect.driver.client import Client

logger = logging.getLogger(__name__)

TOKEN_TABLE = "token_consumption"
LATENCY_TABLE = "agent_latency"

# Columns written by the insert helpers. Omitted columns use table defaults.
TOKEN_COLUMNS = (
    "user_id", "conversation_id", "model_name", "provider",
    "prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd",
)
LATENCY_COLUMNS = (
    "trace_id", "user_id", "conversation_id", "model_name",
    "time_to_first_token_ms", "total_execution_time_ms",
    "tool_execution_time_ms", "tool_call_count", "status",
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class ClickHouseConfigError(RuntimeError):
    """Raised when required ClickHouse settings are missing or invalid."""


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str
    port: int
    username: str
    password: str
    database: str
    retention_days: int
    connect_timeout_s: int
    query_timeout_s: int


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ClickHouseConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ClickHouseConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


@lru_cache(maxsize=1)
def get_settings() -> ClickHouseSettings:
    """Read and validate settings. Call get_settings.cache_clear() in tests."""
    password = os.getenv("CLICKHOUSE_PASSWORD")
    if not password:
        raise ClickHouseConfigError("CLICKHOUSE_PASSWORD is not set")
    database = os.getenv("CLICKHOUSE_DB", "telemetry_db")
    if not _IDENTIFIER.match(database):
        raise ClickHouseConfigError(f"CLICKHOUSE_DB is not a valid identifier: {database!r}")
    return ClickHouseSettings(
        host=os.getenv("CLICKHOUSE_HOST", "clickhouse"),
        port=_env_int("CLICKHOUSE_PORT", 8123),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=password,
        database=database,
        retention_days=_env_int("CLICKHOUSE_RETENTION_DAYS", 395),
        connect_timeout_s=_env_int("CLICKHOUSE_CONNECT_TIMEOUT_S", 10),
        query_timeout_s=_env_int("CLICKHOUSE_QUERY_TIMEOUT_S", 30),
    )


# ---- client management -----------------------------------------------------

def _connect(settings: ClickHouseSettings, database: str | None) -> Client:
    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "username": settings.username,
        "password": settings.password,
        "connect_timeout": settings.connect_timeout_s,
        "send_receive_timeout": settings.query_timeout_s,
        # Without this, a shared client rejects concurrent queries from threads.
        "autogenerate_session_id": False,
    }
    if database:
        kwargs["database"] = database
    return clickhouse_connect.get_client(**kwargs)


_client: Client | None = None
_client_lock = threading.Lock()


def get_clickhouse_client() -> Client:
    """Return the shared client, creating it on first use.

    Raises on connection or configuration errors. Callers decide how to
    degrade; telemetry code should catch, log, and move on.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                settings = get_settings()
                _client = _connect(settings, settings.database)
    return _client


def close_clickhouse_client() -> None:
    """Close the shared client. Safe to call more than once."""
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        client.close()


def ping_clickhouse() -> None:
    """Run a real query. Raises if ClickHouse is unreachable.

    client.ping() is not used because it returns False instead of raising.
    """
    get_clickhouse_client().command("SELECT 1")


# ---- schema ----------------------------------------------------------------

def _schema_statements(settings: ClickHouseSettings) -> list[str]:
    db, days = settings.database, settings.retention_days
    return [
        f"CREATE DATABASE IF NOT EXISTS {db}",
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{TOKEN_TABLE} (
            event_id UUID DEFAULT generateUUIDv4(),
            user_id UUID,
            conversation_id UUID,
            model_name LowCardinality(String),
            provider LowCardinality(String),
            prompt_tokens UInt32,
            completion_tokens UInt32,
            total_tokens UInt32,
            estimated_cost_usd Float64,
            created_at DateTime('UTC') DEFAULT now()
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(created_at)
        ORDER BY (user_id, created_at)
        TTL created_at + INTERVAL {days} DAY
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{LATENCY_TABLE} (
            trace_id UUID DEFAULT generateUUIDv4(),
            user_id UUID,
            conversation_id UUID,
            model_name LowCardinality(String),
            time_to_first_token_ms Float64,
            total_execution_time_ms Float64,
            tool_execution_time_ms Float64,
            tool_call_count UInt16,
            status LowCardinality(String),
            created_at DateTime('UTC') DEFAULT now()
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(created_at)
        ORDER BY (user_id, created_at)
        TTL created_at + INTERVAL {days} DAY
        """,
    ]


def init_clickhouse_tables() -> None:
    """Create the database and tables if missing, then verify the connection.

    Idempotent. Raises on any failure so startup can retry or degrade.
    Existing tables are never altered; change them with a migration.
    """
    settings = get_settings()
    # The telemetry database may not exist yet, so bootstrap without it.
    bootstrap = _connect(settings, database=None)
    try:
        for statement in _schema_statements(settings):
            bootstrap.command(statement)
    finally:
        bootstrap.close()
    ping_clickhouse()  # confirms the shared client can reach the new database
    logger.info("ClickHouse telemetry schema ready on database %s", settings.database)


# ---- write path ------------------------------------------------------------

def _insert(table: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> int:
    if not rows:
        return 0
    settings = get_settings()
    get_clickhouse_client().insert(
        f"{settings.database}.{table}",
        list(rows),
        column_names=list(columns),
        # Server-side batching for many small writes; wait so rows are not lost.
        settings={"async_insert": 1, "wait_for_async_insert": 1},
    )
    return len(rows)


def insert_token_usage(rows: Sequence[Sequence[Any]]) -> int:
    """Insert rows ordered as TOKEN_COLUMNS. Returns the number inserted."""
    return _insert(TOKEN_TABLE, TOKEN_COLUMNS, rows)


def insert_agent_latency(rows: Sequence[Sequence[Any]]) -> int:
    """Insert rows ordered as LATENCY_COLUMNS. Returns the number inserted.

    Use the same trace_id as the MongoDB agent trace to join the two stores.
    """
    return _insert(LATENCY_TABLE, LATENCY_COLUMNS, rows)