from sqlalchemy import Column, String, Boolean, BigInteger, Float, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from .base import Base, UUIDMixin, TimestampMixin

class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    subscription_tier = Column(String(50), default="free", nullable=False)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    quota = relationship("UserQuota", back_populates="user", uselist=False, cascade="all, delete-orphan")

class UserQuota(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_quotas"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    monthly_token_limit = Column(BigInteger, default=1000000, nullable=False)
    tokens_used_this_month = Column(BigInteger, default=0, nullable=False)
    monthly_spend_limit_usd = Column(Float, default=50.0, nullable=False)
    current_spend_usd = Column(Float, default=0.0, nullable=False)
    reset_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="quota")
