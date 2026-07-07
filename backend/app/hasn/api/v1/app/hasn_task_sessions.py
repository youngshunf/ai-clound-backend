"""任务系统 Session API

用于任务系统的 Session 管理，包括：
- Session 创建/更新（upsert）
- Session 摘要更新
- Session 关闭
- Session 消息查询和发送
"""

import hashlib

from typing import Annotated, Any

import sqlalchemy as sa

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.model import HasnAgents
from backend.app.hasn.service.hasn_sessions_service import hasn_sessions_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()
work_sessions_router = APIRouter()


async def _current_owner_id(request: Request, db: CurrentSession) -> str:
    owner_id = getattr(request.user, 'hasn_id', None)
    if owner_id:
        return owner_id
    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if not hasn_human:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return hasn_human.hasn_id


class SessionUpsertRequest(BaseModel):
    """Session Upsert 请求"""

    session_id: str = Field(..., description='Session ID (ULID)')
    conversation_id: str | None = Field(None, description='会话 ID')
    owner_id: str = Field(..., description='Owner ID')
    hasn_id: str = Field(..., description='Agent ID')
    session_kind: str = Field(..., description='Session 类型')
    session_scope: str = Field(..., description='同步范围')
    session_status: str = Field(default='active', description='Session 状态')
    parent_session_id: str | None = Field(None, description='父 Session ID')
    continuation_from_session_id: str | None = Field(None, description='续接自哪个 Session')
    origin_type: str = Field(..., description='来源类型')
    origin_ref: str | None = Field(None, description='来源引用')
    title: str | None = Field(None, description='Session 标题')
    summary_checkpoint_json: dict | None = Field(default={}, description='摘要快照')
    active_binding_id: str | None = Field(None, description='当前活跃的绑定 ID')


class SessionSummaryUpdateRequest(BaseModel):
    """Session 摘要更新请求"""

    summary_checkpoint_json: dict = Field(..., description='摘要快照')
    last_message_at: str | None = Field(None, description='最后消息时间')


class SessionCloseRequest(BaseModel):
    """Session 关闭请求"""

    session_status: str = Field(default='completed', description='关闭状态')


class SessionMessageSendRequest(BaseModel):
    """Session 消息发送请求"""

    content_text: str = Field(..., description='消息内容')
    client_message_id: str = Field(..., description='客户端消息 ID')


class SessionProjectionRequest(BaseModel):
    """工作会话结果投影请求"""

    summary: str = Field(..., description='结果摘要')
    status: str = Field(default='success', description='结果状态')
    completion_reason: str = Field(..., description='完成原因')
    deep_link: str | None = Field(None, description='回跳链接')
    agent_id: str | None = Field(None, description='Agent ID')
    target_conversation_id: str | None = Field(None, description='目标会话 ID')
    source_conversation_id: str | None = Field(None, description='来源会话 ID')
    task_id: int | None = Field(None, description='任务 ID')
    task_run_id: int | None = Field(None, description='任务执行 ID')
    workflow_run_id: str | None = Field(None, description='Workflow Run ID')
    external_app_id: str | None = Field(None, description='外部 App ID')
    milestone_id: str | None = Field(None, description='阶段性投影 ID')


class ExternalProjectionPolicy(BaseModel):
    """外部 APP 可提交的投影策略白名单。"""

    model_config = ConfigDict(extra='forbid')

    project_summary_to_owner_conversation: bool = Field(default=True)
    target_conversation_id: str | None = Field(default=None)


class ExternalWorkSessionLaunchRequest(BaseModel):
    """外部 APP 发起工作会话的字段白名单。"""

    model_config = ConfigDict(extra='forbid')

    external_app_id: str = Field(..., min_length=1)
    external_trace_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    title: str | None = Field(default=None)
    task_description: str = Field(..., min_length=1)
    system_prompt: str | None = Field(default=None)
    skill_ids: list[str] = Field(default_factory=list)
    skill_bundle_ids: list[str] = Field(default_factory=list)
    enabled_toolsets: dict[str, bool] | None = Field(default=None)
    workflow: dict[str, Any] | None = Field(default=None)
    projection_policy: ExternalProjectionPolicy = Field(default_factory=ExternalProjectionPolicy)


class ExternalWorkSessionControlRequest(BaseModel):
    """外部 APP 请求控制工作会话状态。"""

    model_config = ConfigDict(extra='forbid')

    reason: str | None = Field(default=None)
    summary: str | None = Field(default=None)


