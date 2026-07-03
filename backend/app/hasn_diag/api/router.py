"""hasn_diag 应用路由聚合（错误诊断与可观测性·doc21）。

P1 只暴露 owner 端：错误上行 `:sync` + owner 只读 `GET /errors`。issue 读/管理（P3b）
走云端 `hasn.diag.*` MCP 工具（service 层已就绪），不在此挂 HTTP 路由。
"""

from fastapi import APIRouter

from backend.app.hasn_diag.api.v1.app.errors import router as errors_app_router
from backend.core.conf import settings

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/diag/app', tags=['错误诊断-用户端'])
app.include_router(errors_app_router)
