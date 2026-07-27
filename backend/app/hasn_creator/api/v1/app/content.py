"""内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.content import (
    CreateContentParam,
    GetContentDetail,
    UpdateContentParam,
)
from backend.app.hasn_creator.service.content_service import content_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_content',
)
async def get_my_content(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetContentDetail]]:
    page_data = await content_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_content',
)
async def create_my_content(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateContentParam,
) -> ResponseModel:
    await content_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_content_detail',
)
async def get_my_content_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
) -> ResponseSchemaModel[GetContentDetail]:
    content = await content_service.get(db=db, pk=pk)
    if content.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核')
    return response_base.success(data=content)


@router.put(
    '/{pk}',
    summary='更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_content',
)
async def update_my_content(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
    obj: UpdateContentParam,
) -> ResponseModel:
    content = await content_service.get(db=db, pk=pk)
    if getattr(content, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核')
    count = await content_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_content',
)
async def delete_my_content(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')],
) -> ResponseModel:
    user_id = request.user.id
    content = await content_service.get(db=db, pk=pk)
    if content.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核')
    from backend.app.hasn_creator.schema.content import DeleteContentParam
    count = await content_service.delete(db=db, obj=DeleteContentParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
