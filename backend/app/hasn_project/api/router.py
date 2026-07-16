from fastapi import APIRouter

from backend.core.conf import settings

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_project.api.v1.admin.hasn_project import router as admin_hasn_project_router
from backend.app.hasn_project.api.v1.admin.hasn_project_milestone import router as admin_hasn_project_milestone_router
# --- 用户端（仅 JWT） ---
from backend.app.hasn_project.api.v1.app.hasn_project import router as app_hasn_project_router
from backend.app.hasn_project.api.v1.app.hasn_project_milestone import router as app_hasn_project_milestone_router
# --- Agent（Agent JWT） ---
from backend.app.hasn_project.api.v1.agent.hasn_project import router as agent_hasn_project_router
from backend.app.hasn_project.api.v1.agent.hasn_project_milestone import router as agent_hasn_project_milestone_router
# --- 公开（无需认证） ---
from backend.app.hasn_project.api.v1.open.hasn_project import router as open_hasn_project_router
from backend.app.hasn_project.api.v1.open.hasn_project_milestone import router as open_hasn_project_milestone_router

# 平台项目（doc38）canonical 前缀统一为 /api/v1/project/*（对齐 app_id=project / entry_route=/apps/project /
# U5 daemon 代理 /api/v1/project/app），去掉 codegen 默认的模块名 hasn_project 前缀；子前缀归一为 /projects、/milestones。
# 说明：这些是 codegen 生成的通用 CRUD REST 面（owner app-scope 供 daemon 代理，admin 供后台）；
# Agent 主交互面是 hasn.project.* MCP 平台工具（U3），联邦挂靠/并集读等业务逻辑随 U3 补进 service。

# ========================================
# 管理端 API（JWT + RBAC）·前缀 /api/v1/project
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/project', tags=['平台项目（doc38）管理'])

v1.include_router(admin_hasn_project_router, prefix='/projects', tags=['平台项目（doc38）管理'])
v1.include_router(admin_hasn_project_milestone_router, prefix='/milestones', tags=['平台项目里程碑（doc38 §12.3）管理'])

# ========================================
# 用户端 API（仅 JWT）·前缀 /api/v1/project/app
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/project/app', tags=['平台项目（doc38）用户端'])

app.include_router(app_hasn_project_router, prefix='/projects', tags=['平台项目（doc38）用户端'])
app.include_router(app_hasn_project_milestone_router, prefix='/milestones', tags=['平台项目里程碑（doc38 §12.3）用户端'])

# ========================================
# 公开 API（无需认证）·前缀 /api/v1/project/open
# ========================================
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/project/open', tags=['平台项目（doc38）公开'])

open_api.include_router(open_hasn_project_router, prefix='/projects', tags=['平台项目（doc38）公开'])
open_api.include_router(open_hasn_project_milestone_router, prefix='/milestones', tags=['平台项目里程碑（doc38 §12.3）公开'])

# ========================================
# Agent API（Agent JWT）·前缀 /api/v1/project/agent
# ========================================
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/project/agent', tags=['平台项目（doc38）Agent'])

agent.include_router(agent_hasn_project_router, prefix='/projects', tags=['平台项目（doc38）Agent'])
agent.include_router(agent_hasn_project_milestone_router, prefix='/milestones', tags=['平台项目里程碑（doc38 §12.3）Agent'])
