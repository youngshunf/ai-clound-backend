"""项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.profile import (
    CreateProfileParam,
    UpdateProfileParam,
)
from backend.app.hasn_creator.service.profile_service import profile_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_profile',
)
async def agent_list_profile(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await profile_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_profile',
)
async def agent_create_profile(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateProfileParam,
) -> ResponseModel:
    result = await profile_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_profile',
)
async def agent_get_profile(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
) -> ResponseModel:
    profile = await profile_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if profile.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）')
    return response_base.success(data=profile)


@router.put(
    '/{pk}',
    summary='更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_profile',
)
async def agent_update_profile(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
    obj: UpdateProfileParam,
) -> ResponseModel:
    await profile_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if profile.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）')
    count = await profile_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_profile',
)
async def agent_delete_profile(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
) -> ResponseModel:
    await profile_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if profile.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）')
    from backend.app.hasn_creator.schema.profile import DeleteProfileParam
    count = await profile_service.delete(db=db, obj=DeleteProfileParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
