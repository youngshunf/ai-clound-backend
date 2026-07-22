"""平台项目里程碑（doc38 §12.3）用户端 API —— owner 隔离（经父项目归属校验）。

路由前缀: /api/v1/project/app/milestones（router.py 把本 router include 到 /milestones）。
认证: Owner JWT；owner 由 ``request.user.id`` 解析。里程碑经 ``ProjectService`` 落库，跨 owner
由父项目归属校验兜死（跨 owner → 404）。

注意（路径归属）：里程碑的 **create** 是 ``POST /projects/{pk}/milestones``（路径以 /projects 开头，
放在 hasn_project.py 那个 include 到 /projects 的 router 里）；本文件仅承载以 /milestones 开头的
**update**（``PUT /milestones/{id}``）与 **complete**（``POST /milestones/{id}/complete``）。

注意（codegen 修正）：本文件原是 codegen 样板（int pk / user_id / 泛型 service），已整体改写为
ProjectService 支撑；codegen 生成的 admin/agent/open 面继续用泛型 service，互不影响。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Request

from backend.app.hasn_project.api.v1.app._common import bump_project_sync, resolve_owner
from backend.app.hasn_project.service.project_app_service import project_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


@router.put('/{milestone_id}', summary='更新里程碑', dependencies=[DependsJwtAuth], name='project_app_update_milestone')
async def app_update_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    milestone_id: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """改里程碑（name/due_time/status/artifact_ref/sort 局部更新；经父项目校验 owner）。"""
    owner = await resolve_owner(db, request)
    data = await project_service.update_milestone(db, owner=owner, milestone_id=milestone_id, data=body)
    await bump_project_sync(db, owner)
    return response_base.success(data=data)


@router.post(
    '/{milestone_id}/complete',
    summary='完成里程碑',
    dependencies=[DependsJwtAuth],
    name='project_app_complete_milestone',
)
async def app_complete_milestone(
    request: Request, db: CurrentSessionTransaction, milestone_id: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    """完成里程碑（status→done）。纯业务态标记，不触发任何门控/依赖检查。"""
    owner = await resolve_owner(db, request)
    data = await project_service.complete_milestone(db, owner=owner, milestone_id=milestone_id)
    await bump_project_sync(db, owner)
    return response_base.success(data=data)
