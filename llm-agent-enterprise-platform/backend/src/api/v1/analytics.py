from fastapi import APIRouter, Depends
from src.api.deps import get_current_user
from src.services.telemetry_service import TelemetryService
from src.models.relational.user import User

router = APIRouter()

@router.get("/usage")
async def get_user_analytics(current_user: User = Depends(get_current_user)):
    return TelemetryService.get_analytics_summary(user_id=str(current_user.id))

@router.get("/global")
async def get_global_analytics(current_user: User = Depends(get_current_user)):
    return TelemetryService.get_analytics_summary(user_id=None)
