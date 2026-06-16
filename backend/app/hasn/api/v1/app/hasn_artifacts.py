"""分身产物 - 用户端（Owner）API（Owner JWT，数据隔离按 owner_hasn_id）。

路由前缀: /api/v1/artifacts/app
认证方式: Owner JWT（见 common.security.jwt.DependsJwtAuth）；owner 身份由 JWT → hasn_humans 解析。

WebUI 永远只调 daemon，daemon 代理本组（设计 §6.2）。产物展示/下载一律经 resolve_assets 签名。
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, Path, Query, Request

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_artifacts import ArtifactDetail, ArtifactListData
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
    kind: Annotated[str | None, Query(description='按产物类型筛选')] = None,
) -> ResponseSchemaModel[ArtifactListData]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    items, total = await hasn_artifacts_service.list_by_agent(
        db, owner_hasn_id=owner_hasn_id, agent_hasn_id=agent_hasn_id, page=page, size=size, kind=kind
    )
    return response_base.success(data=ArtifactListData(items=items, total=total, page=page, size=size))


@router.get('/artifacts', summary='owner 全部分身产物聚合时间线', dependencies=[DependsJwtAuth])
async def list_owner_artifacts(
    request: Request,
    db: CurrentSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    kind: Annotated[str | None, Query(description='按产物类型筛选')] = None,
) -> ResponseSchemaModel[ArtifactListData]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    items, total = await hasn_artifacts_service.list_for_owner(
        db, owner_hasn_id=owner_hasn_id, page=page, size=size, kind=kind
    )
    return response_base.success(data=ArtifactListData(items=items, total=total, page=page, size=size))


@router.get('/artifacts/{artifact_id}', summary='产物详情（含签名 URL）', dependencies=[DependsJwtAuth])
async def get_artifact_detail(
    request: Request,
    db: CurrentSession,
    artifact_id: Annotated[str, Path(description='产物 ID')],
) -> ResponseSchemaModel[ArtifactDetail]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    detail = await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner_hasn_id, artifact_id=artifact_id)
    return response_base.success(data=detail)


@router.delete('/artifacts/{artifact_id}', summary='软删产物指针（不删 asset 本体）', dependencies=[DependsJwtAuth])
async def delete_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    artifact_id: Annotated[str, Path(description='产物 ID')],
) -> ResponseModel:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    await hasn_artifacts_service.soft_delete(db, owner_hasn_id=owner_hasn_id, artifact_id=artifact_id)
    return response_base.success()
