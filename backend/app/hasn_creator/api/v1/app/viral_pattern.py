"""爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.viral_pattern import (
    CreateViralPatternParam,
    GetViralPatternDetail,
    UpdateViralPatternParam,
)
from backend.app.hasn_creator.service.viral_pattern_service import viral_pattern_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_viral_pattern',
)
async def get_my_viral_pattern(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetViralPatternDetail]]:
    page_data = await viral_pattern_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_viral_pattern',
)
async def create_my_viral_pattern(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateViralPatternParam,
) -> ResponseModel:
    result = await viral_pattern_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_viral_pattern_detail',
)
async def get_my_viral_pattern_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
) -> ResponseSchemaModel[GetViralPatternDetail]:
    viral_pattern = await viral_pattern_service.get(db=db, pk=pk)
    if viral_pattern.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）')
    return response_base.success(data=viral_pattern)


@router.put(
    '/{pk}',
    summary='更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_viral_pattern',
)
async def update_my_viral_pattern(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
    obj: UpdateViralPatternParam,
) -> ResponseModel:
    viral_pattern = await viral_pattern_service.get(db=db, pk=pk)
    if getattr(viral_pattern, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）')
    count = await viral_pattern_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_viral_pattern',
)
async def delete_my_viral_pattern(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')],
) -> ResponseModel:
    user_id = request.user.id
    viral_pattern = await viral_pattern_service.get(db=db, pk=pk)
    if viral_pattern.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）')
    from backend.app.hasn_creator.schema.viral_pattern import DeleteViralPatternParam
    count = await viral_pattern_service.delete(db=db, obj=DeleteViralPatternParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
