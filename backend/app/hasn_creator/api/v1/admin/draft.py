from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.draft import (
    CreateDraftParam,
    DeleteDraftParam,
    GetDraftDetail,
    UpdateDraftParam,
)
from backend.app.hasn_creator.service.draft_service import draft_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取草稿箱（灵感快速捕获，轻量独立于正式流水线）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_draft')
async def get_draft(
    db: CurrentSession, pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')]
) -> ResponseSchemaModel[GetDraftDetail]:
    draft = await draft_service.get(db=db, pk=pk)
    return response_base.success(data=draft)


@router.get(
    '',
    summary='分页获取所有草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_draft_paginated',
)
async def get_draft_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetDraftDetail]]:
    page_data = await draft_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[
        Depends(RequestPermission('draft:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_draft',
)
async def create_draft(db: CurrentSessionTransaction, obj: CreateDraftParam) -> ResponseModel:
    await draft_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[
        Depends(RequestPermission('draft:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_draft',
)
async def update_draft(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')], obj: UpdateDraftParam
) -> ResponseModel:
    count = await draft_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[
        Depends(RequestPermission('draft:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_draft',
)
async def delete_draft(db: CurrentSessionTransaction, obj: DeleteDraftParam) -> ResponseModel:
    count = await draft_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
