"""Redis layer (redis-py asyncio) for cache, short-term memory, and pub/sub.

- Settings come from the environment and are validated once.
- One shared client with a connection pool, timeouts, and retries.
- Responses are decoded to str. Store bytes elsewhere (MinIO), not here.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from ._env import env_int
from .errors import DatabaseConfigError

logger = logging.getLogger(__name__)

_SCHEMES = {"redis", "rediss", "unix"}


class RedisConfigError(DatabaseConfigError):
    """Raised when Redis settings are invalid."""


_env_int = partial(env_int, error=RedisConfigError)


@dataclass(frozen=True)
class RedisSettings:
    uri: str
    max_connections: int
    socket_timeout_s: int
    connect_timeout_s: int
    health_check_interval_s: int
    retries: int


@lru_cache(maxsize=1)
def get_settings() -> RedisSettings:
    """Read and validate settings. Call get_settings.cache_clear() in tests."""
    uri = os.getenv("REDIS_URI", "redis://redis:6379/0")
    scheme = urlsplit(uri).scheme
    if scheme not in _SCHEMES:
        raise RedisConfigError(f"REDIS_URI must start with redis://, rediss:// or unix://, got {scheme!r}")
    return RedisSettings(
        uri=uri,
        max_connections=_env_int("REDIS_MAX_CONNECTIONS", 50),
        # The health check interval must stay below the socket timeout, or an
        # idle pub/sub connection times out before it gets its keepalive ping.
        socket_timeout_s=_env_int("REDIS_SOCKET_TIMEOUT_S", 30),
        connect_timeout_s=_env_int("REDIS_CONNECT_TIMEOUT_S", 5),
        health_check_interval_s=_env_int("REDIS_HEALTH_CHECK_INTERVAL_S", 15),
        retries=_env_int("REDIS_RETRIES", 3, minimum=0),
    )


def _safe_uri(uri: str) -> str:
    """Return the URI without credentials, for logs."""
    parts = urlsplit(uri)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ---- client management -----------------------------------------------------

_client: Redis | None = None


def get_redis() -> Redis:
    """Return the shared client, creating it on first use.

    Call from inside the running event loop.
    """
    global _client
    if _client is None:
        s = get_settings()
        _client = Redis.from_url(
            s.uri,
            encoding="utf-8",
            decode_responses=True,
            max_connections=s.max_connections,
            socket_timeout=s.socket_timeout_s,
            socket_connect_timeout=s.connect_timeout_s,
            health_check_interval=s.health_check_interval_s,
            retry=Retry(ExponentialBackoff(cap=2.0, base=0.1), s.retries),
        )
    return _client


async def ping_redis() -> None:
    """Run a real round trip. Raises if Redis is unreachable."""
    await get_redis().ping()


async def init_redis() -> None:
    """Verify the connection."""
    await ping_redis()
    logger.info("Redis ready at %s", _safe_uri(get_settings().uri))


async def close_redis() -> None:
    """Close the client and its pool. Safe to call more than once."""
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


# ---- pub/sub ---------------------------------------------------------------

@asynccontextmanager
async def subscribe(*channels: str) -> AsyncIterator:
    """Subscribe to channels and always unsubscribe on exit.

    Example:
        async with subscribe("cancel:123") as pubsub:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    """
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(*channels)
    try:
        yield pubsub
    finally:
        await pubsub.unsubscribe(*channels)
        await pubsub.aclose()