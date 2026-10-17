"""MongoDB layer (Motor + Beanie) for agent traces, tool logs, and prompts.

- Settings come from the environment and are validated once.
- One shared Motor client, created lazily inside the running event loop.
- Beanie document models are registered once, then initialized at startup.
- Setup raises on failure so database/__init__.py can retry and report.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Sequence

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from .errors import DatabaseConfigError

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

_DB_NAME = re.compile(r"^[A-Za-z0-9_-]{1,63}$")


class MongoConfigError(DatabaseConfigError):
    """Raised when MongoDB settings or document models are missing."""


@dataclass(frozen=True)
class MongoSettings:
    uri: str
    db_name: str
    max_pool_size: int
    server_selection_timeout_ms: int
    connect_timeout_ms: int


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise MongoConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise MongoConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


@lru_cache(maxsize=1)
def get_settings() -> MongoSettings:
    """Read and validate settings. Call get_settings.cache_clear() in tests."""
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise MongoConfigError("MONGO_URI is not set")
    db_name = os.getenv("MONGO_DB", "llm_traces_db")
    if not _DB_NAME.match(db_name):
        raise MongoConfigError(f"MONGO_DB is not a valid database name: {db_name!r}")
    return MongoSettings(
        uri=uri,
        db_name=db_name,
        max_pool_size=_env_int("MONGO_MAX_POOL_SIZE", 50),
        server_selection_timeout_ms=_env_int("MONGO_SERVER_SELECTION_TIMEOUT_MS", 5000),
        connect_timeout_ms=_env_int("MONGO_CONNECT_TIMEOUT_MS", 5000),
    )


# ---- client management -----------------------------------------------------

_client: AsyncIOMotorClient | None = None


def get_mongo_client() -> AsyncIOMotorClient:
    """Return the shared client, creating it on first use.

    Call from inside the running event loop. Motor clients are bound to
    one loop and are not thread-safe.
    """
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncIOMotorClient(
            s.uri,
            maxPoolSize=s.max_pool_size,
            serverSelectionTimeoutMS=s.server_selection_timeout_ms,
            connectTimeoutMS=s.connect_timeout_ms,
            uuidRepresentation="standard",  # required to store UUID fields
            tz_aware=True,
            appname="llm-agent-platform",
        )
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    return get_mongo_client()[get_settings().db_name]


def close_mongo_client() -> None:
    """Close the shared client. Safe to call more than once."""
    global _client
    client, _client = _client, None
    if client is not None:
        client.close()


async def ping_mongo() -> None:
    """Run a real round trip. Raises if MongoDB is unreachable."""
    await get_mongo_db().command("ping")


# ---- beanie ----------------------------------------------------------------

_document_models: list[type] = []


def register_document_models(*models: type) -> None:
    """Register Beanie documents. Call at import time of models/nosql.

    Example:
        register_document_models(AgentTrace, ToolLog, PromptTemplate)
    """
    for model in models:
        if model not in _document_models:
            _document_models.append(model)


async def init_mongo_beanie(document_models: Sequence[type] | None = None) -> None:
    """Verify the connection, then initialize Beanie.

    Uses the given models, or the registered ones when omitted.
    Safe to call again. Raises if no models exist or MongoDB is down.
    """
    models = list(document_models) if document_models is not None else list(_document_models)
    if not models:
        raise MongoConfigError(
            "No Beanie document models. Pass them in or call register_document_models() first."
        )
    await ping_mongo()  # clear error before Beanie's own failure modes
    await init_beanie(database=get_mongo_db(), document_models=models)
    logger.info("Beanie initialized for database %s with %d models",
                get_settings().db_name, len(models))