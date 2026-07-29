"""运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.project import (
    CreateProjectParam,
    UpdateProjectParam,
)
from backend.app.hasn_creator.service.project_service import project_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_list_project',
)
async def agent_list_project(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await project_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_create_project',
)
async def agent_create_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateProjectParam,
) -> ResponseModel:
    await project_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_get_project',
)
async def agent_get_project(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
) -> ResponseModel:
    project = await project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度')
    return response_base.success(data=project)


@router.put(
    '/{pk}',
    summary='更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_update_project',
)
async def agent_update_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
    obj: UpdateProjectParam,
) -> ResponseModel:
    await project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度')
    count = await project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_creator_agent_delete_project',
)
async def agent_delete_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
) -> ResponseModel:
    await project_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if project.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度')
    from backend.app.hasn_creator.schema.project import DeleteProjectParam
    count = await project_service.delete(db=db, obj=DeleteProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
