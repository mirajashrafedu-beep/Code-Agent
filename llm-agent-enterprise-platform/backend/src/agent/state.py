import json
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from database.redis import get_redis

class AgentState(BaseModel):
    conversation_id: str
    user_id: str
    model_name: str = "gpt-4o"
    current_step: int = 0
    max_steps: int = 6
    thoughts: List[str] = []
    actions: List[Dict[str, Any]] = []
    observations: List[Any] = []
    rag_sources: List[Dict[str, Any]] = []

    async def save_to_redis(self):
        r = get_redis()
        key = f"agent_state:{self.conversation_id}"
        await r.setex(key, 3600, self.model_dump_json())

    @classmethod
    async def load_from_redis(cls, conversation_id: str, user_id: str) -> "AgentState":
        r = get_redis()
        key = f"agent_state:{conversation_id}"
        data = await r.get(key)
        if data:
            return cls.model_validate_json(data)
        return cls(conversation_id=conversation_id, user_id=user_id)
