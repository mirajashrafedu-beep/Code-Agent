from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Any

class BaseLLMGateway(ABC):
    @abstractmethod
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> Dict[str, Any]:
        """Single turn completion"""
        pass

    @abstractmethod
    async def generate_stream(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        tools: List[Dict[str, Any]] | None = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming completion returning chunks and tool calls"""
        pass
