"""内容洞察（复盘结构化结论，进化沉淀核心） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.content_insight import (
    CreateContentInsightParam,
    GetContentInsightDetail,
    UpdateContentInsightParam,
)
from backend.app.hasn_creator.service.content_insight_service import content_insight_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的内容洞察（复盘结构化结论，进化沉淀核心）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_content_insight',
)
async def get_my_content_insight(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetContentInsightDetail]]:
    page_data = await content_insight_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_content_insight',
)
async def create_my_content_insight(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateContentInsightParam,
) -> ResponseModel:
    await content_insight_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取内容洞察（复盘结构化结论，进化沉淀核心）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_content_insight_detail',
)
async def get_my_content_insight_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
) -> ResponseSchemaModel[GetContentInsightDetail]:
    content_insight = await content_insight_service.get(db=db, pk=pk)
    if content_insight.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该内容洞察（复盘结构化结论，进化沉淀核心）')
    return response_base.success(data=content_insight)


@router.put(
    '/{pk}',
    summary='更新内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_content_insight',
)
async def update_my_content_insight(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
    obj: UpdateContentInsightParam,
) -> ResponseModel:
    content_insight = await content_insight_service.get(db=db, pk=pk)
    if getattr(content_insight, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该内容洞察（复盘结构化结论，进化沉淀核心）')
    count = await content_insight_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_content_insight',
)
async def delete_my_content_insight(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')],
) -> ResponseModel:
    user_id = request.user.id
    content_insight = await content_insight_service.get(db=db, pk=pk)
    if content_insight.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该内容洞察（复盘结构化结论，进化沉淀核心）')
    from backend.app.hasn_creator.schema.content_insight import DeleteContentInsightParam
    count = await content_insight_service.delete(db=db, obj=DeleteContentInsightParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
