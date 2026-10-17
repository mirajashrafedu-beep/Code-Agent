from pydantic import BaseModel
from uuid import UUID
from typing import Optional, List, Dict, Any

class ChatRequest(BaseModel):
    conversation_id: Optional[UUID] = None
    prompt: str
    model_name: Optional[str] = "gpt-4o"
    system_persona: Optional[str] = None
    enable_rag: bool = True
    enable_sandbox: bool = True
    enable_web_search: bool = True

class ChatStreamChunk(BaseModel):
    event: str  # 'token', 'thought', 'tool_call', 'tool_result', 'sources', 'done', 'error'
    data: Dict[str, Any]
