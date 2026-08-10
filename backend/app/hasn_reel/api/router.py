"""短视频（reel）API 路由聚合（设计 doc29 §5）。

仅挂载一面（其余 codegen 裸面**刻意不挂载**，对齐 hasn_studio/finance/quant/creator）：
- 用户端（`app`，仅 JWT）：owner 业务面 = 短视频项目化创作（项目 CRUD + 创作历史 + 进度/产物 + daemon
  同步），包裹 reel_service 做**行级隔离**（owner_hasn_id），替代 codegen 裸 CRUD。webui 经 daemon
  `/api/v1/reel/*` 薄代理调用（铁律）。

**刻意不挂载**（安全/架构）：
- `open`（无需认证）：会把项目/创作表**公开无鉴权读写**——越权大洞，退役。
- `agent`（Agent Key REST）：分身走本地 hasn-mcp（reel 引擎本地 sidecar），不经云端 REST。
- `admin`（JWT + RBAC codegen CRUD）：**已整体删除**。AI-Native 应用移出平台归属——每个应用经
  SDK 接入、自选语言、**自建业务运营面**，云端后台不再按表生成运营 CRUD。
"""

from fastapi import APIRouter

# --- 用户端（仅 JWT）：owner 业务面（包裹 reel_service，行级隔离），替代 codegen 裸 CRUD ---
from backend.app.hasn_reel.api.v1.app.reel import router as app_reel_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）  前缀: /api/v1/hasn_reel/
# codegen admin CRUD 已下线（应用自建运营面）；此处仅保留空的 v1 以维持
# backend/app/router.py 的装载契约（`from ... import v1`）不变。
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_reel', tags=['短视频-管理端'])

# ========================================
# 用户端 API（仅 JWT，owner 行级隔离）  前缀: /api/v1/hasn_reel/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_reel/app', tags=['短视频-用户端'])

app.include_router(app_reel_router)
