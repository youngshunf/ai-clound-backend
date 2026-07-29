"""图坊项目云端权威 ID 登记（IMG-P3-cloud）- 用户端（Owner JWT）业务 API。

daemon 侧 `ensure_cloud_project_registered`（apps/daemon/src/domains/imagelab/dispatch.rs）
经 owner 代理 `imagelab_cloud`（base `/api/v1/hasn_imagelab/app`）POST `projects` 到本面，
拿云端权威 ID（server_id）回填 `imagelab_projects.server_id`，深链据此
（hasn://imagelab/projects/{server_id}）——本地 ULID 永不进 URI/卡片（CLAUDE.md 铁律）。

契约（务必与 daemon 匹配）：
- POST /api/v1/hasn_imagelab/app/projects
- 请求体：{ "name": <项目名>, "local_ref": <daemon 本地项目 ULID> }
- 响应：统一信封 ResponseModel，data 含 id（string，= 云端权威 server_id）；daemon 读
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
    """项目云端登记入参（对齐 daemon ensure_cloud_project_registered POST body）。"""

    name: str = Field(default='', description='项目名（供派发/完成卡片展示）')
    local_ref: str = Field(min_length=1, description='daemon 本地项目 ULID（本地权威 ID）')


async def _owner(db: CurrentSession | CurrentSessionTransaction, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法登记图坊项目')
    return owner_hasn_id


@router.post('/projects', summary='[Owner] 图坊项目云端权威 ID 登记（幂等 upsert）', dependencies=[DependsJwtAuth])
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
    # data.id 即云端权威 server_id（string）；daemon 读 response.get("id")。
    return response_base.success(data={'id': server_id, 'server_id': server_id})
