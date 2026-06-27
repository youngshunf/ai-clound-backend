"""第三方 MCP 网关管理面路由聚合（P7-D）。

- owner 面（Owner JWT）：`/api/v1/external_mcp/app/*` —— webui 经 daemon 薄代理。
- 平台 admin 面（Admin JWT + RBAC）：`/api/v1/external_mcp/*` —— Vben Admin。

**刻意不挂载 open/agent 裸 CRUD 面**：external server/secret/binding 的公开无鉴权读写 =
越权大洞；Agent 经云端 MCP（tool.search/代理调用）而非 REST 触达 external 工具。
secret 表更无任何 REST CRUD（密文/明文绝不经 API 出入，仅管理面的 write/rotate/revoke 动作端点）。
"""

from fastapi import APIRouter

from backend.app.external_mcp.api.v1.admin.management import router as admin_management_router
from backend.app.external_mcp.api.v1.app.management import router as app_management_router
from backend.core.conf import settings

# owner 面：/api/v1/external_mcp/app/*
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/external_mcp/app', tags=['第三方MCP网关-Owner'])
app.include_router(app_management_router)

# 平台 admin 面：/api/v1/external_mcp/*
admin = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/external_mcp', tags=['第三方MCP网关-Admin'])
admin.include_router(admin_management_router)
