import os
import json
import httpx
from typing import AsyncGenerator, List, Dict, Any
from openai import AsyncOpenAI
from .base import BaseLLMGateway

class LocalLLMClient(BaseLLMGateway):
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8000/api")
        self.client = AsyncOpenAI(base_url=self.base_url, api_key="dummy-local-key")

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: str = "qwen2.5-coder:7b",
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> Dict[str, Any]:
        accumulated = ""
        async for chunk in self.generate_stream(messages, model, temperature, tools):
            if chunk.get("type") == "content":
                accumulated += chunk.get("content", "")
        return {
            "content": accumulated,
            "tool_calls": None,
            "prompt_tokens": len(str(messages)) // 4,
            "completion_tokens": len(accumulated) // 4,
        }

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        model: str = "qwen2.5-coder:7b",
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # Try direct HTTP request to app.py /api/chat
        endpoint = self.base_url.rstrip("/")
        if not endpoint.endswith("/chat") and not endpoint.endswith("/v1"):
            endpoint = f"{endpoint}/chat"

        try:
            async with httpx.AsyncClient(timeout=120.0) as http_client:
                async with http_client.stream(
                    "POST",
                    endpoint,
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature
                    }
                ) as response:
                    if response.status_code == 200:
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                if "t" in data and data["t"]:
                                    yield {"type": "content", "content": data["t"]}
                                elif "message" in data and isinstance(data["message"], dict):
                                    content = data["message"].get("content", "")
                                    if content:
                                        yield {"type": "content", "content": content}
                            except Exception:
                                pass
                        return
        except Exception:
            pass

        # Fallback to OpenAI-compatible endpoint
        try:
            stream = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                stream=True
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield {"type": "content", "content": delta.content}
        except Exception as e:
            yield {"type": "content", "content": f"Connection notice: {str(e)}"}

