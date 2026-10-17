import json
from typing import Any, AsyncGenerator

def format_sse(data: Any, event: str = "message") -> str:
    """Format data into Server-Sent Events (SSE) compliant wire protocol string."""
    if isinstance(data, (dict, list)):
        payload = json.dumps(data)
    else:
        payload = str(data)
    return f"event: {event}\ndata: {payload}\n\n"

async def sse_generator(generator: AsyncGenerator[dict, None]) -> AsyncGenerator[str, None]:
    async for item in generator:
        event = item.get("event", "message")
        data = item.get("data", {})
        yield format_sse(data=data, event=event)
