"""统一视频引擎 API 路由聚合（STUDIO-P3，设计 doc22 §3）。

挂载两面（其余 codegen 裸面**刻意不挂载**）：
- 管理端（`v1`，JWT + RBAC）：运营/管理控制台查项目/素材/渲染/成品（codegen admin CRUD）。
- 用户端（`app`，仅 JWT）：owner 业务面 = 视频工作台全链路，包裹 studio_service 做**行级隔离**
  （owner_hasn_id），替代 codegen 裸 CRUD。WebUI 经 daemon `/api/v1/studio/*` 薄代理调用（铁律）。

**刻意不挂载**（安全/架构，对齐 finance/quant/creator）：
- `open`（无需认证）：会把项目/素材/渲染/成品表**公开无鉴权读写**——越权大洞，退役。
- `agent`（Agent Key REST）：分身走云端 MCP（`hasn.studio.*` gateway_internal handler），不经 REST。
"""

from fastapi import APIRouter

# --- 管理端（JWT + RBAC）：运营控制台（codegen admin CRUD） ---
from backend.app.hasn_studio.api.v1.admin.studio_artifact import router as admin_studio_artifact_router
from backend.app.hasn_studio.api.v1.admin.studio_asset import router as admin_studio_asset_router
from backend.app.hasn_studio.api.v1.admin.studio_project import router as admin_studio_project_router
from backend.app.hasn_studio.api.v1.admin.studio_render_job import router as admin_studio_render_job_router

# --- 用户端（仅 JWT）：owner 业务面（包裹 studio_service，行级隔离），替代 codegen 裸 CRUD ---
from backend.app.hasn_studio.api.v1.app.studio import router as app_studio_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）  前缀: /api/v1/hasn_studio/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio', tags=['统一视频引擎-管理端'])

v1.include_router(admin_studio_project_router, prefix='/studio-project', tags=['统一视频引擎-管理端-项目'])
v1.include_router(admin_studio_asset_router, prefix='/studio/assets', tags=['统一视频引擎-管理端-素材'])
v1.include_router(admin_studio_render_job_router, prefix='/studio/render/jobs', tags=['统一视频引擎-管理端-渲染'])
v1.include_router(admin_studio_artifact_router, prefix='/studio/artifacts', tags=['统一视频引擎-管理端-成品'])

# ========================================
# 用户端 API（仅 JWT，owner 行级隔离）  前缀: /api/v1/hasn_studio/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio/app', tags=['统一视频引擎-用户端'])

app.include_router(app_studio_router)
