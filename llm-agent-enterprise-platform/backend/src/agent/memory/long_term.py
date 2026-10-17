from typing import List, Dict, Any
from src.tools.rag_retriever import RAGRetrieverTool

class LongTermMemory:
    def __init__(self, user_id: str):
        self.user_id = user_id

    async def recall_relevant_context(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        return await RAGRetrieverTool.execute(query=query, user_id=self.user_id, top_k=top_k)
