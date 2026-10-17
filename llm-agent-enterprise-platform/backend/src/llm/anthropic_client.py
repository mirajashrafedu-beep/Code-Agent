import os
from typing import AsyncGenerator, List, Dict, Any
from anthropic import AsyncAnthropic
from .base import BaseLLMGateway

class AnthropicClient(BaseLLMGateway):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.client = AsyncAnthropic(api_key=self.api_key)

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> Dict[str, Any]:
        system_msg = ""
        filtered_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                filtered_msgs.append(m)

        res = await self.client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=system_msg,
            messages=filtered_msgs
        )
        content_text = ""
        for block in res.content:
            if hasattr(block, "text"):
                content_text += block.text

        return {
            "content": content_text,
            "tool_calls": None,
            "prompt_tokens": res.usage.input_tokens,
            "completion_tokens": res.usage.output_tokens,
        }

    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        system_msg = ""
        filtered_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                filtered_msgs.append(m)

        async with self.client.messages.stream(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=system_msg,
            messages=filtered_msgs
        ) as stream:
            async for text in stream.text_stream:
                yield {"type": "content", "content": text}
