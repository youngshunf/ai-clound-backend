"""草稿箱（灵感快速捕获，轻量独立于正式流水线） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.draft import (
    CreateDraftParam,
    GetDraftDetail,
    UpdateDraftParam,
)
from backend.app.hasn_creator.service.draft_service import draft_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的草稿箱（灵感快速捕获，轻量独立于正式流水线）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_draft',
)
async def get_my_draft(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetDraftDetail]]:
    page_data = await draft_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_draft',
)
async def create_my_draft(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateDraftParam,
) -> ResponseModel:
    await draft_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取草稿箱（灵感快速捕获，轻量独立于正式流水线）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_draft_detail',
)
async def get_my_draft_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
) -> ResponseSchemaModel[GetDraftDetail]:
    draft = await draft_service.get(db=db, pk=pk)
    if draft.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该草稿箱（灵感快速捕获，轻量独立于正式流水线）')
    return response_base.success(data=draft)


@router.put(
    '/{pk}',
    summary='更新草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_draft',
)
async def update_my_draft(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
    obj: UpdateDraftParam,
) -> ResponseModel:
    draft = await draft_service.get(db=db, pk=pk)
    if getattr(draft, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该草稿箱（灵感快速捕获，轻量独立于正式流水线）')
    count = await draft_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除草稿箱（灵感快速捕获，轻量独立于正式流水线）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_draft',
)
async def delete_my_draft(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID')],
) -> ResponseModel:
    user_id = request.user.id
    draft = await draft_service.get(db=db, pk=pk)
    if draft.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该草稿箱（灵感快速捕获，轻量独立于正式流水线）')
    from backend.app.hasn_creator.schema.draft import DeleteDraftParam
    count = await draft_service.delete(db=db, obj=DeleteDraftParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
