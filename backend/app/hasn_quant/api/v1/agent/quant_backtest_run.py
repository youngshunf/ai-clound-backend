"""回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） - Agent API

认证方式: Agent JWT (Bearer token)
Agent 信息: 通过 request.state.agent 获取
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_quant.schema.quant_backtest_run import (
    CreateQuantBacktestRunParam,
    UpdateQuantBacktestRunParam,
)
from backend.app.hasn_quant.service.quant_backtest_run_service import quant_backtest_run_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）列表',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_list_quant_backtest_run',
)
async def agent_list_quant_backtest_run(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    # 可以使用 agent.agent_hasn_id, agent.owner_hasn_id, agent.scopes
    data = await quant_backtest_run_service.get_list(db=db)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_create_quant_backtest_run',
)
async def agent_create_quant_backtest_run(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateQuantBacktestRunParam,
) -> ResponseModel:
    result = await quant_backtest_run_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）详情',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_get_quant_backtest_run',
)
async def agent_get_quant_backtest_run(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
) -> ResponseModel:
    quant_backtest_run = await quant_backtest_run_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if quant_backtest_run.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权访问该回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）')
    return response_base.success(data=quant_backtest_run)


@router.put(
    '/{pk}',
    summary='更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_update_quant_backtest_run',
)
async def agent_update_quant_backtest_run(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
    obj: UpdateQuantBacktestRunParam,
) -> ResponseModel:
    await quant_backtest_run_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if quant_backtest_run.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权修改该回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）')
    count = await quant_backtest_run_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_quant_agent_delete_quant_backtest_run',
)
async def agent_delete_quant_backtest_run(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID')],
) -> ResponseModel:
    await quant_backtest_run_service.get(db=db, pk=pk)
    # TODO: 根据实际业务需求添加权限检查
    # if quant_backtest_run.owner_id != agent.owner_hasn_id:
    #     raise errors.ForbiddenError(msg='无权删除该回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）')
    from backend.app.hasn_quant.schema.quant_backtest_run import DeleteQuantBacktestRunParam
    count = await quant_backtest_run_service.delete(db=db, obj=DeleteQuantBacktestRunParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
