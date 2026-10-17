import os
from typing import Dict, Any

class AgentRouter:
    @staticmethod
    def classify_intent(prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()
        
        # Check math intent
        if any(w in prompt_lower for w in ["calculate", "solve", "integral", "derivative", "equation", "formula", "algebra"]):
            return {"primary_tool": "math_solver", "strategy": "deterministic_math"}

        # Check code/sandbox intent
        if any(w in prompt_lower for w in ["python", "run code", "execute script", "plot", "dataframe", "pandas"]):
            return {"primary_tool": "sandbox_runner", "strategy": "sandbox_execution"}

        # Check search/live info intent
        if any(w in prompt_lower for w in ["latest news", "current price", "weather in", "today", "who won", "what is happening"]):
            return {"primary_tool": "web_search", "strategy": "web_search"}

        # Check document/RAG intent
        if any(w in prompt_lower for w in ["in my uploaded document", "according to the pdf", "in my notes", "document", "file summary"]):
            return {"primary_tool": "rag_retriever", "strategy": "rag_grounded"}

        return {"primary_tool": None, "strategy": "general_reasoning"}
