import openai
from typing import List, Dict, Any

class ConversationSummarizer:
    @staticmethod
    async def summarize_context(messages: List[Dict[str, str]], api_key: str | None = None) -> str:
        if len(messages) < 8:
            return ""
        
        dialogue = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
        try:
            client = openai.AsyncOpenAI(api_key=api_key or "sk-dummy")
            res = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Condense the conversation history into key factual bullet points to conserve context tokens."},
                    {"role": "user", "content": dialogue}
                ],
                max_tokens=250
            )
            return res.choices[0].message.content or ""
        except Exception:
            return ""