@router.post(
    '/sessions/upsert',
    summary='创建或更新 Session',
    dependencies=[DependsJwtAuth],
    name='hasn_session_upsert',
)
async def session_upsert(
    request: Request,
    db: CurrentSessionTransaction,
    obj: SessionUpsertRequest,
) -> ResponseModel:
    """创建或更新 Session（幂等操作）"""
    owner_id = await _current_owner_id(request, db)
    session = await hasn_sessions_service.upsert(
        db=db,
        owner_id=owner_id,
        session_data=obj.model_dump(),
    )
    return response_base.success(data={'session_id': session.session_id})


@router.post(
    '/sessions/{session_id}/summary',
    summary='更新 Session 摘要',
    dependencies=[DependsJwtAuth],
    name='hasn_session_update_summary',
)
async def session_update_summary(
    request: Request,
    db: CurrentSessionTransaction,
    session_id: Annotated[str, Path(description='Session ID')],
    obj: SessionSummaryUpdateRequest,
) -> ResponseModel:
    """更新 Session 摘要"""
    owner_id = await _current_owner_id(request, db)
    session = await hasn_sessions_service.update_summary(
        db=db, owner_id=owner_id, session_id=session_id, summary_data=obj.model_dump()
    )
    return response_base.success(data={'session_id': session.session_id})


@router.post(
    '/sessions/{session_id}/close',
    summary='关闭 Session',
    dependencies=[DependsJwtAuth],
    name='hasn_session_close',
)
async def session_close(
    request: Request,
    db: CurrentSessionTransaction,
    session_id: Annotated[str, Path(description='Session ID')],
    obj: SessionCloseRequest,
) -> ResponseModel:
    """关闭 Session"""
    owner_id = await _current_owner_id(request, db)
    session = await hasn_sessions_service.close_session(
        db=db, owner_id=owner_id, session_id=session_id, close_data=obj.model_dump()
    )
    return response_base.success(data={'session_id': session.session_id})


@router.get(
    '/sessions',
    summary='查询 Session 列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_session_list',
)
async def session_list(
    request: Request,
    db: CurrentSession,
    session_kind: Annotated[str | None, Query(description='Session 类型，逗号分隔')] = None,
    session_scope: Annotated[str | None, Query(description='同步范围')] = None,
    session_status: Annotated[str | None, Query(description='Session 状态')] = None,
    hasn_id: Annotated[str | None, Query(description='Agent ID')] = None,
    origin_type: Annotated[str | None, Query(description='来源类型')] = None,
    origin_ref: Annotated[str | None, Query(description='来源引用')] = None,
) -> ResponseModel:
    """查询 Session 列表"""
    owner_id = await _current_owner_id(request, db)
    page_data = await hasn_sessions_service.get_list_by_owner(
        db=db,
        owner_id=owner_id,
        session_kind=session_kind,
        session_scope=session_scope,
        session_status=session_status,
        hasn_id=hasn_id,
        origin_type=origin_type,
        origin_ref=origin_ref,
    )
    return response_base.success(data=page_data)


@router.get(
    '/sessions/{session_id}/messages',
    summary='查询 Session 消息列表',
    dependencies=[DependsJwtAuth],
    name='hasn_session_messages',
)
async def session_messages(
    request: Request,
    db: CurrentSession,
    session_id: Annotated[str, Path(description='Session ID')],
    page: Annotated[int, Query(description='页码', ge=1)] = 1,
    page_size: Annotated[int, Query(description='每页数量', ge=1, le=100)] = 50,
) -> ResponseModel:
    """查询 Session 消息列表"""
    owner_id = await _current_owner_id(request, db)
    data = await hasn_sessions_service.list_messages(
        db=db,
        owner_id=owner_id,
        session_id=session_id,
        page=page,
        page_size=page_size,
    )
    return response_base.success(data=data)


@router.post(
    '/sessions/{session_id}/projection',
    summary='投影工作会话结果摘要',
    dependencies=[DependsJwtAuth],
    name='hasn_session_project_result',
)
async def session_project_result(
    request: Request,
    db: CurrentSessionTransaction,
    session_id: Annotated[str, Path(description='Session ID')],
    obj: SessionProjectionRequest,
) -> ResponseModel:
    """幂等写入工作会话结果摘要消息。"""
    owner_id = await _current_owner_id(request, db)
    result = await hasn_sessions_service.project_work_session_result(
        db=db,
        owner_id=owner_id,
        session_id=session_id,
        projection_data=obj.model_dump(),
    )
    return response_base.success(data=result)


@router.post(
    '/sessions/{session_id}/messages',
    summary='发送消息到 Session',
    dependencies=[DependsJwtAuth],
    name='hasn_session_send_message',
)
async def session_send_message(
    request: Request,
    db: CurrentSessionTransaction,
    session_id: Annotated[str, Path(description='Session ID')],
    obj: SessionMessageSendRequest,
) -> ResponseModel:
    """发送消息到 Session"""
    raise errors.RequestError(msg='Session 输入必须路由到 hasn-node，本云端接口不返回占位 message_id')


