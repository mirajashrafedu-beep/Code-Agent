from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional, List, Dict, Any

class ThreadCreate(BaseModel):
    title: Optional[str] = "New Conversation"
    model_override: Optional[str] = None
    system_persona: Optional[str] = None

class ThreadUpdate(BaseModel):
    title: Optional[str] = None
    is_pinned: Optional[bool] = None
    model_override: Optional[str] = None
    system_persona: Optional[str] = None

class MessageSchema(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    token_count: int
    tool_calls: Optional[Any] = None
    rag_sources: Optional[Any] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ThreadSummary(BaseModel):
    id: UUID
    title: str
    is_pinned: bool
    created_at: datetime
    updated_at: datetime
    message_count: Optional[int] = 0

    class Config:
        from_attributes = True

class ThreadDetail(BaseModel):
    id: UUID
    title: str
    is_pinned: bool
    model_override: Optional[str] = None
    system_persona: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    messages: List[MessageSchema] = []

    class Config:
        from_attributes = True
