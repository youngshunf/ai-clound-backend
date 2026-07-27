"""素材库；配图/封面/视频/模板（私有桶引用） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.media import (
    CreateMediaParam,
    UpdateMediaParam,
)
from backend.app.hasn_creator.service.media_service import media_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='素材库；配图/封面/视频/模板（私有桶引用）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_media',
)
async def agent_list_media(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await media_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_media',
)
async def agent_create_media(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateMediaParam,
) -> ResponseModel:
    await media_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取素材库；配图/封面/视频/模板（私有桶引用）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_media',
)
async def agent_get_media(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
) -> ResponseModel:
    media = await media_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if media.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该素材库；配图/封面/视频/模板（私有桶引用）')
    return response_base.success(data=media)


@router.put(
    '/{pk}',
    summary='更新素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_media',
)
async def agent_update_media(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
    obj: UpdateMediaParam,
) -> ResponseModel:
    await media_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if media.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该素材库；配图/封面/视频/模板（私有桶引用）')
    count = await media_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_media',
)
async def agent_delete_media(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
) -> ResponseModel:
    await media_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if media.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该素材库；配图/封面/视频/模板（私有桶引用）')
    from backend.app.hasn_creator.schema.media import DeleteMediaParam
    count = await media_service.delete(db=db, obj=DeleteMediaParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
