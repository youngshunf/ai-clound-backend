"""阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.content_stage import (
    CreateContentStageParam,
    GetContentStageDetail,
    UpdateContentStageParam,
)
from backend.app.hasn_creator.service.content_stage_service import content_stage_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_content_stage',
)
async def get_my_content_stage(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetContentStageDetail]]:
    page_data = await content_stage_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_content_stage',
)
async def create_my_content_stage(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateContentStageParam,
) -> ResponseModel:
    await content_stage_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_content_stage_detail',
)
async def get_my_content_stage_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
) -> ResponseSchemaModel[GetContentStageDetail]:
    content_stage = await content_stage_service.get(db=db, pk=pk)
    if content_stage.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播')
    return response_base.success(data=content_stage)


@router.put(
    '/{pk}',
    summary='更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_content_stage',
)
async def update_my_content_stage(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
    obj: UpdateContentStageParam,
) -> ResponseModel:
    content_stage = await content_stage_service.get(db=db, pk=pk)
    if getattr(content_stage, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播')
    count = await content_stage_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_content_stage',
)
async def delete_my_content_stage(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID')],
) -> ResponseModel:
    user_id = request.user.id
    content_stage = await content_stage_service.get(db=db, pk=pk)
    if content_stage.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播')
    from backend.app.hasn_creator.schema.content_stage import DeleteContentStageParam
    count = await content_stage_service.delete(db=db, obj=DeleteContentStageParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
