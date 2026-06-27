"""一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_reel.schema.reel_creation import (
    CreateReelCreationParam,
    GetReelCreationDetail,
    UpdateReelCreationParam,
)
from backend.app.hasn_reel.service.reel_creation_service import reel_creation_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_reel_app_get_my_reel_creation',
)
async def get_my_reel_creation(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetReelCreationDetail]]:
    page_data = await reel_creation_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_create_my_reel_creation',
)
async def create_my_reel_creation(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateReelCreationParam,
) -> ResponseModel:
    result = await reel_creation_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_get_my_reel_creation_detail',
)
async def get_my_reel_creation_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
) -> ResponseSchemaModel[GetReelCreationDetail]:
    reel_creation = await reel_creation_service.get(db=db, pk=pk)
    if reel_creation.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）')
    return response_base.success(data=reel_creation)


@router.put(
    '/{pk}',
    summary='更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_update_my_reel_creation',
)
async def update_my_reel_creation(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
    obj: UpdateReelCreationParam,
) -> ResponseModel:
    reel_creation = await reel_creation_service.get(db=db, pk=pk)
    if getattr(reel_creation, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）')
    count = await reel_creation_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_delete_my_reel_creation',
)
async def delete_my_reel_creation(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')],
) -> ResponseModel:
    user_id = request.user.id
    reel_creation = await reel_creation_service.get(db=db, pk=pk)
    if reel_creation.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）')
    from backend.app.hasn_reel.schema.reel_creation import DeleteReelCreationParam
    count = await reel_creation_service.delete(db=db, obj=DeleteReelCreationParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
