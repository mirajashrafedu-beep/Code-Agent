from datetime import datetime
from typing import List, Optional
from beanie import Document
from pydantic import Field

class PromptTemplateDocument(Document):
    name: str
    category: str  # 'system_persona', 'cot_reasoning', 'rag_extraction'
    description: Optional[str] = None
    template: str
    variables: List[str] = []
    version: int = 1
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "prompt_templates"
        indexes = [
            "name",
            "category",
            "is_active",
        ]
