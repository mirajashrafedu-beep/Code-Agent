from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.deps import get_postgres_db, get_current_user
from src.schemas.conversation import ThreadCreate, ThreadUpdate, ThreadSummary, ThreadDetail
from src.services.conversation_service import ConversationService
from src.models.relational.user import User

router = APIRouter()

@router.get("/", response_model=List[ThreadSummary])
async def list_threads(
    db: AsyncSession = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user)
):
    service = ConversationService(db)
    return await service.list_conversations(user_id=current_user.id)

@router.post("/", response_model=ThreadDetail, status_code=status.HTTP_201_CREATED)
async def create_thread(
    thread_in: ThreadCreate,
    db: AsyncSession = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user)
):
    service = ConversationService(db)
    return await service.create_conversation(user_id=current_user.id, thread_in=thread_in)

@router.get("/{conversation_id}", response_model=ThreadDetail)
async def get_thread(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user)
):
    service = ConversationService(db)
    return await service.get_conversation(conversation_id=conversation_id, user_id=current_user.id)

@router.patch("/{conversation_id}", response_model=ThreadDetail)
async def update_thread(
    conversation_id: UUID,
    update_in: ThreadUpdate,
    db: AsyncSession = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user)
):
    service = ConversationService(db)
    return await service.update_conversation(conversation_id=conversation_id, user_id=current_user.id, update_in=update_in)

@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user)
):
    service = ConversationService(db)
    await service.delete_conversation(conversation_id=conversation_id, user_id=current_user.id)
    return None
