"""视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_asset import (
    CreateStudioAssetParam,
    UpdateStudioAssetParam,
)
from backend.app.hasn_studio.service.studio_asset_service import studio_asset_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_list_studio_asset',
)
async def agent_list_studio_asset(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await studio_asset_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_create_studio_asset',
)
async def agent_create_studio_asset(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioAssetParam,
) -> ResponseModel:
    await studio_asset_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_get_studio_asset',
)
async def agent_get_studio_asset(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
) -> ResponseModel:
    studio_asset = await studio_asset_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_asset.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）')
    return response_base.success(data=studio_asset)


@router.put(
    '/{pk}',
    summary='更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_update_studio_asset',
)
async def agent_update_studio_asset(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
    obj: UpdateStudioAssetParam,
) -> ResponseModel:
    await studio_asset_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_asset.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）')
    count = await studio_asset_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_studio_agent_delete_studio_asset',
)
async def agent_delete_studio_asset(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID')],
) -> ResponseModel:
    await studio_asset_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if studio_asset.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）')
    from backend.app.hasn_studio.schema.studio_asset import DeleteStudioAssetParam
    count = await studio_asset_service.delete(db=db, obj=DeleteStudioAssetParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
