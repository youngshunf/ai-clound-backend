"""竞品账号（定位/选题调研输入） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.competitor import (
    CreateCompetitorParam,
    GetCompetitorDetail,
    UpdateCompetitorParam,
)
from backend.app.hasn_creator.service.competitor_service import competitor_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的竞品账号（定位/选题调研输入）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_competitor',
)
async def get_my_competitor(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetCompetitorDetail]]:
    page_data = await competitor_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建竞品账号（定位/选题调研输入）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_competitor',
)
async def create_my_competitor(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateCompetitorParam,
) -> ResponseModel:
    await competitor_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取竞品账号（定位/选题调研输入）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_competitor_detail',
)
async def get_my_competitor_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
) -> ResponseSchemaModel[GetCompetitorDetail]:
    competitor = await competitor_service.get(db=db, pk=pk)
    if competitor.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该竞品账号（定位/选题调研输入）')
    return response_base.success(data=competitor)


@router.put(
    '/{pk}',
    summary='更新竞品账号（定位/选题调研输入）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_competitor',
)
async def update_my_competitor(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
    obj: UpdateCompetitorParam,
) -> ResponseModel:
    competitor = await competitor_service.get(db=db, pk=pk)
    if getattr(competitor, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该竞品账号（定位/选题调研输入）')
    count = await competitor_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除竞品账号（定位/选题调研输入）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_competitor',
)
async def delete_my_competitor(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='竞品账号（定位/选题调研输入） ID')],
) -> ResponseModel:
    user_id = request.user.id
    competitor = await competitor_service.get(db=db, pk=pk)
    if competitor.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该竞品账号（定位/选题调研输入）')
    from backend.app.hasn_creator.schema.competitor import DeleteCompetitorParam
    count = await competitor_service.delete(db=db, obj=DeleteCompetitorParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
