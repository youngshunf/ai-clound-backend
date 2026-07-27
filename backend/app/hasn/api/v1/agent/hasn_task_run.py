"""任务执行记录 - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_task_run import (
    CreateHasnTaskRunParam,
    UpdateHasnTaskRunParam,
)
from backend.app.hasn.service.hasn_task_run_service import hasn_task_run_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

if TYPE_CHECKING:
    from backend.common.dataclasses import AgentTokenPayload

router = APIRouter()


# ─── TaskResult 上报（hasn-node 回传执行结果） ───


class TaskResultParam:
    """TaskResult 上报参数"""

    def __init__(
        self,
        run_id: int,
        status: str,
        output: str | None = None,
        error: str | None = None,
        model: str | None = None,
        token_usage: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.run_id = run_id
        self.status = status
        self.output = output
        self.error = error
        self.model = model
        self.token_usage = token_usage
        self.duration_ms = duration_ms


@router.post(
    '/task-result',
    summary='上报任务执行结果',
    dependencies=[DependsAgentJwtAuth],
    name='agent_report_task_result',
)
async def agent_report_task_result(
    request: Request,
) -> ResponseModel:
    """hasn-node 上报任务执行结果（TaskResult 消息）"""
    from backend.app.hasn.service.task_scheduler import task_scheduler

    agent: AgentTokenPayload = request.state.agent
    body = await request.json()
    run_id = body.get('run_id')
    status = body.get('status', 'error')
    prompt_snapshot = body.get('prompt_snapshot')
    output = body.get('output')
    error = body.get('error')
    model = body.get('model')
    token_usage = body.get('token_usage')
    duration_ms = body.get('duration_ms')

    if not run_id:
        raise errors.RequestError(msg='run_id is required')

    success = await task_scheduler.handle_task_result(
        run_id=run_id,
        status=status,
        reporting_agent_id=agent.agent_hasn_id,
        prompt_snapshot=prompt_snapshot,
        output=output,
        error=error,
        model=model,
        token_usage=token_usage,
        duration_ms=duration_ms,
    )

    if success:
        return response_base.success(data={'run_id': run_id, 'status': status})
    raise errors.NotFoundError(msg=f'task_run {run_id} not found')


@router.get(
    '',
    summary='任务执行记录列表',
    dependencies=[DependsAgentJwtAuth],
    name='agent_list_hasn_task_run',
)
async def agent_list_hasn_task_run(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id
    data = await hasn_task_run_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建任务执行记录',
    dependencies=[DependsAgentJwtAuth],
    name='agent_create_hasn_task_run',
)
async def agent_create_hasn_task_run(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnTaskRunParam,
) -> ResponseModel:
    await hasn_task_run_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取任务执行记录详情',
    dependencies=[DependsAgentJwtAuth],
    name='agent_get_hasn_task_run',
)
async def agent_get_hasn_task_run(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='任务执行记录 ID')],
) -> ResponseModel:
    hasn_task_run = await hasn_task_run_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_task_run.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该任务执行记录')
    return response_base.success(data=hasn_task_run)


@router.put(
    '/{pk}',
    summary='更新任务执行记录',
    dependencies=[DependsAgentJwtAuth],
    name='agent_update_hasn_task_run',
)
async def agent_update_hasn_task_run(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='任务执行记录 ID')],
    obj: UpdateHasnTaskRunParam,
) -> ResponseModel:
    await hasn_task_run_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_task_run.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该任务执行记录')
    count = await hasn_task_run_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除任务执行记录',
    dependencies=[DependsAgentJwtAuth],
    name='agent_delete_hasn_task_run',
)
async def agent_delete_hasn_task_run(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='任务执行记录 ID')],
) -> ResponseModel:
    await hasn_task_run_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if hasn_task_run.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该任务执行记录')
    from backend.app.hasn.schema.hasn_task_run import DeleteHasnTaskRunParam

    count = await hasn_task_run_service.delete(db=db, obj=DeleteHasnTaskRunParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
