from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_creator.schema.content_stage import (
    CreateContentStageParam,
    DeleteContentStageParam,
    GetContentStageDetail,
    UpdateContentStageParam,
)
from backend.app.hasn_creator.service.content_stage_service import content_stage_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_content_stage')
async def get_content_stage(
    db: CurrentSession, pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')]
) -> ResponseSchemaModel[GetContentStageDetail]:
    content_stage = await content_stage_service.get(db=db, pk=pk)
    return response_base.success(data=content_stage)


@router.get(
    '',
    summary='分页获取所有阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_content_stage_paginated',
)
async def get_content_stage_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetContentStageDetail]]:
    page_data = await content_stage_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[
        Depends(RequestPermission('content:stage:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_content_stage',
)
async def create_content_stage(db: CurrentSessionTransaction, obj: CreateContentStageParam) -> ResponseModel:
    await content_stage_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[
        Depends(RequestPermission('content:stage:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_content_stage',
)
async def update_content_stage(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')], obj: UpdateContentStageParam
) -> ResponseModel:
    count = await content_stage_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[
        Depends(RequestPermission('content:stage:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_content_stage',
)
async def delete_content_stage(db: CurrentSessionTransaction, obj: DeleteContentStageParam) -> ResponseModel:
    count = await content_stage_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
