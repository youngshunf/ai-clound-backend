from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_humans import (
    CreateHasnHumansParam,
    DeleteHasnHumansParam,
    GetHasnHumansDetail,
    UpdateHasnHumansParam,
)
from backend.app.hasn.service.hasn_humans_service import hasn_humans_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 人类用户身份详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_humans')
async def get_hasn_humans(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 人类用户身份 ID')]
) -> ResponseSchemaModel[GetHasnHumansDetail]:
    hasn_humans = await hasn_humans_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_humans)


@router.get(
    '',
    summary='分页获取所有HASN 人类用户身份',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_humanss_paginated')
async def get_hasn_humanss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnHumansDetail]]:
    page_data = await hasn_humans_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 人类用户身份',
    dependencies=[
        Depends(RequestPermission('hasn:humans:add')),
        DependsRBAC,
    ],
)
async def create_hasn_humans(db: CurrentSessionTransaction, obj: CreateHasnHumansParam) -> ResponseModel:
    await hasn_humans_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 人类用户身份',
    dependencies=[
        Depends(RequestPermission('hasn:humans:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_humans(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 人类用户身份 ID')], obj: UpdateHasnHumansParam
) -> ResponseModel:
    count = await hasn_humans_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 人类用户身份',
    dependencies=[
        Depends(RequestPermission('hasn:humans:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_humanss(db: CurrentSessionTransaction, obj: DeleteHasnHumansParam) -> ResponseModel:
    count = await hasn_humans_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
