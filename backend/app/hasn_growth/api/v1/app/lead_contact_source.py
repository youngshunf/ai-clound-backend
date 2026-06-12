"""Lead multi-source evidence - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_contact_source import (
    CreateLeadContactSourceParam,
    GetLeadContactSourceDetail,
    UpdateLeadContactSourceParam,
)
from backend.app.hasn_growth.service.lead_contact_source_service import lead_contact_source_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Lead multi-source evidence列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_contact_sources(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadContactSourceDetail]]:
    page_data = await lead_contact_source_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead multi-source evidence',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_contact_source(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadContactSourceParam,
) -> ResponseModel:
    result = await lead_contact_source_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Lead multi-source evidence详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_contact_source(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')],
) -> ResponseSchemaModel[GetLeadContactSourceDetail]:
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    if lead_contact_source.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Lead multi-source evidence')
    return response_base.success(data=lead_contact_source)


@router.put(
    '/{pk}',
    summary='更新Lead multi-source evidence',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_contact_source(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')],
    obj: UpdateLeadContactSourceParam,
) -> ResponseModel:
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    if getattr(lead_contact_source, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Lead multi-source evidence')
    count = await lead_contact_source_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Lead multi-source evidence',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_contact_source(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Lead multi-source evidence ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    if lead_contact_source.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Lead multi-source evidence')
    from backend.app.hasn_growth.schema.lead_contact_source import DeleteLeadContactSourceParam
    count = await lead_contact_source_service.delete(db=db, obj=DeleteLeadContactSourceParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
