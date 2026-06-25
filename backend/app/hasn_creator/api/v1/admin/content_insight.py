from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.content_insight import (
    CreateContentInsightParam,
    DeleteContentInsightParam,
    GetContentInsightDetail,
    UpdateContentInsightParam,
)
from backend.app.hasn_creator.service.content_insight_service import content_insight_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取内容洞察（复盘结构化结论，进化沉淀核心）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_content_insight')
async def get_content_insight(
    db: CurrentSession, pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')]
) -> ResponseSchemaModel[GetContentInsightDetail]:
    content_insight = await content_insight_service.get(db=db, pk=pk)
    return response_base.success(data=content_insight)


@router.get(
    '',
    summary='分页获取所有内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_content_insight_paginated',
)
async def get_content_insight_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetContentInsightDetail]]:
    page_data = await content_insight_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[
        Depends(RequestPermission('content:insight:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_content_insight',
)
async def create_content_insight(db: CurrentSessionTransaction, obj: CreateContentInsightParam) -> ResponseModel:
    await content_insight_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[
        Depends(RequestPermission('content:insight:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_content_insight',
)
async def update_content_insight(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='内容洞察（复盘结构化结论，进化沉淀核心） ID')], obj: UpdateContentInsightParam
) -> ResponseModel:
    count = await content_insight_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除内容洞察（复盘结构化结论，进化沉淀核心）',
    dependencies=[
        Depends(RequestPermission('content:insight:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_content_insight',
)
async def delete_content_insight(db: CurrentSessionTransaction, obj: DeleteContentInsightParam) -> ResponseModel:
    count = await content_insight_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
