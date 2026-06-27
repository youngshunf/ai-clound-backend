"""短视频（reel）API 路由聚合（设计 doc29 §5）。

挂载两面（其余 codegen 裸面**刻意不挂载**，对齐 hasn_studio/finance/quant/creator）：
- 管理端（`v1`，JWT + RBAC）：运营/管理控制台查项目/创作（codegen admin CRUD）。
- 用户端（`app`，仅 JWT）：owner 业务面 = 短视频项目化创作（项目 CRUD + 创作历史 + 进度/产物 + daemon
  同步），包裹 reel_service 做**行级隔离**（owner_hasn_id），替代 codegen 裸 CRUD。webui 经 daemon
  `/api/v1/reel/*` 薄代理调用（铁律）。

**刻意不挂载**（安全/架构）：
- `open`（无需认证）：会把项目/创作表**公开无鉴权读写**——越权大洞，退役。
- `agent`（Agent Key REST）：分身走本地 hasn-mcp（reel 引擎本地 sidecar），不经云端 REST。
"""

from fastapi import APIRouter

# --- 管理端（JWT + RBAC）：运营控制台（codegen admin CRUD） ---
from backend.app.hasn_reel.api.v1.admin.reel_creation import router as admin_reel_creation_router
from backend.app.hasn_reel.api.v1.admin.reel_project import router as admin_reel_project_router

# --- 用户端（仅 JWT）：owner 业务面（包裹 reel_service，行级隔离），替代 codegen 裸 CRUD ---
from backend.app.hasn_reel.api.v1.app.reel import router as app_reel_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）  前缀: /api/v1/hasn_reel/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_reel', tags=['短视频-管理端'])

v1.include_router(admin_reel_project_router, prefix='/reel-project', tags=['短视频-管理端-项目'])
v1.include_router(admin_reel_creation_router, prefix='/reel/creations', tags=['短视频-管理端-创作'])

# ========================================
# 用户端 API（仅 JWT，owner 行级隔离）  前缀: /api/v1/hasn_reel/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_reel/app', tags=['短视频-用户端'])

app.include_router(app_reel_router)
