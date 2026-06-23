from fastapi import APIRouter

from backend.app.hasn_studio.api.v1.admin.studio_artifact import router as admin_studio_artifact_router
from backend.app.hasn_studio.api.v1.admin.studio_asset import router as admin_studio_asset_router

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_studio.api.v1.admin.studio_project import router as admin_studio_project_router
from backend.app.hasn_studio.api.v1.admin.studio_render_job import router as admin_studio_render_job_router
from backend.app.hasn_studio.api.v1.agent.studio_artifact import router as agent_studio_artifact_router
from backend.app.hasn_studio.api.v1.agent.studio_asset import router as agent_studio_asset_router

# --- Agent（Agent Key） ---
from backend.app.hasn_studio.api.v1.agent.studio_project import router as agent_studio_project_router
from backend.app.hasn_studio.api.v1.agent.studio_render_job import router as agent_studio_render_job_router
from backend.app.hasn_studio.api.v1.app.studio_artifact import router as app_studio_artifact_router
from backend.app.hasn_studio.api.v1.app.studio_asset import router as app_studio_asset_router

# --- 用户端（仅 JWT） ---
from backend.app.hasn_studio.api.v1.app.studio_project import router as app_studio_project_router
from backend.app.hasn_studio.api.v1.app.studio_render_job import router as app_studio_render_job_router
from backend.app.hasn_studio.api.v1.open.studio_artifact import router as open_studio_artifact_router
from backend.app.hasn_studio.api.v1.open.studio_asset import router as open_studio_asset_router

# --- 公开（无需认证） ---
from backend.app.hasn_studio.api.v1.open.studio_project import router as open_studio_project_router
from backend.app.hasn_studio.api.v1.open.studio_render_job import router as open_studio_render_job_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）
# 路径前缀: /api/v1/hasn_studio/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）管理'])

v1.include_router(admin_studio_project_router, prefix='/studio-project', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）管理-视频项目（统一视频引擎 studio：管线/素材/成品的容器）'])
v1.include_router(admin_studio_asset_router, prefix='/studio/assets', tags=['视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）-视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）'])
v1.include_router(admin_studio_render_job_router, prefix='/studio/render/jobs', tags=['视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）-视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）'])
v1.include_router(admin_studio_artifact_router, prefix='/studio/artifacts', tags=['视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）-视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）'])

# ========================================
# 用户端 API（仅 JWT，无 RBAC）
# 路径前缀: /api/v1/hasn_studio/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio/app', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）用户端'])

app.include_router(app_studio_project_router, prefix='/studio-project', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）用户端-视频项目（统一视频引擎 studio：管线/素材/成品的容器）'])
app.include_router(app_studio_asset_router, prefix='/studio/assets', tags=['视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）-视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）'])
app.include_router(app_studio_render_job_router, prefix='/studio/render/jobs', tags=['视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）-视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）'])
app.include_router(app_studio_artifact_router, prefix='/studio/artifacts', tags=['视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）-视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）'])

# ========================================
# 公开 API（无需认证）
# 路径前缀: /api/v1/hasn_studio/open/
# ========================================
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio/open', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）公开'])

open_api.include_router(open_studio_project_router, prefix='/studio-project', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）公开-视频项目（统一视频引擎 studio：管线/素材/成品的容器）'])
open_api.include_router(open_studio_asset_router, prefix='/studio/assets', tags=['视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）-视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）'])
open_api.include_router(open_studio_render_job_router, prefix='/studio/render/jobs', tags=['视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）-视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）'])
open_api.include_router(open_studio_artifact_router, prefix='/studio/artifacts', tags=['视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）-视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）'])

# ========================================
# Agent API
# 路径前缀: /api/v1/hasn_studio/agent/
# ========================================
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio/agent', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）Agent'])

agent.include_router(agent_studio_project_router, prefix='/studio-project', tags=['视频项目（统一视频引擎 studio：管线/素材/成品的容器）Agent-视频项目（统一视频引擎 studio：管线/素材/成品的容器）'])
agent.include_router(agent_studio_asset_router, prefix='/studio/assets', tags=['视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）-视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）'])
agent.include_router(agent_studio_render_job_router, prefix='/studio/render/jobs', tags=['视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）-视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）'])
agent.include_router(agent_studio_artifact_router, prefix='/studio/artifacts', tags=['视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）-视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）'])
