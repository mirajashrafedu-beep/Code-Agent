from datetime import datetime
from typing import Any, Optional
from beanie import Document
from pydantic import Field

class ToolLogDocument(Document):
    user_id: str
    conversation_id: str
    tool_name: str
    input_payload: Any
    output_payload: Any
    status: str  # 'success', 'error'
    error_message: Optional[str] = None
    execution_time_ms: float
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "tool_logs"
        indexes = [
            "user_id",
            "conversation_id",
            "tool_name",
            "created_at",
        ]
