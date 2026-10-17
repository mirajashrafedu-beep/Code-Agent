"""Qdrant vector store layer.

- Settings come from the environment and are validated once.
- One shared async client.
- Schema setup raises on failure, is safe when API and workers start at
  the same time, and refuses to run against a collection whose vector size
  does not match the embedding model.
- Payload indexes on the fields used to filter by user.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache, partial

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

from ._env import env_bool, env_int
from .errors import DatabaseConfigError

logger = logging.getLogger(__name__)

_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")


class VectorConfigError(DatabaseConfigError):
    """Raised when Qdrant settings are invalid or the collection does not match them."""


_env_int = partial(env_int, error=VectorConfigError)


@dataclass(frozen=True)
class QdrantSettings:
    host: str
    port: int
    api_key: str | None
    https: bool
    timeout_s: int
    collection: str
    vector_size: int
    indexed_fields: tuple[str, ...]


@lru_cache(maxsize=1)
def get_settings() -> QdrantSettings:
    """Read and validate settings. Call get_settings.cache_clear() in tests."""
    collection = os.getenv("QDRANT_COLLECTION_NAME", "user_knowledge_base")
    if not _COLLECTION_NAME.match(collection):
        raise VectorConfigError(f"QDRANT_COLLECTION_NAME is not valid: {collection!r}")
    fields = tuple(
        f.strip() for f in os.getenv("QDRANT_PAYLOAD_INDEXES", "user_id,document_id").split(",") if f.strip()
    )
    for field in fields:
        if not _FIELD_NAME.match(field):
            raise VectorConfigError(f"QDRANT_PAYLOAD_INDEXES has an invalid field name: {field!r}")
    return QdrantSettings(
        host=os.getenv("QDRANT_HOST", "qdrant"),
        port=_env_int("QDRANT_PORT", 6333),
        api_key=os.getenv("QDRANT_API_KEY") or None,
        https=env_bool("QDRANT_HTTPS", False),
        timeout_s=_env_int("QDRANT_TIMEOUT_S", 30),
        collection=collection,
        vector_size=_env_int("VECTOR_SIZE", 1536),
        indexed_fields=fields,
    )


# ---- client management -----------------------------------------------------

_client: AsyncQdrantClient | None = None


def get_qdrant() -> AsyncQdrantClient:
    """Return the shared client, creating it on first use."""
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncQdrantClient(
            host=s.host,
            port=s.port,
            api_key=s.api_key,
            https=s.https,
            timeout=s.timeout_s,
        )
    return _client


async def ping_qdrant() -> None:
    """Run a real round trip. Raises if Qdrant is unreachable."""
    await get_qdrant().get_collections()


async def close_qdrant() -> None:
    """Close the client. Safe to call more than once."""
    global _client
    client, _client = _client, None
    if client is not None:
        await client.close()


# ---- schema ----------------------------------------------------------------

async def init_qdrant_schema() -> None:
    """Create the collection and payload indexes if missing, then verify them.

    Raises on any failure so startup can retry or degrade. A 409 from a
    concurrent creator (API and worker starting together) counts as success.
    """
    s = get_settings()
    client = get_qdrant()

    if not await client.collection_exists(s.collection):
        try:
            await client.create_collection(
                collection_name=s.collection,
                vectors_config=VectorParams(size=s.vector_size, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection %s", s.collection)
        except UnexpectedResponse as exc:
            if exc.status_code != 409:
                raise

    info = await client.get_collection(s.collection)
    vectors = info.config.params.vectors
    if isinstance(vectors, VectorParams) and vectors.size != s.vector_size:
        raise VectorConfigError(
            f"Collection {s.collection!r} has vector size {vectors.size}, "
            f"but VECTOR_SIZE is {s.vector_size}. Re-embed into a new collection "
            f"or fix VECTOR_SIZE."
        )

    # Without these indexes, filtering by user scans every vector.
    for field in s.indexed_fields:
        await client.create_payload_index(
            collection_name=s.collection,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    logger.info("Qdrant ready: collection %s, indexes %s", s.collection, list(s.indexed_fields))


def __getattr__(name: str):
    """Keep old module-level constants working, read lazily."""
    if name == "QDRANT_COLLECTION":
        return get_settings().collection
    if name == "VECTOR_SIZE":
        return get_settings().vector_size
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")