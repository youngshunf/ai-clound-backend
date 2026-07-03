"""hasn 同步纯编解码层 —— 命名空间允许表 / 私有键脱敏集 / cursor / 载荷序列化 / 强转（候选④ god-file 拆分 slice-1）。

从 `hasn_sync_service.py` 抽出的**纯逻辑 + 纯数据**（无 self / 无 db / 无 await）：游标解析与格式化、
task / session / memory / runtime 的存储行↔同步载荷互转、可选值强转、运行时摘要脱敏，以及它们依赖的
命名空间允许表 / 私有运行时键集 / 未知命名空间错误对象。`SqlAlchemySyncGateway` 与 `HasnSyncService`
仍按原名 import 这些符号，**零行为变化**；好处是这层纯逻辑可独立单测、god-file 主体随之收缩。
"""

from __future__ import annotations

import json
import uuid

from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any

from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.app.hasn.schema.hasn_sync import (
    ClientEvent,
    MemorySyncCursor,
    MemorySyncPullRequest,
    RuntimeSummary,
    SyncEventRecord,
    TaskRunSummaryRequest,
)
from backend.common.exception import errors
from backend.utils.timezone import timezone


class TaskSyncConflictError(Exception):
    """Raised when optimistic task revision conflict detection rejects an event."""


MEMORY_SYNC_SCOPE_KINDS = {'owner', 'agent'}

OWNER_MEMORY_NAMESPACES = {'portraits', 'facts', 'events', 'procedures', 'work_state', 'summaries', 'audits'}

AGENT_MEMORY_NAMESPACES = {
    'episodic',
    'agent_portraits',
    'agent_facts',
    'agent_events',
    'agent_procedures',
    'tasks',
    'extract_jobs',
}

PRIVATE_RUNTIME_KEYS = {
    'workspace',
    'workspace_path',
    'endpoint',
    'local_endpoint',
    'pid',
    'process_id',
    'cli_args',
    'oauth_path',
    'session_cache',
    'token',
    'access_token',
    'refresh_token',
    'raw_binding_metadata',
}

_MEMORY_NAMESPACE_UNKNOWN_ERROR = ErrorObject(
    code=8036,
    name='ERR_MEMORY_NAMESPACE_UNKNOWN',
    message='Memory sync payload references unknown namespace.',
)


def _parse_owner_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    parts = str(cursor).rsplit(':', maxsplit=1)
    try:
        return max(int(parts[-1]), 0)
    except (TypeError, ValueError) as exc:
        raise errors.RequestError(msg='ERR_SYNC_CURSOR_INVALID') from exc


def _owner_cursor(owner_id: str, revision: int) -> str:
    return f'owner:{owner_id}:{max(int(revision), 0)}'


def _parse_task_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    parts = str(cursor).rsplit(':', maxsplit=1)
    try:
        return max(int(parts[-1]), 0)
    except (TypeError, ValueError) as exc:
        raise errors.RequestError(msg='ERR_TASK_SYNC_CURSOR_INVALID') from exc


def _task_cursor(owner_id: str, revision: int) -> str:
    return f'owner:{owner_id}:task:{max(int(revision), 0)}'


def _task_payload_for_storage(owner_id: str, event: ClientEvent) -> dict[str, Any]:
    task_payload = event.payload.get('task')
    payload = dict(task_payload) if isinstance(task_payload, dict) else dict(event.payload)
    payload['owner_id'] = owner_id
    return payload


def _assert_task_revision_not_stale(
    event: ClientEvent,
    payload: dict[str, Any],
    current_task: dict[str, Any] | None,
) -> None:
    if current_task is None:
        return
    if event.event_type != 'task.deleted' and current_task.get('state') == 'deleted':
        raise TaskSyncConflictError
    base_revision = _optional_int(payload.get('base_revision'))
    if base_revision is None:
        return
    current_revision = _optional_int(current_task.get('task_revision')) or 0
    if base_revision < current_revision:
        raise TaskSyncConflictError


_VALID_RISK_LEVELS = {'low', 'high'}


def _normalize_risk_level(value: Any) -> str:
    """风险等级收敛为合法枚举（low/high），缺省/非法 → low（与列默认一致，KNOWU §4.5）。"""
    risk = str(value or '').strip().lower()
    return risk if risk in _VALID_RISK_LEVELS else 'low'