@router.get(
    '/work-session-summaries',
    summary='列 owner 名下所有设备的工作会话摘要（主会话跨会话感知）',
    dependencies=[DependsJwtAuth],
    name='hasn_work_session_summaries_list',
)
async def work_session_summaries_list(
    request: Request,
    db: CurrentSession,
    limit: Annotated[int, Query(description='数量上限', ge=1, le=100)] = 20,
) -> ResponseModel:
    """owner-scoped 只读：列 owner 名下（含**跑在别的设备**）的工作会话摘要。

    doc13 决策 D 跨设备读端点——daemon 归并进主会话「最近会话」digest 时，用它补齐
    「活儿跑在另一台设备」的工作会话摘要（本地无该 session 行）。owner 隔离。
    """
    owner_id = await _current_owner_id(request, db)
    items = await hasn_sessions_service.list_work_session_summaries(db=db, owner_id=owner_id, limit=limit)
    return response_base.success(data={'items': items})


@router.get(
    '/work-session-summaries/{session_id}',
    summary='取单个工作会话云端摘要（跨设备下钻）',
    dependencies=[DependsJwtAuth],
    name='hasn_work_session_summary_get',
)
async def work_session_summary_get(
    request: Request,
    db: CurrentSession,
    session_id: Annotated[str, Path(description='Session ID')],
) -> ResponseModel:
    """owner-scoped 只读：取单个工作会话的云端摘要（跨设备下钻，无逐条 events）。"""
    owner_id = await _current_owner_id(request, db)
    data = await hasn_sessions_service.get_work_session_summary(db=db, owner_id=owner_id, session_id=session_id)
    if data is None:
        raise errors.NotFoundError(msg='工作会话摘要不存在')
    return response_base.success(data=data)


@work_sessions_router.post(
    '/work-sessions',
    summary='外部 APP 发起工作会话',
    dependencies=[DependsJwtAuth],
    name='hasn_external_work_session_launch',
)
async def external_work_session_launch(
    request: Request,
    db: CurrentSessionTransaction,
    obj: ExternalWorkSessionLaunchRequest,
) -> ResponseModel:
    """校验外部 APP 请求并创建云端 summary-only session header。"""
    owner_id = await _current_owner_id(request, db)
    payload = obj.model_dump()
    _reject_forbidden_launch_values(payload)
    _assert_external_launch_authorized(request=request, obj=obj)
    await _assert_owned_agent(db=db, owner_id=owner_id, agent_id=obj.agent_id)

    session_id = _external_session_id(
        owner_id=owner_id,
        agent_id=obj.agent_id,
        external_app_id=obj.external_app_id,
        external_trace_id=obj.external_trace_id,
    )
    deep_link = f'/tasks/sessions/{session_id}'
    origin_ref = f'{obj.external_app_id}:{obj.external_trace_id}'
    projection_policy = obj.projection_policy.model_dump(exclude_none=True)
    project_summary = bool(projection_policy.get('project_summary_to_owner_conversation', True))
    launch_spec = _external_launch_spec(
        owner_id=owner_id,
        session_id=session_id,
        origin_ref=origin_ref,
        deep_link=deep_link,
        obj=obj,
        projection_policy=projection_policy,
        project_summary=project_summary,
    )

    session = await hasn_sessions_service.upsert(
        db=db,
        owner_id=owner_id,
        session_data={
            'session_id': session_id,
            'conversation_id': None,
            'owner_id': owner_id,
            'hasn_id': obj.agent_id,
            'session_kind': 'task',
            'session_scope': 'summary_only',
            'session_status': 'active',
            'parent_session_id': None,
            'continuation_from_session_id': None,
            'origin_type': 'external_app',
            'origin_ref': origin_ref,
            'title': obj.title,
            'summary_checkpoint_json': {
                'summary': obj.task_description,
                'external_app_id': obj.external_app_id,
                'external_trace_id': obj.external_trace_id,
                'deep_link': deep_link,
            },
            'active_binding_id': None,
        },
    )
    return response_base.success(
        data={
            'accepted': True,
            'session_id': session.session_id,
            'deep_link': deep_link,
            'launch_spec': launch_spec,
        }
    )


