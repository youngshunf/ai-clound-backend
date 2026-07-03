from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_reel.schema.reel_creation import (
    CreateReelCreationParam,
    DeleteReelCreationParam,
    GetReelCreationDetail,
    UpdateReelCreationParam,
)
from backend.app.hasn_reel.service.reel_creation_service import reel_creation_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）详情', dependencies=[DependsJwtAuth], name='hasn_reel_admin_get_reel_creation')
async def get_reel_creation(
    db: CurrentSession, pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')]
) -> ResponseSchemaModel[GetReelCreationDetail]:
    reel_creation = await reel_creation_service.get(db=db, pk=pk)
    return response_base.success(data=reel_creation)


@router.get(
    '',
    summary='分页获取所有一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_reel_admin_get_reel_creation_paginated',
)
async def get_reel_creation_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetReelCreationDetail]]:
    page_data = await reel_creation_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[
        Depends(RequestPermission('reel:creation:add')),
        DependsRBAC,
    ],
    name='hasn_reel_admin_create_reel_creation',
)
async def create_reel_creation(db: CurrentSessionTransaction, obj: CreateReelCreationParam) -> ResponseModel:
    await reel_creation_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[
        Depends(RequestPermission('reel:creation:edit')),
        DependsRBAC,
    ],
    name='hasn_reel_admin_update_reel_creation',
)
async def update_reel_creation(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID')], obj: UpdateReelCreationParam
) -> ResponseModel:
    count = await reel_creation_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
    dependencies=[
        Depends(RequestPermission('reel:creation:del')),
        DependsRBAC,
    ],
    name='hasn_reel_admin_delete_reel_creation',
)
async def delete_reel_creation(db: CurrentSessionTransaction, obj: DeleteReelCreationParam) -> ResponseModel:
    count = await reel_creation_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
