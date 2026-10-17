from typing import AsyncGenerator
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database.postgres import get_db
from database.redis import get_redis
from database.vector import get_qdrant
from database.mongodb import get_mongo_db
from database.s3_minio import get_s3_client, S3MinioClient
from database.clickhouse import get_clickhouse_client
from src.utils.security import decode_access_token
from src.models.relational.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_postgres_db() -> AsyncGenerator[AsyncSession, None]:
    async for db in get_db():
        yield db

def get_redis_client():
    return get_redis()

def get_vector_db():
    return get_qdrant()

def get_nosql_db():
    return get_mongo_db()

def get_s3() -> S3MinioClient:
    return get_s3_client()

def get_telemetry_db():
    return get_clickhouse_client()

async def get_current_user(
    db: AsyncSession = Depends(get_postgres_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    stmt = select(User).where(User.id == UUID(user_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