@work_sessions_router.get(
    '/work-sessions/{session_id}',
    summary='查询外部 APP 工作会话摘要',
    dependencies=[DependsJwtAuth],
    name='hasn_external_work_session_get',
)
async def external_work_session_get(
    request: Request,
    db: CurrentSession,
    session_id: Annotated[str, Path(description='Session ID')],
) -> ResponseModel:
    owner_id = await _current_owner_id(request, db)
    session = await hasn_sessions_service.get_by_session_id(
        db=db,
        owner_id=owner_id,
        session_id=session_id,
    )
    if session.session_kind != 'task' or session.origin_type != 'external_app':
        raise errors.NotFoundError(msg='外部工作会话不存在')
    return response_base.success(data=_external_session_summary(session))


@work_sessions_router.post(
    '/work-sessions/{session_id}/complete',
    summary='外部 APP 请求完成工作会话',
    dependencies=[DependsJwtAuth],
    name='hasn_external_work_session_complete',
)
async def external_work_session_complete(
    request: Request,
    db: CurrentSessionTransaction,
    session_id: Annotated[str, Path(description='Session ID')],
    obj: ExternalWorkSessionControlRequest,
) -> ResponseModel:
    owner_id = await _current_owner_id(request, db)
    session = await hasn_sessions_service.get_by_session_id(
        db=db,
        owner_id=owner_id,
        session_id=session_id,
    )
    if session.session_kind != 'task' or session.origin_type != 'external_app':
        raise errors.NotFoundError(msg='外部工作会话不存在')
    checkpoint = dict(session.summary_checkpoint_json or {})
    checkpoint['external_control_requested'] = 'complete'
    if obj.reason:
        checkpoint['external_control_reason'] = obj.reason
    if obj.summary:
        checkpoint['summary'] = obj.summary
    session.summary_checkpoint_json = checkpoint
    return response_base.success(data={'accepted': True, 'session_id': session.session_id, 'control': 'complete'})


@work_sessions_router.post(
    '/work-sessions/{session_id}/cancel',
    summary='外部 APP 请求取消工作会话',
    dependencies=[DependsJwtAuth],
    name='hasn_external_work_session_cancel',
)
async def external_work_session_cancel(
    request: Request,
    db: CurrentSessionTransaction,
    session_id: Annotated[str, Path(description='Session ID')],
    obj: ExternalWorkSessionControlRequest,
) -> ResponseModel:
    owner_id = await _current_owner_id(request, db)
    session = await hasn_sessions_service.get_by_session_id(
        db=db,
        owner_id=owner_id,
        session_id=session_id,
    )
    if session.session_kind != 'task' or session.origin_type != 'external_app':
        raise errors.NotFoundError(msg='外部工作会话不存在')
    checkpoint = dict(session.summary_checkpoint_json or {})
    checkpoint['external_control_requested'] = 'cancel'
    if obj.reason:
        checkpoint['external_control_reason'] = obj.reason
    session.summary_checkpoint_json = checkpoint
    return response_base.success(data={'accepted': True, 'session_id': session.session_id, 'control': 'cancel'})


