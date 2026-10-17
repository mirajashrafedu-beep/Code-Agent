from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class VectorSearchQuery(BaseModel):
    query: str
    top_k: int = 5
    score_threshold: Optional[float] = 0.65

class DocumentChunkResponse(BaseModel):
    id: str
    score: float
    text: str
    filename: str
    file_key: str
    chunk_index: int
    payload: Dict[str, Any] = {}
