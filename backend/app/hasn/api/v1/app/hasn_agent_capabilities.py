"""HASN Agent 能力声明 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_agent_capabilities import (
    CreateHasnAgentCapabilitiesParam,
    GetHasnAgentCapabilitiesDetail,
    UpdateHasnAgentCapabilitiesParam,
)
from backend.app.hasn.service.hasn_agent_capabilities_service import hasn_agent_capabilities_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的HASN Agent 能力声明列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_hasn_agent_capabilitiess(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnAgentCapabilitiesDetail]]:
    page_data = await hasn_agent_capabilities_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Agent 能力声明',
    dependencies=[DependsJwtAuth],
)
async def create_my_hasn_agent_capabilities(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnAgentCapabilitiesParam,
) -> ResponseModel:
    result = await hasn_agent_capabilities_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN Agent 能力声明详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_hasn_agent_capabilities(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN Agent 能力声明 ID')],
) -> ResponseSchemaModel[GetHasnAgentCapabilitiesDetail]:
    hasn_agent_capabilities = await hasn_agent_capabilities_service.get(db=db, pk=pk)
    if hasn_agent_capabilities.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该HASN Agent 能力声明')
    return response_base.success(data=hasn_agent_capabilities)


@router.put(
    '/{pk}',
    summary='更新HASN Agent 能力声明',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_agent_capabilities(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN Agent 能力声明 ID')],
    obj: UpdateHasnAgentCapabilitiesParam,
) -> ResponseModel:
    user_id = request.user.id
    hasn_agent_capabilities = await hasn_agent_capabilities_service.get(db=db, pk=pk)
    if hasn_agent_capabilities.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN Agent 能力声明')
    count = await hasn_agent_capabilities_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN Agent 能力声明',
    dependencies=[DependsJwtAuth],
)
async def delete_my_hasn_agent_capabilities(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN Agent 能力声明 ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_agent_capabilities = await hasn_agent_capabilities_service.get(db=db, pk=pk)
    if hasn_agent_capabilities.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN Agent 能力声明')
    from backend.app.hasn.schema.hasn_agent_capabilities import DeleteHasnAgentCapabilitiesParam
    count = await hasn_agent_capabilities_service.delete(db=db, obj=DeleteHasnAgentCapabilitiesParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
