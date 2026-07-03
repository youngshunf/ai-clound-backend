from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_sessions import (
    CreateHasnSessionsParam,
    DeleteHasnSessionsParam,
    GetHasnSessionsDetail,
    UpdateHasnSessionsParam,
)
from backend.app.hasn.service.hasn_sessions_service import hasn_sessions_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 会话分层 - 逻辑会话详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_sessions')
async def get_hasn_sessions(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 会话分层 - 逻辑会话 ID')]
) -> ResponseSchemaModel[GetHasnSessionsDetail]:
    hasn_sessions = await hasn_sessions_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_sessions)


@router.get(
    '',
    summary='分页获取所有HASN 会话分层 - 逻辑会话',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_sessions_paginated',
)
async def get_hasn_sessions_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnSessionsDetail]]:
    page_data = await hasn_sessions_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 会话分层 - 逻辑会话',
    dependencies=[
        Depends(RequestPermission('hasn:sessions:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_sessions',
)
async def create_hasn_sessions(db: CurrentSessionTransaction, obj: CreateHasnSessionsParam) -> ResponseModel:
    await hasn_sessions_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 会话分层 - 逻辑会话',
    dependencies=[
        Depends(RequestPermission('hasn:sessions:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_sessions',
)
async def update_hasn_sessions(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 会话分层 - 逻辑会话 ID')], obj: UpdateHasnSessionsParam
) -> ResponseModel:
    count = await hasn_sessions_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 会话分层 - 逻辑会话',
    dependencies=[
        Depends(RequestPermission('hasn:sessions:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_sessions',
)
async def delete_hasn_sessions(db: CurrentSessionTransaction, obj: DeleteHasnSessionsParam) -> ResponseModel:
    count = await hasn_sessions_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
