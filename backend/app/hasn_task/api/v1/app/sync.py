"""任务同步 - 用户端 API（hasn_task 应用，canonical surface）

- /sync/pull · /sync/push：v2.1 任务定义双向同步（Owner JWT，daemon 通道）
- /runs/summary：run 摘要上报（Agent JWT，runtime 执行侧）
- /workflow-node-runs:sync：工作流执行态上行（Owner JWT，daemon 调度器侧，doc36 U5a）
- /builtin-catalog：内置任务目录拉取（Owner JWT，daemon 播种）

服务实现沿用 app/hasn 的 hasn_sync_service（v2.1 同步引擎，跨模块复用），
仅路由面收口进本应用。路径前缀: /api/v1/hasn-task/app
"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from backend.app.hasn.schema.hasn_sync import (
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    TaskRunSummaryRequest,
)
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn_task.api.v1.app.task import current_owner_id
from backend.app.hasn_task.schema.workflow_sync import WorkflowNodeRunsSyncRequest
from backend.app.hasn_task.service.builtin_task_service import workbench_builtin_task_service
from backend.app.hasn_task.service.workflow_sync_service import workflow_sync_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

if TYPE_CHECKING:
    from backend.app.hasn_task.schema.builtin_catalog import BuiltinTaskCatalogResponse

router = APIRouter()


@router.post(
    '/sync/pull',
    summary='拉取任务同步事件（task cursor 之后）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_sync_pull',
)
async def pull_task_sync_events(
    request: Request,
    db: CurrentSession,
    request_body: SyncPullRequest,
) -> SyncPullResponse:
    request_body.node_id = request_body.node_id or request.headers.get('X-Node-Id')
    return await hasn_sync_service.pull_tasks(db, request_body, user_id=request.user.id)


@router.post(
    '/sync/push',
    summary='推送本地任务事件',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_sync_push',
)
async def push_task_sync_events(
    request: Request,
    db: CurrentSessionTransaction,
    request_body: SyncPushRequest,
) -> SyncPushResponse:
    return await hasn_sync_service.push_tasks(db, request_body, user_id=request.user.id)


@router.post(
    '/runs/summary',
    summary='上报任务运行摘要',
    dependencies=[DependsAgentJwtAuth],
    name='hasn_task_app_report_run_summary',
)
async def report_task_run_summary(
    request: Request,
    db: CurrentSessionTransaction,
    request_body: TaskRunSummaryRequest,
) -> ResponseModel:
    summary = await hasn_sync_service.report_task_run_summary(db, request_body, agent=request.state.agent)
    return response_base.success(data=summary)


@router.post(
    '/workflow-node-runs:sync',
    summary='上行工作流执行态（daemon 调度器 → 云端权威表）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_sync_workflow_node_runs',
)
async def sync_workflow_node_runs(
    request: Request,
    db: CurrentSessionTransaction,
    request_body: WorkflowNodeRunsSyncRequest,
) -> ResponseModel:
    """daemon 把本地权威的 `workflow_run` / `workflow_node_run` 推上云（doc36 §6.3 · U5a）。

    **Owner JWT 而非 Agent JWT**：写者是 daemon 的工作流调度器，一次整图 fire 横跨多个分身的节点
    ——Agent JWT 的「不许替别的分身上报」校验会把同批直接顶掉。工作流实例本就 owner 所有，owner
    一律取 JWT 权威身份，入参里的 `owner_id` 只做一致性校验（不一致 → 403，不静默改写）。
    """
    owner_id = await current_owner_id(request, db)
    if request_body.owner_id and request_body.owner_id != owner_id:
        raise errors.ForbiddenError(msg='不能替其它主人上报工作流执行态')
    data = await workflow_sync_service.sync_node_runs(db, request_body, owner_id=owner_id)
    return response_base.success(data=data)


@router.get(
    '/builtin-catalog',
    summary='拉取启用中的内置任务目录（含聚合版本号）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_builtin_catalog',
)
async def list_builtin_catalog(
    request: Request,
    db: CurrentSession,
) -> ResponseModel:
    data: BuiltinTaskCatalogResponse = await workbench_builtin_task_service.list_enabled(db)
    return response_base.success(data=data)
