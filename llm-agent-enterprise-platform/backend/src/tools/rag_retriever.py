import os
from typing import Dict, Any, List
import openai
from qdrant_client import AsyncQdrantClient
from database.vector import get_qdrant, QDRANT_COLLECTION

class RAGRetrieverTool:
    name = "rag_retriever"
    description = "Searches the Qdrant vector database for uploaded documents and user knowledge base."

    @staticmethod
    def get_schema() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "rag_retriever",
                "description": "Performs semantic vector search across the user's uploaded documents and company knowledge base.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to match semantically in the vector store"
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of relevant chunks to retrieve",
                            "default": 4
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    @staticmethod
    async def execute(query: str, user_id: str, top_k: int = 4) -> List[Dict[str, Any]]:
        client = get_qdrant()
        openai_key = os.getenv("OPENAI_API_KEY", "")

        # 1. Embed query
        try:
            ai_client = openai.AsyncOpenAI(api_key=openai_key or "sk-dummy")
            emb = await ai_client.embeddings.create(input=query, model="text-embedding-3-small")
            query_vector = emb.data[0].embedding
        except Exception:
            query_vector = [0.0] * 1536

        # 2. Search Qdrant
        try:
            results = await client.search(
                collection_name=QDRANT_COLLECTION,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True
            )
            hits = []
            for r in results:
                hits.append({
                    "score": r.score,
                    "text": r.payload.get("text", "") if r.payload else "",
                    "filename": r.payload.get("filename", "unknown") if r.payload else "",
                    "chunk_index": r.payload.get("chunk_index", 0) if r.payload else 0,
                })
            return hits
        except Exception as e:
            return [{"error": f"Vector retrieval error: {str(e)}"}]
