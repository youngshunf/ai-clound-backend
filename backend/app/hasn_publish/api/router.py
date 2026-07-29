"""通用网页发布与分享（模块 18，app_id=publish）模块路由聚合。

- /api/v1/publish/app/*    用户端（Owner JWT，owner 隔离）——daemon PublishBroker 调
- /api/v1/publish/agent/*  Agent 端（Agent JWT，owner=agent.owner_hasn_id，scope 闸 publish:read/write）
- /s/{slug}                公开查看端点（独立分享域名，无鉴权外壳；见 hosting.py，P3 接入）

注：publish 为 owner 内容，不开 admin scope；公开面是 /s/{slug} 而非 open CRUD。
"""

from fastapi import APIRouter

from backend.app.hasn_publish.api.v1.agent.site import router as site_agent_router
from backend.app.hasn_publish.api.v1.app.site import router as site_app_router
from backend.app.hasn_publish.api.v1.internal.forms import router as forms_internal_router
from backend.app.hasn_publish.api.v1.open.hosting import router as hosting_router
from backend.app.hasn_publish.api.v1.open.meta import router as meta_open_router
from backend.core.conf import settings

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/publish/app', tags=['网页发布-用户端'])
app.include_router(site_app_router)

agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/publish/agent', tags=['网页发布-Agent端'])
agent.include_router(site_agent_router)

# 公开查看面 /s/{slug}（根路径，独立分享域名；无 /api/v1 前缀）
hosting = APIRouter(tags=['网页发布-公开查看'])
hosting.include_router(hosting_router)

# 公开元数据面 /api/v1/publish/open/*（带前缀走 CORS，供 website /s/{slug} SPA 查看器 fetch 判定态）
open_meta = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/publish/open', tags=['网页发布-公开元数据'])
open_meta.include_router(meta_open_router)

internal = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/publish/internal', tags=['网页发布-内部服务'])
internal.include_router(forms_internal_router)
