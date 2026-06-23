from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_studio.schema.studio_asset import (
    CreateStudioAssetParam,
    DeleteStudioAssetParam,
    GetStudioAssetDetail,
    UpdateStudioAssetParam,
)
from backend.app.hasn_studio.service.studio_asset_service import studio_asset_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）详情', dependencies=[DependsJwtAuth], name='hasn_studio_admin_get_studio_asset')
async def get_studio_asset(
    db: CurrentSession, pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')]
) -> ResponseSchemaModel[GetStudioAssetDetail]:
    studio_asset = await studio_asset_service.get(db=db, pk=pk)
    return response_base.success(data=studio_asset)


@router.get(
    '',
    summary='分页获取所有视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_studio_admin_get_studio_asset_paginated',
)
async def get_studio_asset_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetStudioAssetDetail]]:
    page_data = await studio_asset_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[
        Depends(RequestPermission('studio:asset:add')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_create_studio_asset',
)
async def create_studio_asset(db: CurrentSessionTransaction, obj: CreateStudioAssetParam) -> ResponseModel:
    await studio_asset_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[
        Depends(RequestPermission('studio:asset:edit')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_update_studio_asset',
)
async def update_studio_asset(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')], obj: UpdateStudioAssetParam
) -> ResponseModel:
    count = await studio_asset_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[
        Depends(RequestPermission('studio:asset:del')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_delete_studio_asset',
)
async def delete_studio_asset(db: CurrentSessionTransaction, obj: DeleteStudioAssetParam) -> ResponseModel:
    count = await studio_asset_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
