"""统一视频引擎 API 路由聚合（STUDIO-P3，设计 doc22 §3）。

仅挂载一面（其余 codegen 裸面**刻意不挂载**）：
- 用户端（`app`，仅 JWT）：owner 业务面 = 视频工作台全链路，包裹 studio_service 做**行级隔离**
  （owner_hasn_id），替代 codegen 裸 CRUD。WebUI 经 daemon `/api/v1/studio/*` 薄代理调用（铁律）。

**刻意不挂载**（安全/架构，对齐 finance/quant/creator）：
- `open`（无需认证）：会把项目/素材/渲染/成品表**公开无鉴权读写**——越权大洞，退役。
- `agent`（Agent Key REST）：分身走云端 MCP（`hasn.studio.*` gateway_internal handler），不经 REST。
- `admin`（JWT + RBAC codegen CRUD）：**已整体删除**。AI-Native 应用移出平台归属——每个应用经
  SDK 接入、自选语言、**自建业务运营面**，云端后台不再按表生成运营 CRUD。
"""

from fastapi import APIRouter

# --- 用户端（仅 JWT）：owner 业务面（包裹 studio_service，行级隔离），替代 codegen 裸 CRUD ---
from backend.app.hasn_studio.api.v1.app.studio import router as app_studio_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）  前缀: /api/v1/hasn_studio/
# codegen admin CRUD 已下线（应用自建运营面）；此处仅保留空的 v1 以维持
# backend/app/router.py 的装载契约（`from ... import v1`）不变。
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio', tags=['统一视频引擎-管理端'])

# ========================================
# 用户端 API（仅 JWT，owner 行级隔离）  前缀: /api/v1/hasn_studio/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_studio/app', tags=['统一视频引擎-用户端'])

app.include_router(app_studio_router)
