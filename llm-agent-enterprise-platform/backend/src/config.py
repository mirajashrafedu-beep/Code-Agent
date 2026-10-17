from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "llm-agent-enterprise-platform"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "super-secret-jwt-key-change-this-in-production-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # PostgreSQL
    POSTGRES_URI: str = "postgresql+asyncpg://postgres:postgres_secure_password@postgres:5432/llm_enterprise_db"

    # Qdrant
    QDRANT_HOST: str = "qdrant"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "user_knowledge_base"

    # Redis
    REDIS_URI: str = "redis://redis:6379/0"

    # MongoDB
    MONGO_URI: str = "mongodb://mongo_admin:mongo_secure_password@mongodb:27017/llm_traces_db?authSource=admin"
    MONGO_DB: str = "llm_traces_db"

    # MinIO
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_PUBLIC_ENDPOINT: str = "http://localhost:9000"
    MINIO_ROOT_USER: str = "minio_admin"
    MINIO_ROOT_PASSWORD: str = "minio_secure_password"
    MINIO_BUCKET_UPLOADS: str = "user-uploads"
    MINIO_BUCKET_EXPORTS: str = "exports"

    # ClickHouse
    CLICKHOUSE_HOST: str = "clickhouse"
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = "clickhouse_secure_password"
    CLICKHOUSE_DB: str = "telemetry_db"

    # LLM & Tools
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None
    SANDBOX_URL: str = "http://sandbox:8080"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "allow"

settings = Settings()
