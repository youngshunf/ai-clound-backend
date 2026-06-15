"""new-api 集成模块路由聚合（D1/D5，2026-06-15）。

承接原 `app/llm/api/router.py` 中三块**非网关**路由，保持对外路径不变（前端 / openclaw /
daemon 已依赖）：

- `/api/v1/llm/api-keys`        自建 API Key 体系（Owner，D1）
- `/api/v1/llm/newapi-mappings` new-api 用户映射管理（Admin）
- `/api/v1/llm/app/newapi`      new-api 用量与额度（用户端）

> 代码归属已迁 `app/newapi`，但 URL 前缀 `/llm/*` 保留：纯 URL 兼容，与被删的自建网关无关。
"""

from fastapi import APIRouter

from backend.app.newapi.api.v1.admin.llm_newapi_user_mapping import router as admin_newapi_mapping_router
from backend.app.newapi.api.v1.app.llm_newapi_user_mapping import router as app_newapi_mapping_router
from backend.app.newapi.api.v1.llm_models import router as llm_models_router
from backend.app.newapi.api.v1.llm_usage import router as llm_usage_router
from backend.app.newapi.apikey.api import router as api_keys_router
from backend.core.conf import settings

# Owner / Admin 级（沿用 /api/v1/llm 前缀）
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/llm')
v1.include_router(api_keys_router, prefix='/api-keys', tags=['LLM API Key 管理'])
v1.include_router(admin_newapi_mapping_router, prefix='/newapi-mappings', tags=['new-api 用户映射管理'])
# 删网关后接管的两条「存活」路径（daemon 依赖；new-api 权威，无 DB 直连）：
#   GET /api/v1/llm/usage/summary    用量汇总（Owner JWT）
#   GET /api/v1/llm/models/available 可用模型目录（公开）
v1.include_router(llm_usage_router, prefix='/usage', tags=['LLM 用量（new-api 权威）'])
v1.include_router(llm_models_router, prefix='/models', tags=['LLM 可用模型（new-api 权威）'])

# 用户端（沿用 /api/v1/llm/app 前缀）
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/llm/app')
app.include_router(app_newapi_mapping_router, prefix='/newapi', tags=['new-api 用量与额度'])
