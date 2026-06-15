"""客户端 companion API 聚合路由（hasn_client 应用）.

挂载位置: /api/v1/app（URL 兼容——移动端 + 桌面端客户端已发布版本依赖，ADR-15 收编 R2 仅迁目录不改 URL）。
服务对象：推送 / 灰度（feature flags）/ 遥测 / owner api key 等全体客户端共用能力。
"""
from fastapi import APIRouter

from backend.app.hasn_client.api.feature_flags import router as feature_flags_router
from backend.app.hasn_client.api.owner_api_keys import router as owner_api_keys_router
from backend.app.hasn_client.api.push_receipts import router as push_receipts_router
from backend.app.hasn_client.api.push_tokens import router as push_tokens_router
from backend.app.hasn_client.api.telemetry import router as telemetry_router
from backend.core.conf import settings

client_router = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/app', tags=['客户端 App'])
client_router.include_router(owner_api_keys_router, prefix='/owner_api_keys')
client_router.include_router(push_tokens_router, prefix='/push_tokens')
client_router.include_router(push_receipts_router, prefix='/push_receipts')
client_router.include_router(telemetry_router, prefix='/telemetry')
client_router.include_router(feature_flags_router, prefix='/feature-flags')
