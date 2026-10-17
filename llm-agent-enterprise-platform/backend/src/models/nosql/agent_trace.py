from datetime import datetime
from typing import Any, List, Optional
from beanie import Document
from pydantic import BaseModel, Field

class ExecutionStep(BaseModel):
    step_number: int
    step_type: str  # 'thought', 'action', 'observation', 'final_answer'
    thought: Optional[str] = None
    action_name: Optional[str] = None
    action_input: Optional[Any] = None
    observation: Optional[Any] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AgentTraceDocument(Document):
    user_id: str
    conversation_id: str
    message_id: Optional[str] = None
    prompt: str
    steps: List[ExecutionStep] = []
    final_output: Optional[str] = None
    total_tokens: int = 0
    duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "agent_traces"
        indexes = [
            "user_id",
            "conversation_id",
            "created_at",
        ]
