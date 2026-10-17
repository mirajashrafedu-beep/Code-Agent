import io
import os
import uuid
from celery import shared_task
from minio import Minio
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
import openai

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER", "minio_admin")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD", "minio_secure_password")
MINIO_BUCKET_UPLOADS = os.getenv("MINIO_BUCKET_UPLOADS", "user-uploads")

QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION_NAME", "user_knowledge_base")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks

@shared_task(name="tasks.index_file_content")
def index_file_content(user_id: str, file_key: str, filename: str):
    try:
        # 1. Download file from MinIO
        minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ROOT_USER,
            secret_key=MINIO_ROOT_PASSWORD,
            secure=False
        )
        response = minio_client.get_object(MINIO_BUCKET_UPLOADS, file_key)
        content = response.read().decode("utf-8", errors="ignore")
        response.close()
        response.release_conn()

        if not content.strip():
            return {"status": "skipped", "reason": "empty file"}

        # 2. Chunk text
        chunks = chunk_text(content)
        if not chunks:
            return {"status": "skipped", "reason": "no chunks produced"}

        # 3. Embed chunks with OpenAI
        client = openai.OpenAI(api_key=OPENAI_API_KEY or "sk-dummy")
        points = []

        for idx, chunk in enumerate(chunks):
            # Compute embedding or fallback dummy vector
            try:
                emb_res = client.embeddings.create(
                    input=chunk,
                    model="text-embedding-3-small"
                )
                vector = emb_res.data[0].embedding
            except Exception:
                vector = [0.0] * 1536

            point_id = str(uuid.uuid4())
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "user_id": user_id,
                        "file_key": file_key,
                        "filename": filename,
                        "chunk_index": idx,
                        "text": chunk
                    }
                )
            )

        # 4. Upsert into Qdrant
        qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        collections = [c.name for c in qdrant.get_collections().collections]
        if QDRANT_COLLECTION not in collections:
            qdrant.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE)
            )

        qdrant.upsert(
            collection_name=QDRANT_COLLECTION,
            points=points
        )

        return {"status": "success", "indexed_chunks": len(points), "filename": filename}

    except Exception as e:
        return {"status": "error", "error": str(e)}
