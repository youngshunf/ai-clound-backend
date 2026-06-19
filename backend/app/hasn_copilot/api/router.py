"""会议副驾（潜行会议副驾，模块 copilot，PG schema=hasn_copilot）模块路由聚合。

- /api/v1/copilot/app/*  用户端（Owner JWT，owner 硬隔离）

注：副驾数据为 owner 私有实时会议元数据，不开 admin/open/agent scope（codegen 生成的
通用 CRUD scope 已移除，避免按 pk 越权读他人会话）。Agent 侧若需访问，后续按
`/api/v1/copilot/agent/*` + Agent JWT 单独设计，不复用 owner route。
"""

from fastapi import APIRouter

from backend.app.hasn_copilot.api.v1.app.copilot import router as copilot_app_router
from backend.core.conf import settings

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/copilot/app', tags=['会议副驾-用户端'])
app.include_router(copilot_app_router)
