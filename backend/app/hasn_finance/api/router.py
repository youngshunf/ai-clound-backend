"""金融数据 API 路由聚合（FIN-S3）。

只有 owner 用户端只读面（`app`）——金融数据是纯云端只读数据应用：
- Agent 面走云端 MCP（`hasn.finance.*` gateway_internal handler），**不在此 REST 路由**；
- 无管理端 CRUD、无 codegen 裸表（一期无 per-owner 金融状态表）。

路径前缀: `/api/v1/finance/app/*`。WebUI 经 daemon `/api/v1/finance/*` 薄代理调用本面（铁律）。
"""

from fastapi import APIRouter

from backend.app.hasn_finance.api.v1.app.finance import router as app_finance_router
from backend.app.hasn_finance.api.v1.app.resources import router as app_resources_router
from backend.app.hasn_finance.api.v1.app.sync import router as app_sync_router
from backend.core.conf import settings

# 用户端 API（仅 JWT）：行情只读看板 + 6 类产物/watchlist 的 :sync 上行（daemon outbox 投影）
# + 同 7 类的 list/get 下行读（daemon read-through 回源，05 §3.2.1）。
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/finance/app', tags=['金融投研-用户端'])

app.include_router(app_finance_router)
app.include_router(app_sync_router)
app.include_router(app_resources_router)
