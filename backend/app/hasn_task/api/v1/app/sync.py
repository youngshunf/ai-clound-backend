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
from backend.app.hasn.service._sync_codec import _contains_private_runtime_key
from backend.app.hasn.service.sync_business_handlers import TASK_SYNC_EVENTS
from backend.app.hasn.service.sync_entry_auth import (
    require_node_identity,
    require_owner_identity,
)
from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.app.hasn_sync.application.push import accept_envelopes
from backend.app.hasn_sync.ports.dto import InboxEnvelope
from backend.app.hasn_task.api.v1.app.task import current_owner_id
from backend.app.hasn_task.schema.workflow_sync import (
    WorkflowDefinitionsSyncRequest,
    WorkflowNodeRunsSyncRequest,
)
from backend.app.hasn_task.service.builtin_task_service import workbench_builtin_task_service
from backend.app.hasn_task.service.workflow_sync_service import workflow_sync_service
from backend.app.hasn_task.service.workflow_definition_import_service import workflow_definition_import_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import (
    CurrentSession,
    CurrentSessionTransaction,
    CurrentSyncSession,
    CurrentSyncSessionTransaction,
)

if TYPE_CHECKING:
    from backend.app.hasn_task.schema.builtin_catalog import BuiltinTaskCatalogResponse

router = APIRouter()
_TASK_EVENT_UNSUPPORTED_ERROR = ErrorObject(
    code=8037,
    name='ERR_TASK_SYNC_EVENT_UNSUPPORTED',
    message='任务同步事件类型不受支持',
)
_PRIVATE_METADATA_ERROR = ErrorObject(
    code=8034,
    name='ERR_RUNTIME_PRIVATE_METADATA_REJECTED',
    message='任务同步载荷包含本地私有元数据',
)
_TASK_CONFLICT_ERROR = ErrorObject(
    code=8042,
    name='ERR_TASK_SYNC_INBOX_CONFLICT',
    message='同一任务客户端事件 ID 的载荷与已接收事件不一致',
)


def _rejected_for(template: ErrorObject, client_event_id: str) -> ErrorObject:
    """把拒绝模板绑定到具体事件——`detail.client_event_id` 是客户端逐事件定位的唯一锚点。"""
    return template.model_copy(update={'detail': {'client_event_id': client_event_id}})


@router.post(
    '/sync/pull',
    summary='拉取任务同步事件（task cursor 之后）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_sync_pull',
)
async def pull_task_sync_events(
    request: Request,
    db: CurrentSyncSession,
    request_body: SyncPullRequest,
) -> SyncPullResponse:
    request_body.node_id = request_body.node_id or request.headers.get('X-Node-Id')
    require_owner_identity(request, request_body.owner_id)
    return await hasn_sync_service.pull_tasks(db, request_body, user_id=None)


@router.post(
    '/sync/push',
    summary='推送本地任务事件',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_sync_push',
)
async def push_task_sync_events(
    request: Request,
    db: CurrentSyncSessionTransaction,
    request_body: SyncPushRequest,
) -> SyncPushResponse:
    owner_id = require_owner_identity(request, request_body.owner_id)
    node_id = require_node_identity(request, request_body.node_id)
    envelopes: list[InboxEnvelope] = []
    rejected: list[ErrorObject] = []
    for event in request_body.events:
        # 每条拒绝都带上 client_event_id（hasn-node 实施/98），供 daemon 逐事件处置。
        if event.event_type not in TASK_SYNC_EVENTS:
            rejected.append(_rejected_for(_TASK_EVENT_UNSUPPORTED_ERROR, event.client_event_id))
            continue
        if _contains_private_runtime_key(event.payload):
            rejected.append(_rejected_for(_PRIVATE_METADATA_ERROR, event.client_event_id))
            continue
        payload_agent_id = event.payload.get('agent_id')
        subject_hasn_id = event.hasn_id
        if not subject_hasn_id and isinstance(payload_agent_id, str):
            subject_hasn_id = payload_agent_id
        envelopes.append(
            InboxEnvelope(
                owner_id=owner_id,
                node_id=node_id,
                client_event_id=event.client_event_id,
                hasn_id=subject_hasn_id or owner_id,
                event_type=event.event_type,
                payload=event.payload,
                dedupe_key=event.dedupe_key,
            )
        )
    result = await accept_envelopes(db, envelopes)
    for item in result.items:
        if item.status == 'conflict':
            rejected.append(_rejected_for(_TASK_CONFLICT_ERROR, item.client_event_id))
    return SyncPushResponse(
        accepted=result.accepted,
        rejected=rejected,
        next_cursor=f'owner:{owner_id}:task:0',
    )


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


@router.post(
    '/workflows:sync',
    summary='导入旧 daemon 工作流定义（create-only）',
    dependencies=[DependsJwtAuth],
    name='hasn_task_app_sync_workflow_definitions',
)
async def sync_workflow_definitions(
    request: Request,
    db: CurrentSessionTransaction,
    request_body: WorkflowDefinitionsSyncRequest,
) -> ResponseModel:
    """仅承接存量定义修复；新版场景实例化必须走模板 Owner 权威入口。"""
    owner_id = await current_owner_id(request, db)
    if request_body.owner_id and request_body.owner_id != owner_id:
        raise errors.ForbiddenError(msg='不能替其它主人导入工作流定义')
    created: list[str] = []
    idempotent: list[str] = []
    for item in request_body.definitions:
        result = await workflow_definition_import_service.import_one(
            db, owner_id=owner_id, workflow=item.workflow
        )
        target = created if result == 'created' else idempotent
        target.append(item.workflow.workflow_uuid or '')
    return response_base.success(data={'created': created, 'idempotent': idempotent})


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
