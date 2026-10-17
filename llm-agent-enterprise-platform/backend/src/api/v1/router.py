from fastapi import APIRouter
from src.api.v1 import auth, conversations, chat, files, memory, analytics

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["Conversations"])
api_router.include_router(chat.router, prefix="/chat", tags=["Agent Chat & Streaming"])
api_router.include_router(files.router, prefix="/files", tags=["MinIO S3 Storage & Uploads"])
api_router.include_router(memory.router, prefix="/memory", tags=["Vector Semantic Memory"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["ClickHouse Telemetry"])
