"""设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_design.schema.hasn_design_project import (
    CreateHasnDesignProjectParam,
    UpdateHasnDesignProjectParam,
)
from backend.app.hasn_design.service.hasn_design_project_service import hasn_design_project_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_design_agent_list_hasn_design_project',
)
async def agent_list_hasn_design_project(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await hasn_design_project_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_design_agent_create_hasn_design_project',
)
async def agent_create_hasn_design_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnDesignProjectParam,
) -> ResponseModel:
    result = await hasn_design_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_design_agent_get_hasn_design_project',
)
async def agent_get_hasn_design_project(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
) -> ResponseModel:
    hasn_design_project = await hasn_design_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_design_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）')
    return response_base.success(data=hasn_design_project)


@router.put(
    '/{pk}',
    summary='更新设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_design_agent_update_hasn_design_project',
)
async def agent_update_hasn_design_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
    obj: UpdateHasnDesignProjectParam,
) -> ResponseModel:
    await hasn_design_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_design_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）')
    count = await hasn_design_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_design_agent_delete_hasn_design_project',
)
async def agent_delete_hasn_design_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID')],
) -> ResponseModel:
    await hasn_design_project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_design_project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）')
    from backend.app.hasn_design.schema.hasn_design_project import DeleteHasnDesignProjectParam

    count = await hasn_design_project_service.delete(db=db, obj=DeleteHasnDesignProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
