"""hasn_hosting 应用路由聚合（无头 hasn-node 托管）。

三面（契约 §3），前缀刻意挂在既有 `hasn` 命名空间下，让 daemon/客户端只认一套 `/api/v1/hasn/*`：

- app      Owner 用户端（Owner JWT）        → `/api/v1/hasn/app/cloud-nodes`
- node     节点面（授权码 / 设备 token）     → `/api/v1/hasn/node/cloud`
- internal 内部面（Bearer hosting 服务令牌）→ `/api/v1/hasn/internal/cloud-nodes`

**刻意不挂 codegen 的 admin/agent/open 裸 CRUD**：授权码表是凭据表，任何形式的通用 CRUD
暴露都是越权大洞；托管状态的写入只经内部面的收敛端点。故那批生成文件已整体移除。
"""

from fastapi import APIRouter

from backend.app.hasn_hosting.api.v1.app.cloud_nodes import router as app_cloud_nodes_router
from backend.app.hasn_hosting.api.v1.internal.cloud_nodes import router as internal_cloud_nodes_router
from backend.app.hasn_hosting.api.v1.node.cloud import router as node_cloud_router
from backend.core.conf import settings

_BASE = f'{settings.FASTAPI_API_V1_PATH}/hasn'

# 注：列表/创建端点的路径是空串（`/api/v1/hasn/app/cloud-nodes` 本身），
# 故末段前缀必须落在 include_router 上——FastAPI 禁止 prefix 与 path 同时为空。
app = APIRouter(prefix=f'{_BASE}/app', tags=['云端节点托管-用户端'])
app.include_router(app_cloud_nodes_router, prefix='/cloud-nodes')

node = APIRouter(prefix=f'{_BASE}/node/cloud', tags=['云端节点托管-节点面'])
node.include_router(node_cloud_router)

internal = APIRouter(prefix=f'{_BASE}/internal/cloud-nodes', tags=['云端节点托管-内部服务'])
internal.include_router(internal_cloud_nodes_router)
