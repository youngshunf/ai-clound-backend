"""规划与目标管理应用（app_id=plan）路由聚合。

装配两套真实路由（共用同一自定义 `plan_service`，仅身份来源不同）：
- **Agent 端**（`/agent/*`，Agent JWT）：daemon hasn-mcp 经 `BackendGateway.for_agent` 调用面（P2 接 MCP）。
- **Owner/WebUI 端**（`/app/*`，Owner JWT）：daemon `domains/plan` 经 `BackendGateway.owner_transport`
  回源做 read_through/local_first；WebUI 只调 daemon（P1 daemon 镜像随 #1673 落地）。

codegen 生成的泛型 per-table 路由（goal/todo/... 各表 admin/app/agent）与自定义 owner-scoped service
不兼容，已剪除；这里只挂手写的合并版（app/plan.py + agent/plan.py）。
"""

from fastapi import APIRouter

from backend.app.hasn_plan.api.v1.agent.plan import router as agent_plan_router
from backend.app.hasn_plan.api.v1.app.plan import router as app_plan_router
from backend.core.conf import settings

v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/plan')

# Agent 端：/api/v1/plan/agent/*（Agent JWT，分身经 hasn-mcp 管理主人规划数据）
v1.include_router(agent_plan_router, prefix='/agent', tags=['规划与目标管理（Agent 端）'])
# Owner/WebUI 端：/api/v1/plan/app/*（Owner JWT，daemon webui-facing 回源通道）
v1.include_router(app_plan_router, prefix='/app', tags=['规划与目标管理（Owner 端）'])
