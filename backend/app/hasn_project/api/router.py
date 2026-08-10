from fastapi import APIRouter

from backend.core.conf import settings

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
#
# codegen admin CRUD 已下线：AI-Native 应用移出平台归属——每个应用经 SDK 接入、自选语言、
# **自建业务运营面**，云端后台不再按表生成运营 CRUD。
# 空的 v1 予以保留：本模块的 v1 本就未装载进主路由（_load_hasn_project 只挂 app 面），
# 留空壳只为不改动本文件其余三面的对称结构。
# service/crud/model 全部保留——api/v1/agent/* 与 api/v1/open/* 仍在 import 复用。
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/project', tags=['平台项目（doc38）管理'])

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
