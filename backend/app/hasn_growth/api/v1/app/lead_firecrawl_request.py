"""Firecrawl request audit for AI lead automation - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_firecrawl_request import (
    CreateLeadFirecrawlRequestParam,
    GetLeadFirecrawlRequestDetail,
    UpdateLeadFirecrawlRequestParam,
)
from backend.app.hasn_growth.service.lead_firecrawl_request_service import lead_firecrawl_request_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的Firecrawl request audit for AI lead automation列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_firecrawl_requests(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadFirecrawlRequestDetail]]:
    page_data = await lead_firecrawl_request_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Firecrawl request audit for AI lead automation',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_firecrawl_request(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadFirecrawlRequestParam,
) -> ResponseModel:
    result = await lead_firecrawl_request_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取Firecrawl request audit for AI lead automation详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_firecrawl_request(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')],
) -> ResponseSchemaModel[GetLeadFirecrawlRequestDetail]:
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    if lead_firecrawl_request.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该Firecrawl request audit for AI lead automation')
    return response_base.success(data=lead_firecrawl_request)


@router.put(
    '/{pk}',
    summary='更新Firecrawl request audit for AI lead automation',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_firecrawl_request(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')],
    obj: UpdateLeadFirecrawlRequestParam,
) -> ResponseModel:
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    if getattr(lead_firecrawl_request, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该Firecrawl request audit for AI lead automation')
    count = await lead_firecrawl_request_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除Firecrawl request audit for AI lead automation',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_firecrawl_request(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    if lead_firecrawl_request.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该Firecrawl request audit for AI lead automation')
    from backend.app.hasn_growth.schema.lead_firecrawl_request import DeleteLeadFirecrawlRequestParam
    count = await lead_firecrawl_request_service.delete(db=db, obj=DeleteLeadFirecrawlRequestParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
