from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import HTTPException, status
from src.models.relational.user import User, UserQuota
from src.schemas.auth import UserCreate, UserLogin
from src.utils.security import get_password_hash, verify_password, create_access_token

class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, user_in: UserCreate) -> User:
        stmt = select(User).where(User.email == user_in.email)
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email address already exists.",
            )

        new_user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name,
            subscription_tier="free",
        )
        self.db.add(new_user)
        await self.db.flush()

        quota = UserQuota(user_id=new_user.id)
        self.db.add(quota)
        await self.db.commit()
        await self.db.refresh(new_user)
        return new_user

    async def authenticate(self, credentials: UserLogin) -> tuple[User, str]:
        stmt = select(User).where(User.email == credentials.email)
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user or not verify_password(credentials.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password credentials.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = create_access_token(subject=user.id)
        return user, token
