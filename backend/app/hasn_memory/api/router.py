"""记忆模块（hasn_memory）路由出口。

ADR-15 收编：owner_memory 用户端接口实现迁入本模块，但 **URL 前缀保持不变**
（`/api/v1/hasn/app/owner/memory*`，daemon 依赖）——仍由 `app/hasn/api/router.py`
在 hasn `app` 路由内（prefix `/owner`）挂载 `app_owner_memory_router`，本文件仅做实现出口。

Agent 侧记忆工具（contribute/get）继续在 `app/hasn/api/v1/agent/hasn_agent_profile.py`
（Agent JWT），service 经本模块复用。
"""

from backend.app.hasn_memory.api.v1.app.owner_memory import router as app_owner_memory_router

__all__ = ['app_owner_memory_router']
