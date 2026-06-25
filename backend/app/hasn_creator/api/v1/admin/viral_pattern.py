from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.viral_pattern import (
    CreateViralPatternParam,
    DeleteViralPatternParam,
    GetViralPatternDetail,
    UpdateViralPatternParam,
)
from backend.app.hasn_creator.service.viral_pattern_service import viral_pattern_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_viral_pattern')
async def get_viral_pattern(
    db: CurrentSession, pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')]
) -> ResponseSchemaModel[GetViralPatternDetail]:
    viral_pattern = await viral_pattern_service.get(db=db, pk=pk)
    return response_base.success(data=viral_pattern)


@router.get(
    '',
    summary='分页获取所有爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_viral_pattern_paginated',
)
async def get_viral_pattern_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetViralPatternDetail]]:
    page_data = await viral_pattern_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[
        Depends(RequestPermission('viral:pattern:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_viral_pattern',
)
async def create_viral_pattern(db: CurrentSessionTransaction, obj: CreateViralPatternParam) -> ResponseModel:
    await viral_pattern_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[
        Depends(RequestPermission('viral:pattern:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_viral_pattern',
)
async def update_viral_pattern(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID')], obj: UpdateViralPatternParam
) -> ResponseModel:
    count = await viral_pattern_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）',
    dependencies=[
        Depends(RequestPermission('viral:pattern:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_viral_pattern',
)
async def delete_viral_pattern(db: CurrentSessionTransaction, obj: DeleteViralPatternParam) -> ResponseModel:
    count = await viral_pattern_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
