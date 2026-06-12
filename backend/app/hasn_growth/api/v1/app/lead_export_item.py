"""Lead CSV export item snapshot - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_export_item import (
    CreateLeadExportItemParam,
    GetLeadExportItemDetail,
    UpdateLeadExportItemParam,
)
from backend.app.hasn_growth.service.lead_export_item_service import lead_export_item_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Lead CSV export item snapshot列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_export_items(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadExportItemDetail]]:
    page_data = await lead_export_item_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead CSV export item snapshot',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_export_item(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadExportItemParam,
) -> ResponseModel:
    result = await lead_export_item_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Lead CSV export item snapshot详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_export_item(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead CSV export item snapshot ID')],
) -> ResponseSchemaModel[GetLeadExportItemDetail]:
    lead_export_item = await lead_export_item_service.get(db=db, pk=pk)
    if lead_export_item.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Lead CSV export item snapshot')
    return response_base.success(data=lead_export_item)


@router.put(
    '/{pk}',
    summary='更新Lead CSV export item snapshot',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_export_item(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead CSV export item snapshot ID')],
    obj: UpdateLeadExportItemParam,
) -> ResponseModel:
    lead_export_item = await lead_export_item_service.get(db=db, pk=pk)
    if getattr(lead_export_item, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Lead CSV export item snapshot')
    count = await lead_export_item_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead CSV export item snapshot',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_export_item(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead CSV export item snapshot ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_export_item = await lead_export_item_service.get(db=db, pk=pk)
    if lead_export_item.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead CSV export item snapshot')
    from backend.app.hasn_growth.schema.lead_export_item import DeleteLeadExportItemParam
    count = await lead_export_item_service.delete(db=db, obj=DeleteLeadExportItemParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
