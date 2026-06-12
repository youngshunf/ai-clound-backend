"""AI lead automation source configuration - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_source_config import (
    CreateLeadSourceConfigParam,
    GetLeadSourceConfigDetail,
    UpdateLeadSourceConfigParam,
)
from backend.app.hasn_growth.service.lead_source_config_service import lead_source_config_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的AI lead automation source configuration列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_source_configs(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadSourceConfigDetail]]:
    page_data = await lead_source_config_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI lead automation source configuration',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_source_config(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadSourceConfigParam,
) -> ResponseModel:
    result = await lead_source_config_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取AI lead automation source configuration详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_source_config(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')],
) -> ResponseSchemaModel[GetLeadSourceConfigDetail]:
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    if lead_source_config.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该AI lead automation source configuration')
    return response_base.success(data=lead_source_config)


@router.put(
    '/{pk}',
    summary='更新AI lead automation source configuration',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_source_config(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')],
    obj: UpdateLeadSourceConfigParam,
) -> ResponseModel:
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    if getattr(lead_source_config, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该AI lead automation source configuration')
    count = await lead_source_config_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除AI lead automation source configuration',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_source_config(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI lead automation source configuration ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    if lead_source_config.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该AI lead automation source configuration')
    from backend.app.hasn_growth.schema.lead_source_config import DeleteLeadSourceConfigParam
    count = await lead_source_config_service.delete(db=db, obj=DeleteLeadSourceConfigParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
