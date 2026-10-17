import uuid
from uuid import UUID
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.deps import get_postgres_db, get_current_user
from src.schemas.chat import ChatRequest
from src.services.conversation_service import ConversationService
from src.schemas.conversation import ThreadCreate
from src.agent.core import ReActAgent
from src.models.relational.user import User

router = APIRouter()

@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    db: AsyncSession = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user)
):
    conv_service = ConversationService(db)
    
    # 1. Resolve or create conversation
    if request.conversation_id:
        conversation = await conv_service.get_conversation(request.conversation_id, current_user.id)
    else:
        conversation = await conv_service.create_conversation(
            current_user.id,
            ThreadCreate(title=request.prompt[:30], model_override=request.model_name)
        )

    # 2. Persist user message in Postgres
    await conv_service.add_message(
        conversation_id=conversation.id,
        role="user",
        content=request.prompt
    )

    # 3. Instantiate ReAct Agent
    agent = ReActAgent(
        user_id=str(current_user.id),
        conversation_id=str(conversation.id),
        model_name=request.model_name or "gpt-4o",
        system_persona=request.system_persona or conversation.system_persona,
        enable_rag=request.enable_rag,
        enable_sandbox=request.enable_sandbox,
        enable_web_search=request.enable_web_search
    )

    async def event_generator():
        accumulated_assistant_reply = ""
        rag_sources_captured = None

        async for chunk in agent.execute_stream(request.prompt):
            event = chunk.get("event")
            data = chunk.get("data", {})
            
            if event == "token":
                accumulated_assistant_reply += data.get("token", "")
            elif event == "sources":
                rag_sources_captured = data.get("sources")

            yield {"event": event, "data": data}

        # 4. Save assistant reply to Postgres
        await conv_service.add_message(
            conversation_id=conversation.id,
            role="assistant",
            content=accumulated_assistant_reply,
            rag_sources=rag_sources_captured
        )

    return EventSourceResponse(event_generator())
