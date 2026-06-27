"""OpenPencil design 项目分享协作（owner 端，doc27 §P3-C / OP-P3-9）。

design 是 daemon 本地优先权威（云端只有轻登记表 hasn_design_project），**没有云端 design_service**。
分享(A) 全复用既有泛型 `resource_share`（resource_type 开放字符串 → 'design' 直接通行）。

与 studio 项目分享的区别：studio 项目是云端权威行（studio_project，bigint PK，service 内做 owner 归属校验）；
**design 项目是 daemon 权威（project_id = ULID 字符串，云端不持有项目行）→ owner JWT 即权威**，
本路由不做云端行归属校验，只把 owner 请求的 ACL 落进泛型 resource_share（resource_owner_hasn_id = 当前 owner）。

WebUI 经 daemon `/api/v1/design/projects/{id}/share*` 薄代理调用本面（铁律：WebUI 不直连云端）。
一律返回统一信封（ResponseModel + response_base.success）。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.hasn.service.app_catalog_service import resolve_owner_hasn_id
from backend.app.hasn.service.resource_share_service import ResourceShareService
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

RESOURCE_TYPE_DESIGN = 'design'


async def _owner(db: CurrentSession | CurrentSessionTransaction, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法分享设计项目')
    return owner_hasn_id


class AddShareRequest(BaseModel):
    grantee_type: str = Field(description='human/agent/enterprise')
    grantee_id: str = Field(min_length=1, description='被授权对象 ID（人/分身 hasn_id 或企业 id）')
    permission: str = Field(description='viewer/editor/manager')


@router.get('/projects/{project_id}/shares', summary='[Owner] 查看设计项目协作名单', dependencies=[DependsJwtAuth])
async def list_project_shares(request: Request, db: CurrentSession, project_id: str) -> ResponseModel:
    await _owner(db, request)
    data = await ResourceShareService.list_shares(db, resource_type=RESOURCE_TYPE_DESIGN, resource_id=project_id)
    return response_base.success(data=data)


@router.post('/projects/{project_id}/shares', summary='[Owner] 添加/更新设计项目协作者', dependencies=[DependsJwtAuth])
async def add_project_share(
    request: Request, db: CurrentSessionTransaction, project_id: str, body: AddShareRequest
) -> ResponseModel:
    owner = await _owner(db, request)
    data = await ResourceShareService.upsert_share(
        db,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        owner_hasn_id=owner,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
        permission=body.permission,
        granted_by=owner,
    )
    return response_base.success(data=data)


@router.delete('/projects/{project_id}/shares', summary='[Owner] 撤销设计项目协作者', dependencies=[DependsJwtAuth])
async def revoke_project_share(
    request: Request, db: CurrentSessionTransaction, project_id: str, grantee_type: str, grantee_id: str
) -> ResponseModel:
    await _owner(db, request)
    ok = await ResourceShareService.revoke_share(
        db,
        resource_type=RESOURCE_TYPE_DESIGN,
        resource_id=project_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
    )
    return response_base.success(data={'revoked': ok})
