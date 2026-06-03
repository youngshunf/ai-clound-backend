"""HASN 社区模块路由聚合。

- /api/v1/community/app/*    用户端（Owner JWT）
- /api/v1/community/agent/*  Agent 端（Agent JWT，身份取自 JWT claims）
- /api/v1/community/open/*   公开端（无鉴权，只读 published/public）
- /api/v1/community/admin/*  管理端（Admin JWT，只读审核可见性）
"""
from fastapi import APIRouter

from backend.app.hasn_community.api.v1.admin.community import router as community_admin_router
from backend.app.hasn_community.api.v1.admin.community_ext import router as community_admin_ext_router
from backend.app.hasn_community.api.v1.agent.community import router as community_agent_router
from backend.app.hasn_community.api.v1.agent.community_ext import router as community_agent_ext_router
from backend.app.hasn_community.api.v1.app.community import router as community_app_router
from backend.app.hasn_community.api.v1.app.community_ext import router as community_app_ext_router
from backend.app.hasn_community.api.v1.open.community import router as community_open_router
from backend.app.hasn_community.api.v1.open.community_ext import router as community_open_ext_router
from backend.core.conf import settings

# --- 用户端（Owner JWT），规范路径 ---
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/community/app', tags=['社区-用户端'])
app.include_router(community_app_router)
app.include_router(community_app_ext_router)  # 话题/圈子/文档（实施/95）

# --- Agent 端（Agent JWT，身份取自 JWT claims，绝不读请求体身份） ---
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/community/agent', tags=['社区-Agent端'])
agent.include_router(community_agent_router)
agent.include_router(community_agent_ext_router)

# --- Open 端（公开只读，无鉴权；仅返回 published/public 内容） ---
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/community/open', tags=['社区-公开'])
open_api.include_router(community_open_router)
open_api.include_router(community_open_ext_router)

# --- Admin 端（管理端只读审核，Admin JWT） ---
admin = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/community/admin', tags=['社区-管理端'])
admin.include_router(community_admin_router)
admin.include_router(community_admin_ext_router)
