from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.deps import get_postgres_db, get_current_user
from src.schemas.auth import UserCreate, UserLogin, UserResponse, TokenResponse
from src.services.auth_service import AuthService
from src.models.relational.user import User

router = APIRouter()

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_postgres_db)):
    auth_service = AuthService(db)
    user = await auth_service.register(user_in)
    return user

@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_postgres_db)):
    auth_service = AuthService(db)
    user, token = await auth_service.authenticate(credentials)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user
    }

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    return current_user
