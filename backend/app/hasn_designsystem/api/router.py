"""设计系统生成应用（app_id=designsystem）路由聚合。

当前仅装配 **Agent 端真实路由**（DS-P4，daemon hasn-mcp 经 BackendGateway.for_agent 调用面）。
owner/WebUI 端（app scope）真实路由在 DS-P8 落地。codegen 生成的泛型 admin/app/open per-table
路由为产物留盘但**不接线**（与自定义业务 service 不兼容）。
"""

from fastapi import APIRouter

from backend.app.hasn_designsystem.api.v1.agent.designsystem import router as agent_designsystem_router
from backend.core.conf import settings

v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/designsystem')

# Agent 端：/api/v1/designsystem/agent/*（Agent JWT）
v1.include_router(agent_designsystem_router, prefix='/agent', tags=['设计系统（Agent 端）'])
