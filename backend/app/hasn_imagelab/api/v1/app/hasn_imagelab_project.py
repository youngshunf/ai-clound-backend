"""历史图坊本地引用的兼容登记 - 用户端（Owner JWT）业务 API。

当前图坊以平台项目 UUID 为根，不再创建应用级项目实体；本端点仅供旧客户端把历史
`imagelab_projects.local_ref` 换成兼容 server_id，避免旧深链把本地 ULID 放进 URI/卡片。

契约（务必与 daemon 匹配）：
- POST /api/v1/hasn_imagelab/app/projects
- 请求体：{ "name": <历史显示名>, "local_ref": <daemon 历史本地引用> }
- 响应：统一信封 ResponseModel，data 含历史兼容 server_id；旧 daemon 读
  response.get("id")，回退 server_id。
- 幂等：按 (owner_hasn_id, local_ref) upsert——同一 owner 同一 local_ref 重复登记返回同一 id。

身份恒取自 Owner JWT（request.user.id → owner_hasn_id，行级隔离）；无平台身份映射则拒。
"""

from fastapi import APIRouter, Request
from pydantic import Field

from backend.app.hasn_core.app_platform import resolve_owner_hasn_id
from backend.app.hasn_imagelab.service.hasn_imagelab_project_service import hasn_imagelab_project_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.schema import SchemaBase
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class RegisterProjectParam(SchemaBase):
    """历史本地引用兼容登记入参。"""

    name: str = Field(default='', description='历史显示名（供旧卡片展示）')
    local_ref: str = Field(min_length=1, description='daemon 历史本地引用')


async def _owner(db: CurrentSession | CurrentSessionTransaction, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法登记历史图坊引用')
    return owner_hasn_id


@router.post('/projects', summary='[Owner] 历史图坊本地引用兼容登记（幂等 upsert）', dependencies=[DependsJwtAuth])
async def register_imagelab_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: RegisterProjectParam,
) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    server_id = await hasn_imagelab_project_service.register_project(
        db=db,
        owner_id=owner_hasn_id,
        local_ref=obj.local_ref.strip(),
        name=obj.name.strip(),
    )
    # data.id 保留历史兼容 server_id；旧 daemon 读 response.get("id")。
    return response_base.success(data={'id': server_id, 'server_id': server_id})
