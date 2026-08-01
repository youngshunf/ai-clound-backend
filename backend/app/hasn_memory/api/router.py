"""记忆模块（hasn_memory）路由出口。

ADR-15 收编：owner_memory 用户端接口实现迁入本模块，但 **URL 前缀保持不变**
（`/api/v1/hasn/app/owner/memory*`，daemon 依赖）——仍由 `app/hasn/api/router.py`
在 hasn `app` 路由内（prefix `/owner`）挂载 `app_owner_memory_router`，本文件仅做实现出口。

Agent 侧记忆工具（contribute/get）继续在 `app/hasn/api/v1/agent/hasn_agent_profile.py`
（Agent JWT），service 经本模块复用。

主人画像完整度（owner_profile_coverage，「了解主人」功能）：owner 读端点同样由
`app/hasn/api/router.py` 在 `/owner` 前缀下挂载 `app_owner_profile_coverage_router`，
URL `/api/v1/hasn/app/owner/profile-coverage`（daemon 代理）。Agent 侧读走 MCP 平台工具
`hasn.owner.coverage.get`（非 REST scope CRUD，故不生成 admin/agent/open scope 路由）。

doc19 S6 新增两个前缀（同样在 `app/hasn/api/router.py` 挂载，本文件只做实现出口）：

- Agent scope 合并闸 `/api/v1/hasn/memory/agent/merge/{apply,request}`（Agent JWT 自识别）；
- App scope 合并可见性 `/api/v1/hasn/app/memory/merge/status`（Owner JWT，主人记忆页数据源）。
"""

from backend.app.hasn_memory.api.v1.agent.merge_gate import router as agent_merge_gate_router
from backend.app.hasn_memory.api.v1.app.merge_status import router as app_merge_status_router
from backend.app.hasn_memory.api.v1.app.owner_memory import router as app_owner_memory_router
from backend.app.hasn_memory.api.v1.app.owner_profile_coverage import router as app_owner_profile_coverage_router

__all__ = [
    'agent_merge_gate_router',
    'app_merge_status_router',
    'app_owner_memory_router',
    'app_owner_profile_coverage_router',
]
