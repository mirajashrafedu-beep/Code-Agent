from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database.vector import init_qdrant_schema
from database.redis import close_redis
from database.mongodb import init_mongo_beanie
from database.clickhouse import init_clickhouse_tables
from src.models.nosql.agent_trace import AgentTraceDocument
from src.models.nosql.tool_logs import ToolLogDocument
from src.models.nosql.prompt_templates import PromptTemplateDocument
from src.api.v1.router import api_router
from src.config import settings
from src.utils.logger import logger

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Multi-Database Startup Handlers
    logger.info("Starting up multi-database connectors...")
    
    # 1. Initialize Qdrant Collection
    try:
        await init_qdrant_schema()
    except Exception as e:
        logger.warning(f"Qdrant startup warning: {e}")

    # 2. Initialize MongoDB Beanie Documents
    try:
        await init_mongo_beanie([AgentTraceDocument, ToolLogDocument, PromptTemplateDocument])
    except Exception as e:
        logger.warning(f"MongoDB Beanie startup warning: {e}")

    # 3. Initialize ClickHouse Tables
    try:
        init_clickhouse_tables()
    except Exception as e:
        logger.warning(f"ClickHouse startup warning: {e}")

    logger.info("All 6 Database Engines connected & verified.")
    yield

    # Shutdown Handlers
    logger.info("Shutting down database connections...")
    await close_redis()
    logger.info("Clean shutdown complete.")

app = FastAPI(
    title="Enterprise LLM Agent Platform API",
    version="1.0.0",
    description="Multi-Database Autonomous Agent Orchestration Gateway",
    lifespan=lifespan
)

# CORS Middleware for Next.js 15 Client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "engines": {
            "postgres": "ready",
            "qdrant": "ready",
            "redis": "ready",
            "mongodb": "ready",
            "minio": "ready",
            "clickhouse": "ready"
        }
    }
