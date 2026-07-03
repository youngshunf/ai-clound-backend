from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from backend.app.hasn.schema.hasn_platform_operator_grants import (
    BatchCreateHasnPlatformOperatorGrantsParam,
    CreateHasnPlatformOperatorGrantsParam,
    DeleteHasnPlatformOperatorGrantsParam,
    GetHasnPlatformOperatorGrantsDetail,
    OperatorGrantAgentOption,
    OperatorGrantOwnerOption,
    OperatorGrantScopeOption,
    UpdateHasnPlatformOperatorGrantsParam,
)
from backend.app.hasn.service.hasn_platform_operator_grants_service import hasn_platform_operator_grants_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


def _current_admin_id(request: Request) -> str:
    """当前登录 Admin 的审计标识（用户名优先，回落 id）——granted_by 由此填，前端不可伪造。"""
    user = request.user
    return str(getattr(user, 'username', None) or getattr(user, 'id', '') or '') or 'unknown'


@router.get('/options/owners', summary='授予对象·用户下拉（Admin-only）', dependencies=[DependsJwtAuth])
async def get_operator_grant_owner_options(
    db: CurrentSession, keyword: Annotated[str | None, Query(description='按昵称/hasn_id 关键字收窄')] = None
) -> ResponseSchemaModel[list[OperatorGrantOwnerOption]]:
    data = await hasn_platform_operator_grants_service.list_owner_options(db=db, keyword=keyword)
    return response_base.success(data=data)


@router.get('/options/agents', summary='授予对象·某用户的分身下拉（Admin-only）', dependencies=[DependsJwtAuth])
async def get_operator_grant_agent_options(
    db: CurrentSession, owner_hasn_id: Annotated[str, Query(description='所属用户 hasn_id')]
) -> ResponseSchemaModel[list[OperatorGrantAgentOption]]:
    data = await hasn_platform_operator_grants_service.list_agent_options(db=db, owner_hasn_id=owner_hasn_id)
    return response_base.success(data=data)


@router.get(
    '/options/scopes',
    summary='特权 scope 目录·下拉（声明驱动·只读·Admin-only）',
    dependencies=[DependsJwtAuth],
)
async def get_operator_grant_scope_options() -> ResponseSchemaModel[list[OperatorGrantScopeOption]]:
    data = hasn_platform_operator_grants_service.list_scope_options()
    return response_base.success(data=data)


@router.get('/{pk}', summary='获取平台运维授予源（Admin-only·G1 特权门）详情', dependencies=[DependsJwtAuth], name='hasn_admin_get_hasn_platform_operator_grants')
async def get_hasn_platform_operator_grants(
    db: CurrentSession, pk: Annotated[int, Path(description='平台运维授予源（Admin-only·G1 特权门） ID')]
) -> ResponseSchemaModel[GetHasnPlatformOperatorGrantsDetail]:
    hasn_platform_operator_grants = await hasn_platform_operator_grants_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_platform_operator_grants)


@router.get(
    '',
    summary='分页获取所有平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_admin_get_hasn_platform_operator_grants_paginated',
)
async def get_hasn_platform_operator_grants_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnPlatformOperatorGrantsDetail]]:
    page_data = await hasn_platform_operator_grants_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:add')),
        DependsRBAC,
    ],
    name='hasn_admin_create_hasn_platform_operator_grants',
)
async def create_hasn_platform_operator_grants(
    request: Request, db: CurrentSessionTransaction, obj: CreateHasnPlatformOperatorGrantsParam
) -> ResponseModel:
    # granted_by 由后端从当前登录 Admin 覆盖（审计不可伪造），忽略前端传入
    obj = obj.model_copy(update={'granted_by': _current_admin_id(request)})
    await hasn_platform_operator_grants_service.create(db=db, obj=obj)
    return response_base.success()


@router.post(
    '/batch',
    summary='批量授予平台运维特权（一次给同一分身多选 scope·Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:add')),
        DependsRBAC,
    ],
    name='hasn_admin_batch_create_hasn_platform_operator_grants',
)
async def batch_create_hasn_platform_operator_grants(
    request: Request, db: CurrentSessionTransaction, obj: BatchCreateHasnPlatformOperatorGrantsParam
) -> ResponseModel:
    # granted_by 由后端从当前登录 Admin 覆盖（审计不可伪造），前端不传
    created = await hasn_platform_operator_grants_service.create_batch(
        db=db,
        agent_hasn_id=obj.agent_hasn_id,
        scopes=obj.scopes,
        granted_by=_current_admin_id(request),
        note=obj.note,
    )
    return response_base.success(data={'created': created})


@router.put(
    '/{pk}',
    summary='更新平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:edit')),
        DependsRBAC,
    ],
    name='hasn_admin_update_hasn_platform_operator_grants',
)
async def update_hasn_platform_operator_grants(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='平台运维授予源（Admin-only·G1 特权门） ID')], obj: UpdateHasnPlatformOperatorGrantsParam
) -> ResponseModel:
    count = await hasn_platform_operator_grants_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除平台运维授予源（Admin-only·G1 特权门）',
    dependencies=[
        Depends(RequestPermission('hasn:platform:operator:grants:del')),
        DependsRBAC,
    ],
    name='hasn_admin_delete_hasn_platform_operator_grants',
)
async def delete_hasn_platform_operator_grants(db: CurrentSessionTransaction, obj: DeleteHasnPlatformOperatorGrantsParam) -> ResponseModel:
    count = await hasn_platform_operator_grants_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
