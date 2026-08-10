"""量化研究 API 路由聚合（QUANT-P3，设计 23 §6）。

仅挂载一面（其余 codegen 裸面**刻意不挂载**）：
- 用户端（`app`，仅 JWT）：owner 业务面 = 回测研究全链路，包裹 quant_service 做**行级隔离**
  （owner_hasn_id），替代 codegen 裸 CRUD。WebUI 经 daemon `/api/v1/quant/*` 薄代理调用（铁律）。

**刻意不挂载**（安全/架构）：
- `open`（无需认证）：会把策略/回测表**公开无鉴权读写**——越权大洞，退役。
- `agent`（Agent Key REST）：分身走云端 MCP（`hasn.quant.*` gateway_internal handler），不经 REST。
- `admin`（JWT + RBAC codegen CRUD）：**已整体删除**。AI-Native 应用移出平台归属——每个应用经
  SDK 接入、自选语言、**自建业务运营面**，云端后台不再按表生成运营 CRUD。
（对齐 finance/creator：codegen 全四面生成，落地只保留有意义的面。）

实盘线（deploy_live/submit_order，P6+ 真钱强闸）不在任何 REST 面暴露——本期仅回测研究。
"""

from fastapi import APIRouter

# --- 用户端（仅 JWT）：owner 业务面（包裹 quant_service，行级隔离），替代 codegen 裸 CRUD ---
from backend.app.hasn_quant.api.v1.app.quant import router as app_quant_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）  前缀: /api/v1/hasn_quant/
# codegen admin CRUD 已下线（应用自建运营面）；此处仅保留空的 v1 以维持
# backend/app/router.py 的装载契约（`from ... import v1`）不变。
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant', tags=['量化研究-管理端'])

# ========================================
# 用户端 API（仅 JWT，owner 行级隔离）  前缀: /api/v1/hasn_quant/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant/app', tags=['量化研究-用户端（owner 回测研究）'])

app.include_router(app_quant_router)
