from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn.schema.hasn_app_entitlement import (
    AdminGrantEntitlementParam,
    CreateHasnAppEntitlementParam,
    DeleteHasnAppEntitlementParam,
    GetHasnAppEntitlementDetail,
    UpdateHasnAppEntitlementParam,
)
from backend.app.hasn.service import app_catalog_service
from backend.app.hasn.service.hasn_app_entitlement_service import hasn_app_entitlement_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取AI-Native 应用权益（云端权威）详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_app_entitlement')
async def get_hasn_app_entitlement(
    db: CurrentSession, pk: Annotated[int, Path(description='AI-Native 应用权益（云端权威） ID')]
) -> ResponseSchemaModel[GetHasnAppEntitlementDetail]:
    hasn_app_entitlement = await hasn_app_entitlement_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_app_entitlement)


@router.get(
    '',
    summary='分页获取所有AI-Native 应用权益（云端权威）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_app_entitlement_paginated',
)
async def get_hasn_app_entitlement_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnAppEntitlementDetail]]:
    page_data = await hasn_app_entitlement_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI-Native 应用权益（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:entitlement:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_app_entitlement',
)
async def create_hasn_app_entitlement(db: CurrentSessionTransaction, obj: CreateHasnAppEntitlementParam) -> ResponseModel:
    await hasn_app_entitlement_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新AI-Native 应用权益（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:entitlement:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_app_entitlement',
)
async def update_hasn_app_entitlement(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='AI-Native 应用权益（云端权威） ID')], obj: UpdateHasnAppEntitlementParam
) -> ResponseModel:
    count = await hasn_app_entitlement_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除AI-Native 应用权益（云端权威）',
    dependencies=[
        Depends(RequestPermission('hasn:app:entitlement:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_app_entitlement',
)
async def delete_hasn_app_entitlement(db: CurrentSessionTransaction, obj: DeleteHasnAppEntitlementParam) -> ResponseModel:
    count = await hasn_app_entitlement_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


# ============================ C5：语义化授予 / 撤销（对齐 resolve_app_access 准入语义） ============================


@router.post(
    '/grant',
    summary='管理员授予应用权益（幂等，source=admin_grant，对齐 active 唯一约束）',
    dependencies=[
        Depends(RequestPermission('hasn:app:entitlement:add')),
        DependsRBAC,
    ],
    name='admin_grant_app_entitlement',
)
async def grant_app_entitlement(
    db: CurrentSessionTransaction, obj: AdminGrantEntitlementParam
) -> ResponseSchemaModel[GetHasnAppEntitlementDetail]:
    ent = await app_catalog_service.grant_entitlement(
        db,
        app_id=obj.app_id,
        subject_type=obj.subject_type,
        subject_id=obj.subject_id,
        source='admin_grant',
        expires_at=obj.expires_at,
    )
    return response_base.success(data=GetHasnAppEntitlementDetail.model_validate(ent))


@router.post(
    '/{pk}/revoke',
    summary='管理员撤销应用权益（软撤销 status=revoked，非物理删除）',
    dependencies=[
        Depends(RequestPermission('hasn:app:entitlement:edit')),
        DependsRBAC,
    ],
    name='admin_revoke_app_entitlement',
)
async def revoke_app_entitlement(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='权益 ID')]
) -> ResponseModel:
    ok = await app_catalog_service.revoke_entitlement(db, entitlement_id=pk)
    if not ok:
        raise errors.RequestError(msg='权益不存在或非 active 状态')
    return response_base.success()