def _task_storage_row(
    owner_id: str,
    task_uuid: str,
    payload: dict[str, Any],
    event: ClientEvent,
    now: datetime,
) -> dict[str, Any]:
    timestamp = _coerce_datetime_or_none(payload.get('updated_at')) or now
    created_time = _coerce_datetime_or_none(payload.get('created_at')) or timestamp
    state = str(payload.get('state') or 'scheduled')
    if event.event_type == 'task.deleted':
        state = 'deleted'
    executor_node_id = _optional_string(payload.get('executor_node_id') or payload.get('node_id'))
    executor_policy = _optional_string(payload.get('executor_policy') or payload.get('executor_kind')) or 'local_node'
    return {
        'owner_id': owner_id,
        'agent_id': str(payload.get('agent_id') or event.hasn_id or ''),
        'name': str(payload.get('name') or ''),
        'description': _optional_string(payload.get('description')),
        'prompt': str(payload.get('prompt') or ''),
        'system_prompt': _optional_string(payload.get('system_prompt')),
        'input_template': _optional_string(payload.get('input_template')),
        'skill_bundle_ids': _coerce_list(payload.get('skill_bundle_ids')),
        'skill_bundle_refs': _coerce_list(payload.get('skill_bundle_refs')),
        'skill_ids': _coerce_list(payload.get('skill_ids')),
        'skill_refs': _coerce_list(payload.get('skill_refs')),
        'workflow_id': _optional_int(payload.get('workflow_id')),
        'workflow': _coerce_dict(payload.get('workflow')),
        'enabled_toolsets': _coerce_list_or_none(payload.get('enabled_toolsets')),
        'context_from_task_id': _optional_int(payload.get('context_from_task_id')),
        'schedule_type': str(payload.get('schedule_type') or 'once'),
        'schedule_config': _coerce_dict(payload.get('schedule_config')),
        'schedule_display': _optional_string(payload.get('schedule_display')),
        'risk_level': _normalize_risk_level(payload.get('risk_level')),
        'timezone': str(payload.get('timezone') or 'Asia/Shanghai'),
        'misfire_policy': str(payload.get('misfire_policy') or 'skip'),
        'catchup_limit': _optional_int(payload.get('catchup_limit')),
        'enabled': bool(payload.get('enabled', True)),
        'state': state,
        'next_run_at': _coerce_datetime_or_none(payload.get('next_run_at')),
        'run_count': _optional_int(payload.get('run_count')) or 0,
        'repeat_times': _optional_int(payload.get('repeat_times')),
        'repeat_completed': _optional_int(payload.get('repeat_completed')) or 0,
        'task_uuid': task_uuid,
        'executor_policy': executor_policy,
        'executor_node_id': executor_node_id,
        'binding_id': _optional_string(payload.get('binding_id')),
        'deleted_at': _coerce_datetime_or_none(payload.get('deleted_at')) if state == 'deleted' else None,
        'created_by': _optional_string(payload.get('created_by')),
        'continuation_enabled': bool(payload.get('continuation_enabled')),
        'enable_subagents': bool(payload.get('enable_subagents')),
        'created_by_kind': str(payload.get('created_by_kind') or 'owner'),
        'builtin_key': _optional_string(payload.get('builtin_key')),
        'builtin_synced_revision': _optional_int(payload.get('builtin_synced_revision')),
        'created_time': created_time,
        'updated_time': timestamp,
    }


def _task_sync_payload(
    task_uuid: str,
    stored_task: dict[str, Any],
    payload: dict[str, Any],
    event: ClientEvent,
) -> dict[str, Any]:
    return {
        'task_id': task_uuid,
        'server_id': payload.get('server_id'),
        'owner_id': stored_task['owner_id'],
        'agent_id': stored_task['agent_id'],
        'name': stored_task['name'],
        'description': stored_task['description'],
        'prompt': stored_task['prompt'],
        'system_prompt': stored_task['system_prompt'],
        'skill_bundle_ids': stored_task['skill_bundle_ids'],
        'skill_bundle_refs': stored_task['skill_bundle_refs'],
        'skill_ids': stored_task['skill_ids'],
        'schedule_type': stored_task['schedule_type'],
        'schedule_config': stored_task['schedule_config'],
        'schedule_display': stored_task['schedule_display'],
        'risk_level': stored_task.get('risk_level', 'low'),
        'enabled': stored_task['enabled'],
        'state': stored_task['state'],
        'continuation_enabled': stored_task['continuation_enabled'],
        'enable_subagents': stored_task['enable_subagents'],
        'created_by_kind': stored_task['created_by_kind'],
        'builtin_key': stored_task.get('builtin_key'),
        'builtin_synced_revision': stored_task.get('builtin_synced_revision'),
        'next_run_at': payload.get('next_run_at'),
        'run_count': stored_task['run_count'],
        'repeat_times': stored_task['repeat_times'],
        'repeat_completed': stored_task['repeat_completed'],
        'sync_status': payload.get('sync_status') or 'synced',
        'created_at': payload.get('created_at'),
        'updated_at': payload.get('updated_at'),
        'deleted_at': payload.get('deleted_at') if event.event_type == 'task.deleted' else None,
    }


