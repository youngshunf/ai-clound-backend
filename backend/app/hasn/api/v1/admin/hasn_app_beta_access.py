"""灰度内测访问 · 管理端（APPBETA-2）。

非标准 CRUD：管理员在此审批用户的内测申请（通过/拒绝）、主动邀请用户进内测、查看申请列表、
撤销访问。业务逻辑在 ``app_catalog_service``（与 catalog/entitlement 同域），本文件只做接口编排。
鉴权用 ``DependsJwtAuth``（管理端登录即可，对齐 catalog 列表端点）。
"""

import logging

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from backend.app.hasn.crud.crud_hasn_app_beta_access import hasn_app_beta_access_dao
from backend.app.hasn.schema.hasn_app_beta_access import (
    DecideHasnAppBetaParam,
    DeleteHasnAppBetaAccessParam,
    GetHasnAppBetaAccessDetail,
    InviteHasnAppBetaParam,
)
from backend.app.hasn.service import app_catalog_service
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

log = logging.getLogger(__name__)

router = APIRouter()


@router.get('', summary='列灰度内测访问/申请（可按 app_id / status 过滤）', dependencies=[DependsJwtAuth])
async def list_app_beta_access(
    db: CurrentSession,
    app_id: Annotated[str | None, Query(description='按应用过滤')] = None,
    status: Annotated[str | None, Query(description='按状态过滤 pending/approved/rejected')] = None,
) -> ResponseSchemaModel[list[GetHasnAppBetaAccessDetail]]:
    rows = await app_catalog_service.list_beta_access(db, app_id=app_id, status=status)
    return response_base.success(data=[GetHasnAppBetaAccessDetail.model_validate(r) for r in rows])


@router.post('/invite', summary='邀请某主体进灰度内测（直接通过，无需对方申请）', dependencies=[DependsJwtAuth])
async def invite_app_beta_access(
    request: Request, db: CurrentSessionTransaction, obj: InviteHasnAppBetaParam
) -> ResponseSchemaModel[GetHasnAppBetaAccessDetail]:
    row = await app_catalog_service.invite_beta(
        db,
        app_id=obj.app_id,
        subject_id=obj.subject_id,
        subject_type=obj.subject_type,
        decided_by=str(request.user.id),
        note=obj.note,
    )
    return response_base.success(data=GetHasnAppBetaAccessDetail.model_validate(row))


@router.post('/{pk}/approve', summary='通过一条灰度内测申请', dependencies=[DependsJwtAuth])
async def approve_app_beta_access(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='灰度内测访问 ID')],
    obj: DecideHasnAppBetaParam | None = None,
) -> ResponseSchemaModel[GetHasnAppBetaAccessDetail]:
    row = await app_catalog_service.decide_beta(
        db, pk=pk, approve=True, decided_by=str(request.user.id), note=(obj.note if obj else None)
    )
    return response_base.success(data=GetHasnAppBetaAccessDetail.model_validate(row))


@router.post('/{pk}/reject', summary='拒绝一条灰度内测申请', dependencies=[DependsJwtAuth])
async def reject_app_beta_access(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='灰度内测访问 ID')],
    obj: DecideHasnAppBetaParam | None = None,
) -> ResponseSchemaModel[GetHasnAppBetaAccessDetail]:
    row = await app_catalog_service.decide_beta(
        db, pk=pk, approve=False, decided_by=str(request.user.id), note=(obj.note if obj else None)
    )
    return response_base.success(data=GetHasnAppBetaAccessDetail.model_validate(row))


@router.delete('', summary='撤销/清理灰度内测访问行（删除后用户可重新申请）', dependencies=[DependsJwtAuth])
async def delete_app_beta_access(db: CurrentSessionTransaction, obj: DeleteHasnAppBetaAccessParam) -> ResponseModel:
    count = await hasn_app_beta_access_dao.delete(db, obj.pks)
    return response_base.success() if count > 0 else response_base.fail()
