import json
from typing import List, Dict, Any
from database.redis import get_redis

class ShortTermMemory:
    def __init__(self, conversation_id: str, max_messages: int = 10):
        self.conversation_id = conversation_id
        self.max_messages = max_messages
        self.key = f"buffer_history:{conversation_id}"

    async def add_message(self, role: str, content: str):
        r = get_redis()
        msg_json = json.dumps({"role": role, "content": content})
        await r.rpush(self.key, msg_json)
        await r.ltrim(self.key, -self.max_messages, -1)
        await r.expire(self.key, 86400)

    async def get_recent_messages(self) -> List[Dict[str, str]]:
        r = get_redis()
        items = await r.lrange(self.key, 0, -1)
        return [json.loads(i) for i in items]
