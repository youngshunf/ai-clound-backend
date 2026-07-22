"""设计系统生成应用（app_id=designsystem）路由聚合。

装配两套真实路由（共用同一自定义 `design_system_service`，仅身份来源不同）：
- **Agent 端**（DS-P4，`/agent/*`，Agent JWT）：daemon hasn-mcp 经 `BackendGateway.for_agent` 调用面。
- **Owner/WebUI 端**（DS-P8，`/app/*`，Owner JWT）：daemon `domains/designsystem` 经
  `BackendGateway.owner_transport` 回源做 read_through/local_first；WebUI 只调 daemon。

已移除未接线的 codegen 泛型路由；它们与当前自定义领域服务不兼容，且不应作为潜在公开接口保留。
"""

from fastapi import APIRouter

from backend.app.hasn_designsystem.api.v1.agent.designsystem import router as agent_designsystem_router
from backend.app.hasn_designsystem.api.v1.app.designsystem import router as app_designsystem_router
from backend.core.conf import settings

v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/designsystem')

# Agent 端：/api/v1/designsystem/agent/*（Agent JWT）
v1.include_router(agent_designsystem_router, prefix='/agent', tags=['设计系统（Agent 端）'])
# Owner/WebUI 端：/api/v1/designsystem/app/*（Owner JWT，daemon webui-facing 回源通道）
v1.include_router(app_designsystem_router, prefix='/app', tags=['设计系统（Owner 端）'])