def _task_sync_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    task_uuid = str(row.get('task_uuid') or '')
    return {
        'task_id': task_uuid,
        'task_uuid': task_uuid,
        'server_id': row.get('server_id'),
        'owner_id': str(row.get('owner_id') or ''),
        'agent_id': str(row.get('agent_id') or ''),
        'name': str(row.get('name') or ''),
        'description': row.get('description'),
        'prompt': str(row.get('prompt') or ''),
        'system_prompt': row.get('system_prompt'),
        'skill_bundle_ids': _coerce_list(row.get('skill_bundle_ids')),
        'skill_bundle_refs': _coerce_list(row.get('skill_bundle_refs')),
        'skill_ids': _coerce_list(row.get('skill_ids')),
        'schedule_type': str(row.get('schedule_type') or 'once'),
        'schedule_config': _coerce_dict(row.get('schedule_config')),
        'schedule_display': row.get('schedule_display'),
        'risk_level': _normalize_risk_level(row.get('risk_level')),
        'enabled': bool(row.get('enabled', True)),
        'state': str(row.get('state') or 'scheduled'),
        'continuation_enabled': bool(row.get('continuation_enabled')),
        'enable_subagents': bool(row.get('enable_subagents')),
        'created_by_kind': str(row.get('created_by_kind') or 'owner'),
        'builtin_key': row.get('builtin_key'),
        'builtin_synced_revision': _optional_int(row.get('builtin_synced_revision')),
        'next_run_at': _timestamp_or_original(row.get('next_run_at')),
        'run_count': _optional_int(row.get('run_count')) or 0,
        'repeat_times': _optional_int(row.get('repeat_times')),
        'repeat_completed': _optional_int(row.get('repeat_completed')) or 0,
        'sync_status': 'synced',
        'created_at': _timestamp_or_original(row.get('created_time')),
        'updated_at': _timestamp_or_original(row.get('updated_time')),
    }


def _task_assignment_event_payload(
    task: dict[str, Any],
    assignment: dict[str, Any],
    old_node_id: str,
) -> dict[str, Any]:
    return {
        **_task_sync_payload_from_row(task),
        'executor_policy': assignment['executor_kind'],
        'executor_kind': assignment['executor_kind'],
        'executor_node_id': assignment['executor_node_id'],
        'binding_id': assignment['binding_id'],
        'assignment_state': assignment['assignment_state'],
        'previous_executor_node_id': old_node_id or None,
        'visible_node_ids': [assignment['executor_node_id']] if assignment['executor_node_id'] else [],
    }


def _assignment_from_runtime_report(report: dict[str, Any]) -> dict[str, Any]:
    dispatchable = (
        report.get('runtime_status') == 'online'
        and bool(report.get('adapter_registered', True))
        and bool(report.get('handle_available', True))
        and bool(report.get('node_id'))
    )
    if not dispatchable:
        return {
            'executor_kind': 'unresolved',
            'executor_node_id': '',
            'binding_id': report.get('binding_id'),
            'assignment_state': 'unresolved',
            'resolved_at': report.get('reported_at') or timezone.now(),
        }
    return {
        'executor_kind': 'cloud_runtime_host' if _runtime_report_is_cloud_host(report) else 'local_node',
        'executor_node_id': str(report.get('node_id') or ''),
        'binding_id': report.get('binding_id'),
        'assignment_state': 'assigned',
        'resolved_at': report.get('reported_at') or timezone.now(),
    }


