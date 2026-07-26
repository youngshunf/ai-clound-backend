"""多任务编排（工作流）- 用户端 API（hasn_task 应用，设计 07 §9/§11）

认证方式: DependsJwtAuth（仅当前登录用户）。owner 隔离 = current_owner_id。
路径前缀: /api/v1/hasn-task/app
- 列/查工作流图 + 执行历史 + 节点产出
- 主人审批 agent 建的定时工作流（D4 业务态，approve/reject，非 ask_gate）
- 主人触发/暂停/取消（与 daemon 本地 WorkflowScheduler 经云端权威状态协同）
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request

from backend.app.hasn_task.api.v1.app.task import current_owner_id
from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
from backend.app.hasn_task.service.workflow_history_service import workflow_history_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/workflow-runs',
    summary='列我的场景执行历史',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_history_list',
)
async def list_my_workflow_runs(
    request: Request,
    db: CurrentSession,
    status: Annotated[str, Query(description='all/running/completed/failed/blocked/cancelled')] = 'all',
    project_id: Annotated[UUID | None, Query(description='按执行时的项目快照过滤')] = None,
    cursor: Annotated[str | None, Query(description='上一页返回的不透明游标')] = None,
    limit: Annotated[int, Query(description='单页数量，服务端会限制为 1 至 100')] = 50,
) -> ResponseModel:
    """按云端账本列执行历史，父工作流已删除时也返回快照记录。"""
    owner_id = await current_owner_id(request, db)
    data = await workflow_history_service.list_runs(
        db,
        owner_id=owner_id,
        status=status,
        project_id=project_id,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=data)


@router.get(
    '/workflow-runs/{workflow_run_uuid}/scenario-view',
    summary='查只读场景执行详情',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_history_scenario_view',
)
async def get_my_workflow_run_scenario_view(
    request: Request, db: CurrentSession, workflow_run_uuid: Annotated[str, Path()]
) -> ResponseModel:
    """远端执行实例只可查看；能力字段明确告诉客户端不得在此节点接管。"""
    owner_id = await current_owner_id(request, db)
    data = await workflow_history_service.get_scenario_view(db, owner_id=owner_id, workflow_run_uuid=workflow_run_uuid)
    return response_base.success(data=data)


@router.get('/workflows', summary='列我的工作流', dependencies=[DependsJwtAuth], name='hasn_workflow_app_list')
async def list_my_workflows(request: Request, db: CurrentSession) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    workflows = await agent_workflow_service.list_workflows(db, owner_id=owner_id)
    return response_base.success(data={'workflows': workflows})


@router.get(
    '/workflows/{workflow_uuid}',
    summary='查工作流图 + 执行历史',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_get',
)
async def get_my_workflow(request: Request, db: CurrentSession, workflow_uuid: Annotated[str, Path()]) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    detail = await agent_workflow_service.get_workflow(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
    return response_base.success(data=detail)


@router.get(
    '/workflows/{workflow_uuid}/nodes/{node_key}/result',
    summary='取节点产出',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_node_result',
)
async def get_my_node_result(
    request: Request,
    db: CurrentSession,
    workflow_uuid: Annotated[str, Path()],
    node_key: Annotated[str, Path()],
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    result = await agent_workflow_service.get_node_result(
        db, owner_id=owner_id, workflow_uuid=workflow_uuid, node_key=node_key
    )
    return response_base.success(data={'node': result})


@router.post(
    '/workflows/{workflow_uuid}/approve',
    summary='批准 agent 建的定时工作流（D4）',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_approve',
)
async def approve_my_workflow(
    request: Request, db: CurrentSessionTransaction, workflow_uuid: Annotated[str, Path()]
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    workflow = await agent_workflow_service.approve(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
    return response_base.success(data={'workflow': workflow})


@router.post(
    '/workflows/{workflow_uuid}/reject',
    summary='拒绝 agent 建的定时工作流（D4）',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_reject',
)
async def reject_my_workflow(
    request: Request, db: CurrentSessionTransaction, workflow_uuid: Annotated[str, Path()]
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    workflow = await agent_workflow_service.reject(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
    return response_base.success(data={'workflow': workflow})


@router.post(
    '/workflows/{workflow_uuid}/run',
    summary='立即触发整图',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_run',
)
async def run_my_workflow(
    request: Request, db: CurrentSessionTransaction, workflow_uuid: Annotated[str, Path()]
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    workflow = await agent_workflow_service.run(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
    return response_base.success(data={'workflow': workflow})


@router.post(
    '/workflows/{workflow_uuid}/pause',
    summary='暂停工作流',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_pause',
)
async def pause_my_workflow(
    request: Request, db: CurrentSessionTransaction, workflow_uuid: Annotated[str, Path()]
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    workflow = await agent_workflow_service.pause(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
    return response_base.success(data={'workflow': workflow})


@router.post(
    '/workflows/{workflow_uuid}/cancel',
    summary='取消整图执行',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_app_cancel',
)
async def cancel_my_workflow(
    request: Request, db: CurrentSessionTransaction, workflow_uuid: Annotated[str, Path()]
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    result = await agent_workflow_service.cancel(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
    return response_base.success(data=result)