async def _assert_owned_agent(*, db: CurrentSession, owner_id: str, agent_id: str) -> None:
    agent = (
        await db.execute(
            sa.select(HasnAgents).where(
                HasnAgents.hasn_id == agent_id,
                HasnAgents.owner_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    if not agent:
        raise errors.NotFoundError(msg='Agent 不存在或不属于当前 owner')
    if getattr(agent, 'deleted_at', None) is not None or getattr(agent, 'status', 'active') in {
        'disabled',
        'revoked',
        'archived',
        'deleted',
    }:
        raise errors.ForbiddenError(msg='Agent 不可接收外部工作会话')


def _external_session_id(
    *,
    owner_id: str,
    agent_id: str,
    external_app_id: str,
    external_trace_id: str,
) -> str:
    raw = f'{owner_id}:{agent_id}:{external_app_id}:{external_trace_id}'.encode()
    return f'sess_ext_{hashlib.sha256(raw).hexdigest()[:24]}'


def _external_launch_spec(
    *,
    owner_id: str,
    session_id: str,
    origin_ref: str,
    deep_link: str,
    obj: ExternalWorkSessionLaunchRequest,
    projection_policy: dict[str, Any],
    project_summary: bool,
) -> dict[str, Any]:
    return {
        'session_id': session_id,
        'owner_id': owner_id,
        'agent_id': obj.agent_id,
        'origin_type': 'external_app',
        'origin_ref': origin_ref,
        'title': obj.title,
        'task_description': obj.task_description,
        'system_prompt': obj.system_prompt,
        'skill_ids': obj.skill_ids,
        'skill_bundle_ids': obj.skill_bundle_ids,
        'enabled_toolsets': obj.enabled_toolsets,
        'workflow': obj.workflow,
        'source': {
            'external_app_id': obj.external_app_id,
            'external_trace_id': obj.external_trace_id,
        },
        'projection_policy': projection_policy,
        'completion_policy': {
            'mode': 'external_controlled',
            'project_on_complete': project_summary,
            'require_user_confirmation': True,
        },
    }


def _external_session_summary(session: Any) -> dict[str, Any]:
    return {
        'session_id': session.session_id,
        'owner_id': session.owner_id,
        'agent_id': session.hasn_id,
        'origin_type': session.origin_type,
        'origin_ref': session.origin_ref,
        'title': session.title,
        'status': session.session_status,
        'summary': session.summary_checkpoint_json or {},
        'deep_link': (session.summary_checkpoint_json or {}).get('deep_link'),
    }


def _assert_external_launch_authorized(*, request: Request, obj: ExternalWorkSessionLaunchRequest) -> None:
    permissions = _external_launch_permissions(request)

    if obj.system_prompt and not _permission_enabled(
        permissions,
        'work_sessions.allow_system_prompt',
        'allow_system_prompt',
    ):
        raise errors.ForbiddenError(msg='system_prompt 未授权')

    _assert_authorized_values(
        'skill_ids',
        obj.skill_ids,
        _permission_values(permissions, 'work_sessions.skill_ids', 'skill_ids'),
    )
    _assert_authorized_values(
        'skill_bundle_ids',
        obj.skill_bundle_ids,
        _permission_values(permissions, 'work_sessions.skill_bundle_ids', 'skill_bundle_ids'),
    )

    enabled_toolsets = [key for key, enabled in (obj.enabled_toolsets or {}).items() if enabled]
    _assert_authorized_values(
        'toolsets',
        enabled_toolsets,
        _permission_values(permissions, 'work_sessions.toolsets', 'toolsets'),
    )

    workflow = obj.workflow or {}
    if not workflow:
        return

    allow_inline_workflow = _permission_enabled(
        permissions,
        'work_sessions.allow_inline_workflow',
        'allow_inline_workflow',
    )
    workflow_id = workflow.get('workflow_id')
    if workflow_id:
        _assert_authorized_values(
            'workflow_ids',
            [str(workflow_id)],
            _permission_values(permissions, 'work_sessions.workflow_ids', 'workflow_ids'),
        )
        inline_keys = set(workflow) - {'workflow_id', 'workflow_run_id'}
        if inline_keys and not allow_inline_workflow:
            raise errors.ForbiddenError(msg=f'workflow 未授权字段: {", ".join(sorted(inline_keys))}')
        return

    if not allow_inline_workflow:
        raise errors.ForbiddenError(msg='workflow 未授权')


def _external_launch_permissions(request: Request) -> dict[str, Any]:
    for key in ('external_app_permissions', 'owner_api_key_scopes', 'auth_scopes'):
        permissions = _work_session_permissions(request.scope.get(key))
        if permissions is not None:
            return permissions
    return {}


def _work_session_permissions(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    nested = value.get('work_sessions')
    if isinstance(nested, dict):
        return nested
    return value


def _assert_authorized_values(field: str, submitted: list[str], allowed: set[str]) -> None:
    submitted_values = {str(value) for value in submitted if str(value)}
    if not submitted_values or '*' in allowed:
        return
    unauthorized = submitted_values - allowed
    if unauthorized:
        raise errors.ForbiddenError(msg=f'{field} 未授权: {", ".join(sorted(unauthorized))}')


def _permission_values(permissions: dict[str, Any], *keys: str) -> set[str]:
    for key in keys:
        value = _permission_value(permissions, key)
        normalized = _normalize_permission_values(value)
        if normalized is not None:
            return normalized
    return set()


def _normalize_permission_values(value: Any) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {str(key) for key, enabled in value.items() if enabled is True}
    if isinstance(value, list | tuple | set):
        return {str(item) for item in value}
    return set()


def _permission_enabled(permissions: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = _permission_value(permissions, key)
        if value is not None:
            return value is True
    return False


def _permission_value(permissions: dict[str, Any], key: str) -> Any:
    if key in permissions:
        return permissions[key]
    current: Any = permissions
    for part in key.split('.'):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _reject_forbidden_launch_values(value: Any, path: str = '') -> None:
    forbidden_keys = ('credential', 'token', 'password', 'secret', 'local_path')
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f'{path}.{key}' if path else str(key)
            if any(part in str(key).lower() for part in forbidden_keys):
                raise errors.RequestError(msg=f'字段不允许进入云端明文 payload: {key_path}')
            _reject_forbidden_launch_values(child, key_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_launch_values(child, f'{path}[{index}]')
