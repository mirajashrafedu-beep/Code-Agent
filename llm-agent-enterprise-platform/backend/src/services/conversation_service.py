from uuid import UUID
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from src.models.relational.conversation import Conversation
from src.models.relational.message import Message
from src.schemas.conversation import ThreadCreate, ThreadUpdate

class ConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_conversations(self, user_id: UUID) -> List[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_conversation(self, conversation_id: UUID, user_id: UUID) -> Conversation:
        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
        result = await self.db.execute(stmt)
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation thread not found")
        return conv

    async def create_conversation(self, user_id: UUID, thread_in: ThreadCreate) -> Conversation:
        conv = Conversation(
            user_id=user_id,
            title=thread_in.title or "New Conversation",
            model_override=thread_in.model_override,
            system_persona=thread_in.system_persona,
        )
        self.db.add(conv)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def update_conversation(self, conversation_id: UUID, user_id: UUID, update_in: ThreadUpdate) -> Conversation:
        conv = await self.get_conversation(conversation_id, user_id)
        if update_in.title is not None:
            conv.title = update_in.title
        if update_in.is_pinned is not None:
            conv.is_pinned = update_in.is_pinned
        if update_in.model_override is not None:
            conv.model_override = update_in.model_override
        if update_in.system_persona is not None:
            conv.system_persona = update_in.system_persona
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def delete_conversation(self, conversation_id: UUID, user_id: UUID) -> bool:
        conv = await self.get_conversation(conversation_id, user_id)
        await self.db.delete(conv)
        await self.db.commit()
        return True

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        token_count: int = 0,
        tool_calls: Optional[dict] = None,
        rag_sources: Optional[list] = None
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=token_count,
            tool_calls=tool_calls,
            rag_sources=rag_sources
        )
        self.db.add(msg)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg
