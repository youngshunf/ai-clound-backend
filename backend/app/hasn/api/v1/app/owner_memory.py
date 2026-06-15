"""[ADR-15 收编兼容 shim] Owner 记忆用户端路由已迁入 `app/hasn_memory.api.v1.app.owner_memory`。

URL 前缀 `/api/v1/hasn/app/owner/memory*` 保持不变（仍由 `app/hasn/api/router.py` 在
hasn `app` 路由内 prefix `/owner` 挂载本 `router`，daemon 依赖）；实现已迁入 `app/hasn_memory`。
本文件 re-export `router` 保持注册点不变。
"""

from backend.app.hasn_memory.api.v1.app.owner_memory import router as router
