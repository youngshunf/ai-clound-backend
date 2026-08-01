"""图坊（imagelab）API 路由聚合。

仅挂载 owner 业务面（对齐 hasn_reel/designsystem：codegen 裸面刻意不挂载）：
- 用户端（`app`，Owner JWT）：历史客户端的图坊本地引用兼容登记。当前流程直接使用平台项目 UUID；
  旧客户端仍可经 POST /api/v1/hasn_imagelab/app/projects 换取兼容 server_id。

刻意不挂载 admin/agent/open 裸 CRUD：图坊业务数据在 daemon 本地 SQLite（本地权威），
云端兼容表不承担应用级项目语义；分身走本地 hasn-mcp（图坊引擎本地 sidecar），不经云端 REST。
"""

from fastapi import APIRouter

from backend.app.hasn_imagelab.api.v1.app.hasn_imagelab_project import router as app_imagelab_project_router
from backend.core.conf import settings

# ========================================
# 用户端 API（Owner JWT，owner 行级隔离）  前缀: /api/v1/hasn_imagelab/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_imagelab/app', tags=['图坊-用户端'])

app.include_router(app_imagelab_project_router)
