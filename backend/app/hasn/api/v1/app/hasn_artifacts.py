"""分身产物 - 用户端（Owner）API（Owner JWT，数据隔离按 owner_hasn_id）。

路由前缀: /api/v1/artifacts/app
认证方式: Owner JWT（见 common.security.jwt.DependsJwtAuth）；owner 身份由 JWT → hasn_humans 解析。

WebUI 永远只调 daemon，daemon 代理本组（设计 §6.2）。产物展示/下载一律经 resolve_assets 签名。
"""

from typing import Annotated, Literal, cast

import sqlalchemy as sa

from fastapi import APIRouter, Path, Query, Request

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.artifact_contract import (
    ArtifactKind,
    ArtifactListItem,
    ArtifactListPage,
    ArtifactSourceKind,
)
from backend.app.hasn.schema.hasn_artifacts import ArtifactDetail, UpdateArtifactContentParam
from backend.app.hasn.service.artifact_query_service import artifact_query_service
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _current_owner_hasn_id(db: CurrentSession, user_id: int) -> str:
    owner_hasn_id = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner_hasn_id:
        raise errors.NotFoundError(msg='当前用户尚未绑定 HASN 身份')
    return owner_hasn_id


@router.get(
    '/agents/{agent_hasn_id}/artifacts',
    summary='列某分身的产物时间线（校验归属本人）',
    dependencies=[DependsJwtAuth],
)
async def list_agent_artifacts(
    request: Request,
    db: CurrentSession,
    agent_hasn_id: Annotated[str, Path(description='分身 hasn_id')],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    kind: Annotated[ArtifactKind | None, Query(description='按产物类型筛选')] = None,
    work_session_id: Annotated[str | None, Query(description='按工作会话筛选')] = None,
    project_id: Annotated[str | None, Query(description='按项目筛选')] = None,
    origin_ref: Annotated[str | None, Query(description='按来源资源筛选')] = None,
    source_kind: Annotated[ArtifactSourceKind | None, Query(description='按来源类型筛选')] = None,
    source_app_id: Annotated[str | None, Query(description='按来源应用筛选')] = None,
    resource_kind: Annotated[str | None, Query(description='按资源类型筛选')] = None,
    keyword: Annotated[str | None, Query(description='按标题或摘要搜索')] = None,
    status: Annotated[Literal['active', 'deleted', 'missing'], Query(description='按当前状态筛选')] = 'active',
    cursor: Annotated[str | None, Query(description='keyset 游标（设计 02 §8.2/A16，daemon 聚合用；与 page 二选一）')] = None,
) -> ResponseSchemaModel[ArtifactListPage]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    result = await artifact_query_service.list(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=agent_hasn_id,
        page=page,
        size=size,
        artifact_kind=kind,
        work_session_id=work_session_id,
        project_id=project_id,
        origin_ref=origin_ref,
        source_kind=source_kind,
        source_app_id=source_app_id,
        resource_kind=resource_kind,
        keyword=keyword,
        status=status,
        cursor=cursor,
    )
    return cast(ResponseSchemaModel[ArtifactListPage], response_base.success(data=result))


@router.get('/artifacts', summary='owner 全部分身产物聚合时间线', dependencies=[DependsJwtAuth])
async def list_owner_artifacts(
    request: Request,
    db: CurrentSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    kind: Annotated[ArtifactKind | None, Query(description='按产物类型筛选')] = None,
    agent_hasn_id: Annotated[str | None, Query(description='按分身筛选')] = None,
    work_session_id: Annotated[str | None, Query(description='按工作会话筛选')] = None,
    project_id: Annotated[str | None, Query(description='按项目筛选')] = None,
    origin_ref: Annotated[str | None, Query(description='按来源资源筛选')] = None,
    source_kind: Annotated[ArtifactSourceKind | None, Query(description='按来源类型筛选')] = None,
    source_app_id: Annotated[str | None, Query(description='按来源应用筛选')] = None,
    resource_kind: Annotated[str | None, Query(description='按资源类型筛选')] = None,
    keyword: Annotated[str | None, Query(description='按标题或摘要搜索')] = None,
    status: Annotated[Literal['active', 'deleted', 'missing'], Query(description='按当前状态筛选')] = 'active',
    cursor: Annotated[str | None, Query(description='keyset 游标（设计 02 §8.2/A16，daemon 聚合用；与 page 二选一）')] = None,
) -> ResponseSchemaModel[ArtifactListPage]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    result = await artifact_query_service.list(
        db,
        owner_hasn_id=owner_hasn_id,
        page=page,
        size=size,
        artifact_kind=kind,
        agent_hasn_id=agent_hasn_id,
        work_session_id=work_session_id,
        project_id=project_id,
        origin_ref=origin_ref,
        source_kind=source_kind,
        source_app_id=source_app_id,
        resource_kind=resource_kind,
        keyword=keyword,
        status=status,
        cursor=cursor,
    )
    return cast(ResponseSchemaModel[ArtifactListPage], response_base.success(data=result))


