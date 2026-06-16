from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_task.schema.skill_bundle import (
    CreateHasnSkillBundleParam,
    DeleteHasnSkillBundleParam,
    GetHasnSkillBundleDetail,
    UpdateHasnSkillBundleParam,
)
from backend.app.hasn_task.service.skill_bundle_service import hasn_skill_bundle_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/{pk}',
    summary='获取Skill Bundle 定义表（多个 skill 的组合）详情',
    dependencies=[DependsJwtAuth],
    name='admin_get_hasn_skill_bundle',
)
async def get_hasn_skill_bundle(
    db: CurrentSession, pk: Annotated[int, Path(description='Skill Bundle 定义表（多个 skill 的组合） ID')]
) -> ResponseSchemaModel[GetHasnSkillBundleDetail]:
    hasn_skill_bundle = await hasn_skill_bundle_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_skill_bundle)


@router.get(
    '',
    summary='分页获取所有Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_skill_bundle_paginated',
)
async def get_hasn_skill_bundle_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnSkillBundleDetail]]:
    page_data = await hasn_skill_bundle_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[
        Depends(RequestPermission('hasn:skill:bundle:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_skill_bundle',
)
async def create_hasn_skill_bundle(db: CurrentSessionTransaction, obj: CreateHasnSkillBundleParam) -> ResponseModel:
    await hasn_skill_bundle_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[
        Depends(RequestPermission('hasn:skill:bundle:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_skill_bundle',
)
async def update_hasn_skill_bundle(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='Skill Bundle 定义表（多个 skill 的组合） ID')],
    obj: UpdateHasnSkillBundleParam,
) -> ResponseModel:
    count = await hasn_skill_bundle_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Skill Bundle 定义表（多个 skill 的组合）',
    dependencies=[
        Depends(RequestPermission('hasn:skill:bundle:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_skill_bundle',
)
async def delete_hasn_skill_bundle(db: CurrentSessionTransaction, obj: DeleteHasnSkillBundleParam) -> ResponseModel:
    count = await hasn_skill_bundle_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
