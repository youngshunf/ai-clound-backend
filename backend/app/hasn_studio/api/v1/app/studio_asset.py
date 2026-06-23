"""视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_asset import (
    CreateStudioAssetParam,
    GetStudioAssetDetail,
    UpdateStudioAssetParam,
)
from backend.app.hasn_studio.service.studio_asset_service import studio_asset_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_studio_app_get_my_studio_asset',
)
async def get_my_studio_asset(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioAssetDetail]]:
    page_data = await studio_asset_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_create_my_studio_asset',
)
async def create_my_studio_asset(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioAssetParam,
) -> ResponseModel:
    result = await studio_asset_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_get_my_studio_asset_detail',
)
async def get_my_studio_asset_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
) -> ResponseSchemaModel[GetStudioAssetDetail]:
    studio_asset = await studio_asset_service.get(db=db, pk=pk)
    if studio_asset.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）')
    return response_base.success(data=studio_asset)


@router.put(
    '/{pk}',
    summary='更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_update_my_studio_asset',
)
async def update_my_studio_asset(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
    obj: UpdateStudioAssetParam,
) -> ResponseModel:
    studio_asset = await studio_asset_service.get(db=db, pk=pk)
    if getattr(studio_asset, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）')
    count = await studio_asset_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_delete_my_studio_asset',
)
async def delete_my_studio_asset(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
) -> ResponseModel:
    user_id = request.user.id
    studio_asset = await studio_asset_service.get(db=db, pk=pk)
    if studio_asset.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）')
    from backend.app.hasn_studio.schema.studio_asset import DeleteStudioAssetParam
    count = await studio_asset_service.delete(db=db, obj=DeleteStudioAssetParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
