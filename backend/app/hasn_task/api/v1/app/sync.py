"""任务同步 - 用户端 API（hasn_task 应用，canonical surface）

- /sync/pull · /sync/push：v2.1 任务定义双向同步（Owner JWT，daemon 通道）
- /runs/summary：run 摘要上报（Agent JWT，runtime 执行侧）
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
from backend.app.hasn_task.service.builtin_task_service import workbench_builtin_task_service
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