def _runtime_report_is_cloud_host(report: dict[str, Any]) -> bool:
    summary = _coerce_dict(report.get('summary_json'))
    runtime_type = str(report.get('runtime_type') or '').lower()
    runtime_host = str(summary.get('runtime_host') or summary.get('host_kind') or '').lower()
    return (
        runtime_type in {'cloud_runtime_host', 'cloud_hermes', 'cloud_sdk'}
        or runtime_host in {'cloud', 'cloud_runtime_host'}
        or bool(summary.get('cloud_runtime_host'))
        or bool(summary.get('is_cloud_runtime_host'))
    )


def _task_run_summary_for_storage(
    request: TaskRunSummaryRequest,
    *,
    owner_id: str,
    agent_hasn_id: str,
) -> dict[str, Any]:
    task_uuid = str(request.task_uuid or request.task_id or '')
    run_uuid = str(request.run_uuid or request.run_id or request.task_run_id or uuid.uuid4())
    dedupe_key = str(request.dedupe_key or run_uuid)
    return {
        'run_uuid': run_uuid,
        'task_uuid': task_uuid,
        'owner_id': owner_id,
        'agent_id': agent_hasn_id,
        'executor_node_id': request.executor_node_id,
        'session_id': request.session_id,
        'scheduled_fire_at': _coerce_datetime_or_none(request.scheduled_fire_at),
        'dedupe_key': dedupe_key,
        'status': request.status,
        'output_summary': request.output_summary if request.output_summary is not None else request.output,
        'error': request.error,
        'deep_link': request.deep_link,
        'model': request.model,
        'token_usage': request.token_usage,
        'duration_ms': request.duration_ms,
        'started_at': _coerce_datetime_or_none(request.started_at),
        'finished_at': _coerce_datetime_or_none(request.finished_at),
    }


def _task_run_summary_event_payload(summary: dict[str, Any]) -> dict[str, Any]:
    response = _task_run_summary_response_payload(summary)
    return {
        'owner_id': response['owner_id'],
        'agent_id': response['agent_id'],
        'task_id': response['task_uuid'],
        'task_uuid': response['task_uuid'],
        'run_uuid': response['run_uuid'],
        'dedupe_key': response['dedupe_key'],
        'status': response['status'],
        'output_summary': response['output_summary'],
        'error': response['error'],
        'deep_link': response['deep_link'],
    }


def _task_run_summary_response_payload(summary: dict[str, Any]) -> dict[str, Any]:
    token_usage = summary.get('token_usage')
    if isinstance(token_usage, str):
        try:
            token_usage = json.loads(token_usage)
        except json.JSONDecodeError:
            token_usage = None
    return {
        'run_uuid': str(summary.get('run_uuid') or ''),
        'task_uuid': str(summary.get('task_uuid') or summary.get('task_id') or ''),
        'owner_id': str(summary.get('owner_id') or ''),
        'agent_id': str(summary.get('agent_id') or ''),
        'session_id': summary.get('session_id'),
        'dedupe_key': str(summary.get('dedupe_key') or ''),
        'status': str(summary.get('status') or ''),
        'output_summary': summary.get('output_summary') if summary.get('output_summary') is not None else summary.get('output'),
        'error': summary.get('error'),
        'deep_link': summary.get('deep_link'),
        'model': summary.get('model'),
        'token_usage': token_usage if isinstance(token_usage, dict) else None,
        'duration_ms': summary.get('duration_ms'),
    }


def _required_string(payload: dict[str, Any], key: str, error_name: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise errors.RequestError(msg=error_name)
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _coerce_list_or_none(value: Any) -> list[Any] | None:
    if value is None:
        return None
    return _coerce_list(value)


def _coerce_datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=datetime_timezone.utc)
    if isinstance(value, str):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _timestamp_or_original(value: Any) -> Any:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return value


def _memory_aggregate_id(event: ClientEvent) -> str:
    record_id = event.payload.get('record_id')
    namespace = event.payload.get('namespace')
    sync_scope_kind = event.payload.get('sync_scope_kind')
    sync_scope_id = event.payload.get('sync_scope_id')
    if record_id:
        return str(record_id)
    if namespace and sync_scope_kind and sync_scope_id:
        return f'{sync_scope_kind}:{sync_scope_id}:{namespace}'
    return event.client_event_id


