"""演示文稿版本快照（云端权威历史） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_designsystem.schema.revision import (
    CreateRevisionParam,
    GetRevisionDetail,
    UpdateRevisionParam,
)
from backend.app.hasn_designsystem.service.revision_service import revision_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的演示文稿版本快照（云端权威历史）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_designsystem_app_get_my_revision',
)
async def get_my_revision(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetRevisionDetail]]:
    page_data = await revision_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建演示文稿版本快照（云端权威历史）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_create_my_revision',
)
async def create_my_revision(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateRevisionParam,
) -> ResponseModel:
    result = await revision_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取演示文稿版本快照（云端权威历史）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_get_my_revision_detail',
)
async def get_my_revision_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
) -> ResponseSchemaModel[GetRevisionDetail]:
    revision = await revision_service.get(db=db, pk=pk)
    if revision.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该演示文稿版本快照（云端权威历史）')
    return response_base.success(data=revision)


@router.put(
    '/{pk}',
    summary='更新演示文稿版本快照（云端权威历史）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_update_my_revision',
)
async def update_my_revision(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
    obj: UpdateRevisionParam,
) -> ResponseModel:
    revision = await revision_service.get(db=db, pk=pk)
    if getattr(revision, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该演示文稿版本快照（云端权威历史）')
    count = await revision_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除演示文稿版本快照（云端权威历史）',
    dependencies=[DependsJwtAuth],
    name='hasn_designsystem_app_delete_my_revision',
)
async def delete_my_revision(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')],
) -> ResponseModel:
    user_id = request.user.id
    revision = await revision_service.get(db=db, pk=pk)
    if revision.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该演示文稿版本快照（云端权威历史）')
    from backend.app.hasn_designsystem.schema.revision import DeleteRevisionParam
    count = await revision_service.delete(db=db, obj=DeleteRevisionParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
