"""素材站目录管理面路由聚合（A-P2-0）。

- 平台 admin 面（Admin JWT + RBAC）：`/api/v1/hasn_stock/*` —— Vben Admin。

**刻意不挂 open/agent 裸 CRUD 面**：素材站 api_key 是平台共享付费凭据 → 公开无鉴权读写 = 泄密大洞；
分身经云端 MCP `hasn.stock.search` / `hasn.stock.download`（目录经 provider_store 权威读）触达，不经 REST。
"""

from fastapi import APIRouter

from backend.app.hasn_stock.api.v1.admin.providers import router as admin_providers_router
from backend.core.conf import settings

# 平台 admin 面：/api/v1/hasn_stock/*
admin = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_stock', tags=['素材站目录-Admin'])
admin.include_router(admin_providers_router)
