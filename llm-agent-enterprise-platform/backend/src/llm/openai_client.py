import os
from typing import AsyncGenerator, List, Dict, Any
import openai
from openai import AsyncOpenAI
from .base import BaseLLMGateway

class OpenAIClient(BaseLLMGateway):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> Dict[str, Any]:
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        return {
            "content": choice.message.content or "",
            "tool_calls": [tc.model_dump() for tc in choice.message.tool_calls] if choice.message.tool_calls else None,
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
        }

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            if delta.content:
                yield {"type": "content", "content": delta.content}
            if delta.tool_calls:
                yield {"type": "tool_calls", "tool_calls": [tc.model_dump() for tc in delta.tool_calls]}
