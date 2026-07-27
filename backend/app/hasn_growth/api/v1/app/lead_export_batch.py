"""Lead CSV export batch - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_export_batch import (
    CreateLeadExportBatchParam,
    GetLeadExportBatchDetail,
    UpdateLeadExportBatchParam,
)
from backend.app.hasn_growth.service.lead_export_batch_service import lead_export_batch_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Lead CSV export batch列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_export_batchs(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadExportBatchDetail]]:
    page_data = await lead_export_batch_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead CSV export batch',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_export_batch(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadExportBatchParam,
) -> ResponseModel:
    await lead_export_batch_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取Lead CSV export batch详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_export_batch(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')],
) -> ResponseSchemaModel[GetLeadExportBatchDetail]:
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    if lead_export_batch.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Lead CSV export batch')
    return response_base.success(data=lead_export_batch)


@router.put(
    '/{pk}',
    summary='更新Lead CSV export batch',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_export_batch(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')],
    obj: UpdateLeadExportBatchParam,
) -> ResponseModel:
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    if getattr(lead_export_batch, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Lead CSV export batch')
    count = await lead_export_batch_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead CSV export batch',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_export_batch(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead CSV export batch ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_export_batch = await lead_export_batch_service.get(db=db, pk=pk)
    if lead_export_batch.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead CSV export batch')
    from backend.app.hasn_growth.schema.lead_export_batch import DeleteLeadExportBatchParam
    count = await lead_export_batch_service.delete(db=db, obj=DeleteLeadExportBatchParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
