"""Valid deduplicated lead contact - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_contact import (
    CreateLeadContactParam,
    GetLeadContactDetail,
    UpdateLeadContactParam,
)
from backend.app.hasn_growth.service.lead_contact_service import lead_contact_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Valid deduplicated lead contact列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_contacts(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadContactDetail]]:
    page_data = await lead_contact_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Valid deduplicated lead contact',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_contact(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadContactParam,
) -> ResponseModel:
    result = await lead_contact_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Valid deduplicated lead contact详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_contact(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Valid deduplicated lead contact ID')],
) -> ResponseSchemaModel[GetLeadContactDetail]:
    lead_contact = await lead_contact_service.get(db=db, pk=pk)
    if lead_contact.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Valid deduplicated lead contact')
    return response_base.success(data=lead_contact)


@router.put(
    '/{pk}',
    summary='更新Valid deduplicated lead contact',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_contact(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Valid deduplicated lead contact ID')],
    obj: UpdateLeadContactParam,
) -> ResponseModel:
    lead_contact = await lead_contact_service.get(db=db, pk=pk)
    if getattr(lead_contact, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Valid deduplicated lead contact')
    count = await lead_contact_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Valid deduplicated lead contact',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_contact(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Valid deduplicated lead contact ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_contact = await lead_contact_service.get(db=db, pk=pk)
    if lead_contact.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Valid deduplicated lead contact')
    from backend.app.hasn_growth.schema.lead_contact import DeleteLeadContactParam
    count = await lead_contact_service.delete(db=db, obj=DeleteLeadContactParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
