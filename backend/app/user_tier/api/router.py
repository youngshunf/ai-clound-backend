from fastapi import APIRouter

from backend.app.user_tier.api.v1.subscription_tier import router as subscription_tier_router
from backend.core.conf import settings

v1 = APIRouter(prefix=settings.FASTAPI_API_V1_PATH, tags=['订阅等级配置表 - 定义不同订阅等级的权益'])

v1.include_router(subscription_tier_router, prefix='/subscription/tiers')