def _memory_pull_selections(request: MemorySyncPullRequest) -> list[MemorySyncCursor]:
    namespace_map: dict[str, list[str]] = {}
    for selector in request.namespaces:
        namespace_map.setdefault(selector.sync_scope_kind, [])
        for namespace in selector.names:
            if not _memory_namespace_allowed(selector.sync_scope_kind, namespace):
                raise errors.RequestError(
                    msg=_MEMORY_NAMESPACE_UNKNOWN_ERROR.name,
                    data=_MEMORY_NAMESPACE_UNKNOWN_ERROR.model_dump(),
                )
            if namespace not in namespace_map[selector.sync_scope_kind]:
                namespace_map[selector.sync_scope_kind].append(namespace)

    cursor_map = {
        (cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace): cursor.last_pulled_revision
        for cursor in request.cursors
    }
    selections: list[MemorySyncCursor] = [MemorySyncCursor(
                sync_scope_kind='owner',
                sync_scope_id=request.owner_id,
                namespace=namespace,
                last_pulled_revision=cursor_map.get(('owner', request.owner_id, namespace), 0),
            ) for namespace in namespace_map.get('owner', [])]
    for agent_id in request.agent_ids:
        selections.extend(MemorySyncCursor(
                    sync_scope_kind='agent',
                    sync_scope_id=agent_id,
                    namespace=namespace,
                    last_pulled_revision=cursor_map.get(('agent', agent_id, namespace), 0),
                ) for namespace in namespace_map.get('agent', []))
    return selections


def _advance_memory_cursors(
    selections: list[MemorySyncCursor], events: list[SyncEventRecord]
) -> list[MemorySyncCursor]:
    revisions = {
        (cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace): cursor.last_pulled_revision
        for cursor in selections
    }
    for event in events:
        sync_scope_kind = event.payload.get('sync_scope_kind')
        sync_scope_id = event.payload.get('sync_scope_id')
        namespace = event.payload.get('namespace')
        namespace_revision = event.payload.get('namespace_revision')
        key = (sync_scope_kind, sync_scope_id, namespace)
        if key in revisions and isinstance(namespace_revision, int):
            revisions[key] = max(revisions[key], namespace_revision)

    return [
        MemorySyncCursor(
            sync_scope_kind=cursor.sync_scope_kind,
            sync_scope_id=cursor.sync_scope_id,
            namespace=cursor.namespace,
            last_pulled_revision=revisions[cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace],
        )
        for cursor in selections
    ]


def _memory_namespace_revision_key(event: ClientEvent) -> tuple[str, str, str]:
    sync_scope_kind = _required_memory_payload_string(event, 'sync_scope_kind')
    sync_scope_id = _required_memory_payload_string(event, 'sync_scope_id')
    namespace = _required_memory_payload_string(event, 'namespace')
    if sync_scope_kind not in MEMORY_SYNC_SCOPE_KINDS:
        raise errors.RequestError(msg='ERR_MEMORY_SYNC_SCOPE_INVALID')
    return sync_scope_kind, sync_scope_id, namespace


def _memory_namespace_allowed(sync_scope_kind: str, namespace: str) -> bool:
    if sync_scope_kind == 'owner':
        return namespace in OWNER_MEMORY_NAMESPACES
    if sync_scope_kind == 'agent':
        return namespace in AGENT_MEMORY_NAMESPACES
    return False


def _required_memory_payload_string(event: ClientEvent, key: str) -> str:
    value = event.payload.get(key)
    if not isinstance(value, str) or not value:
        raise errors.RequestError(msg='ERR_MEMORY_SYNC_SCOPE_INVALID')
    return value


def _report_id(owner_id: str, node_id: str, summary: RuntimeSummary) -> str:
    stable = uuid.uuid5(uuid.NAMESPACE_URL, f'hasn:runtime-report:{owner_id}:{node_id}:{summary.agent_id}')
    return f'rr_{stable.hex[:24]}'


def _runtime_status_for_storage(status: str) -> str:
    if status == 'missing':
        return 'unavailable'
    if status == 'failed':
        return 'error'
    return status


def _contains_private_runtime_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in PRIVATE_RUNTIME_KEYS:
                return True
            if _contains_private_runtime_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_private_runtime_key(item) for item in value)
    return False


def _redact_runtime_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(summary).items()
        if str(key).lower() not in PRIVATE_RUNTIME_KEYS
    }


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return timezone.now()


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
