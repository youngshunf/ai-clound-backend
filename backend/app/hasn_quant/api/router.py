"""量化研究 API 路由聚合（QUANT-P3，设计 23 §6）。

挂载两面（其余 codegen 裸面**刻意不挂载**）：
- 管理端（`v1`，JWT + RBAC）：运营/管理控制台查策略与回测（codegen admin CRUD）。
- 用户端（`app`，仅 JWT）：owner 业务面 = 回测研究全链路，包裹 quant_service 做**行级隔离**
  （owner_hasn_id），替代 codegen 裸 CRUD。WebUI 经 daemon `/api/v1/quant/*` 薄代理调用（铁律）。

**刻意不挂载**（安全/架构）：
- `open`（无需认证）：会把策略/回测表**公开无鉴权读写**——越权大洞，退役。
- `agent`（Agent Key REST）：分身走云端 MCP（`hasn.quant.*` gateway_internal handler），不经 REST。
（对齐 finance/creator：codegen 全四面生成，落地只保留有意义的面。）

实盘线（deploy_live/submit_order，P6+ 真钱强闸）不在任何 REST 面暴露——本期仅回测研究。
"""

from fastapi import APIRouter

from backend.app.hasn_quant.api.v1.admin.quant_backtest_run import router as admin_quant_backtest_run_router

# --- 管理端（JWT + RBAC）：运营控制台 ---
from backend.app.hasn_quant.api.v1.admin.quant_strategy import router as admin_quant_strategy_router

# --- 用户端（仅 JWT）：owner 业务面（包裹 quant_service，行级隔离），替代 codegen 裸 CRUD ---
from backend.app.hasn_quant.api.v1.app.quant import router as app_quant_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）  前缀: /api/v1/hasn_quant/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant', tags=['量化研究-管理端'])

v1.include_router(admin_quant_strategy_router, prefix='/quant-strategy', tags=['量化研究-管理端-策略'])
v1.include_router(admin_quant_backtest_run_router, prefix='/quant/backtest/runs', tags=['量化研究-管理端-回测'])

# ========================================
# 用户端 API（仅 JWT，owner 行级隔离）  前缀: /api/v1/hasn_quant/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_quant/app', tags=['量化研究-用户端（owner 回测研究）'])

app.include_router(app_quant_router)