@router.get(
    '/artifacts/by-origin',
    summary='列某业务对象产出的产物（按 origin_ref 反查，规划详情产物轨）',
    dependencies=[DependsJwtAuth],
)
async def list_artifacts_by_origin(
    request: Request,
    db: CurrentSession,
    origin_ref: Annotated[str, Query(description='业务资源回指，如 resource:plan:todo:{id}')],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    agent_hasn_id: Annotated[str | None, Query(description='按分身筛选')] = None,
    work_session_id: Annotated[str | None, Query(description='按工作会话筛选')] = None,
    project_id: Annotated[str | None, Query(description='按项目筛选')] = None,
    kind: Annotated[ArtifactKind | None, Query(description='按产物类型筛选')] = None,
    source_kind: Annotated[ArtifactSourceKind | None, Query(description='按来源类型筛选')] = None,
    source_app_id: Annotated[str | None, Query(description='按来源应用筛选')] = None,
    resource_kind: Annotated[str | None, Query(description='按资源类型筛选')] = None,
    keyword: Annotated[str | None, Query(description='按标题或摘要搜索')] = None,
    status: Annotated[Literal['active', 'deleted', 'missing'], Query(description='按当前状态筛选')] = 'active',
    cursor: Annotated[str | None, Query(description='keyset 游标（设计 02 §8.2/A16，daemon 聚合用；与 page 二选一）')] = None,
) -> ResponseSchemaModel[ArtifactListPage]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    result = await artifact_query_service.list(
        db,
        owner_hasn_id=owner_hasn_id,
        origin_ref=origin_ref,
        agent_hasn_id=agent_hasn_id,
        work_session_id=work_session_id,
        project_id=project_id,
        page=page,
        size=size,
        artifact_kind=kind,
        source_kind=source_kind,
        source_app_id=source_app_id,
        resource_kind=resource_kind,
        keyword=keyword,
        status=status,
        cursor=cursor,
    )
    return cast(ResponseSchemaModel[ArtifactListPage], response_base.success(data=result))


@router.get(
    '/artifacts/by-session',
    summary='列某工作会话产出的产物（按 session_id 反查，工作会话页资源栏）',
    dependencies=[DependsJwtAuth],
)
async def list_artifacts_by_session(
    request: Request,
    db: CurrentSession,
    session_id: Annotated[str, Query(description='工作会话 id（hasn-node 本地工作会话 id）')],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    agent_hasn_id: Annotated[str | None, Query(description='按分身筛选')] = None,
    project_id: Annotated[str | None, Query(description='按项目筛选')] = None,
    origin_ref: Annotated[str | None, Query(description='按来源资源筛选')] = None,
    kind: Annotated[ArtifactKind | None, Query(description='按产物类型筛选')] = None,
    source_kind: Annotated[ArtifactSourceKind | None, Query(description='按来源类型筛选')] = None,
    source_app_id: Annotated[str | None, Query(description='按来源应用筛选')] = None,
    resource_kind: Annotated[str | None, Query(description='按资源类型筛选')] = None,
    keyword: Annotated[str | None, Query(description='按标题或摘要搜索')] = None,
    status: Annotated[Literal['active', 'deleted', 'missing'], Query(description='按当前状态筛选')] = 'active',
    cursor: Annotated[str | None, Query(description='keyset 游标（设计 02 §8.2/A16，daemon 聚合用；与 page 二选一）')] = None,
) -> ResponseSchemaModel[ArtifactListPage]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    result = await artifact_query_service.list(
        db,
        owner_hasn_id=owner_hasn_id,
        work_session_id=session_id,
        agent_hasn_id=agent_hasn_id,
        project_id=project_id,
        origin_ref=origin_ref,
        page=page,
        size=size,
        artifact_kind=kind,
        source_kind=source_kind,
        source_app_id=source_app_id,
        resource_kind=resource_kind,
        keyword=keyword,
        status=status,
        cursor=cursor,
    )
    return cast(ResponseSchemaModel[ArtifactListPage], response_base.success(data=result))


@router.get('/artifacts/{artifact_id}', summary='产物详情（含签名 URL）', dependencies=[DependsJwtAuth])
async def get_artifact_detail(
    request: Request,
    db: CurrentSession,
    artifact_id: Annotated[str, Path(description='产物 ID')],
) -> ResponseSchemaModel[ArtifactDetail]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    result = await hasn_artifacts_service.get_detail(
        db,
        owner_hasn_id=owner_hasn_id,
        artifact_id=artifact_id,
    )
    return cast(ResponseSchemaModel[ArtifactDetail], response_base.success(data=result))


@router.put(
    '/artifacts/{artifact_id}',
    summary='更新产物正文（markdown 编辑保存，只改 body/title）',
    dependencies=[DependsJwtAuth],
)
async def update_artifact_content(
    request: Request,
    db: CurrentSessionTransaction,
    artifact_id: Annotated[str, Path(description='产物 ID')],
    obj: UpdateArtifactContentParam,
) -> ResponseModel:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    await hasn_artifacts_service.update_content(
        db, owner_hasn_id=owner_hasn_id, artifact_id=artifact_id, body=obj.body, title=obj.title
    )
    return response_base.success()


@router.delete('/artifacts/{artifact_id}', summary='软删产物指针（不删 asset 本体）', dependencies=[DependsJwtAuth])
async def delete_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    artifact_id: Annotated[str, Path(description='产物 ID')],
) -> ResponseModel:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    await hasn_artifacts_service.soft_delete(db, owner_hasn_id=owner_hasn_id, artifact_id=artifact_id)
    return response_base.success()
