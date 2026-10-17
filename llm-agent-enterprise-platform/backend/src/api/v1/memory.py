from typing import List
from fastapi import APIRouter, Depends
from src.api.deps import get_current_user
from src.schemas.memory import VectorSearchQuery, DocumentChunkResponse
from src.tools.rag_retriever import RAGRetrieverTool
from src.models.relational.user import User

router = APIRouter()

@router.post("/search", response_model=List[DocumentChunkResponse])
async def search_memory(
    query_in: VectorSearchQuery,
    current_user: User = Depends(get_current_user)
):
    results = await RAGRetrieverTool.execute(
        query=query_in.query,
        user_id=str(current_user.id),
        top_k=query_in.top_k
    )
    formatted = []
    for r in results:
        if "error" in r:
            continue
        formatted.append(
            DocumentChunkResponse(
                id=str(r.get("chunk_index", 0)),
                score=float(r.get("score", 0.0)),
                text=r.get("text", ""),
                filename=r.get("filename", "unknown"),
                file_key="",
                chunk_index=int(r.get("chunk_index", 0)),
                payload=r
            )
        )
    return formatted
