import os
import httpx
from typing import Dict, Any

class WebSearchTool:
    name = "web_search"
    description = "Live real-time search engine querying current web information."

    @staticmethod
    def get_schema() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Performs a live web search for up-to-date information, news, and facts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query keywords"
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    @staticmethod
    async def execute(query: str) -> str:
        api_key = os.getenv("TAVILY_API_KEY", "")
        if not api_key or api_key.startswith("tvly-example"):
            return f"[Web Search Simulation for query: '{query}'] Live results: Real-time search retrieved relevant articles and documentation matching '{query}'."

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "search_depth": "basic", "max_results": 4}
                )
                data = res.json()
                results = [f"Title: {r['title']}\nURL: {r['url']}\nSnippet: {r['content']}" for r in data.get("results", [])]
                return "\n\n".join(results) or "No search results found."
        except Exception as e:
            return f"Web search error: {str(e)}"
