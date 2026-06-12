"""Rejected, invalid, duplicate, or failed lead record - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_rejected_record import (
    CreateLeadRejectedRecordParam,
    GetLeadRejectedRecordDetail,
    UpdateLeadRejectedRecordParam,
)
from backend.app.hasn_growth.service.lead_rejected_record_service import lead_rejected_record_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Rejected, invalid, duplicate, or failed lead record列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_rejected_records(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadRejectedRecordDetail]]:
    page_data = await lead_rejected_record_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Rejected, invalid, duplicate, or failed lead record',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_rejected_record(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadRejectedRecordParam,
) -> ResponseModel:
    result = await lead_rejected_record_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Rejected, invalid, duplicate, or failed lead record详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_rejected_record(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Rejected, invalid, duplicate, or failed lead record ID')],
) -> ResponseSchemaModel[GetLeadRejectedRecordDetail]:
    lead_rejected_record = await lead_rejected_record_service.get(db=db, pk=pk)
    if lead_rejected_record.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Rejected, invalid, duplicate, or failed lead record')
    return response_base.success(data=lead_rejected_record)


@router.put(
    '/{pk}',
    summary='更新Rejected, invalid, duplicate, or failed lead record',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_rejected_record(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Rejected, invalid, duplicate, or failed lead record ID')],
    obj: UpdateLeadRejectedRecordParam,
) -> ResponseModel:
    lead_rejected_record = await lead_rejected_record_service.get(db=db, pk=pk)
    if getattr(lead_rejected_record, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Rejected, invalid, duplicate, or failed lead record')
    count = await lead_rejected_record_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Rejected, invalid, duplicate, or failed lead record',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_rejected_record(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Rejected, invalid, duplicate, or failed lead record ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_rejected_record = await lead_rejected_record_service.get(db=db, pk=pk)
    if lead_rejected_record.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Rejected, invalid, duplicate, or failed lead record')
    from backend.app.hasn_growth.schema.lead_rejected_record import DeleteLeadRejectedRecordParam
    count = await lead_rejected_record_service.delete(db=db, obj=DeleteLeadRejectedRecordParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
