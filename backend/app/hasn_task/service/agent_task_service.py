"""Agent 任务能力面 service（云端补线，设计 06 §5）。

所有写操作复用 v2.1 任务同步引擎的 ``save_task_event`` 事件通道（node_id=cloud-agent-api）：
- 权威 upsert（``hasn_task.task``）+ revision bump + ``hasn_sync_events`` feed 下发 + client_event_id 幂等
- 各节点经 ``/sync/pull`` 拉到 ``task.created/updated/deleted`` 事件镜像到本地（中心仍不 tick）

业务规则（D4/R2/R4，与 daemon Broker/M4 同一套语义）：
- D4：agent 建 ``once`` → ``scheduled``；agent 建 ``interval/cron`` → ``pending_approval`` + 提醒卡片
- R2：agent 改调度类字段（schedule_type/schedule_config/timezone）且结果为周期 → 回 ``pending_approval``；
      ``rejected`` 拒绝任何写；``run_now`` 仅 scheduled/paused；``resume`` 仅 paused
- R4：create 幂等键 (owner, agent, name, schedule_type, schedule_config) 短窗口去重 + 频控 10/小时
"""

from __future__ import annotations

import json
import uuid

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn_task.service.task_service import calc_next_run_at
from backend.app.notification.service.notification_service import notification_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

CLOUD_AGENT_NODE_ID = 'cloud-agent-api'
IDEMPOTENCY_WINDOW = timedelta(minutes=10)
CREATE_RATE_LIMIT_PER_HOUR = 10
PERIODIC_TYPES = ('interval', 'cron')
SCHEDULE_FIELDS = ('schedule_type', 'schedule_config', 'timezone')

_TASK_COLUMNS = (
    'id, task_uuid, owner_id, agent_id, name, description, prompt, system_prompt, '
    'skill_bundle_ids, skill_bundle_refs, skill_ids, skill_refs, enabled_toolsets, '
    'schedule_type, schedule_config, schedule_display, timezone, misfire_policy, '
    'enabled, state, next_run_at, last_run_at, last_status, run_count, repeat_times, repeat_completed, '
    'task_revision, created_by, created_by_kind, continuation_enabled, enable_subagents, '
    'created_time, updated_time, deleted_at'
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def task_to_public(row: dict[str, Any]) -> dict[str, Any]:
    """任务行 → agent 可见投影（task_id = 端云稳定 task_uuid）。"""
    return {
        'task_id': row.get('task_uuid'),
        'name': row.get('name'),
        'description': row.get('description'),
        'prompt': row.get('prompt'),
        'system_prompt': row.get('system_prompt'),
        'agent_id': row.get('agent_id'),
        'schedule_type': row.get('schedule_type'),
        'schedule_config': _as_dict(row.get('schedule_config')),
        'schedule_display': row.get('schedule_display'),
        'timezone': row.get('timezone'),
        'state': row.get('state'),
        'enabled': row.get('enabled'),
        'next_run_at': _iso(row.get('next_run_at')),
        'last_run_at': _iso(row.get('last_run_at')),
        'last_status': row.get('last_status'),
        'run_count': row.get('run_count'),
        'continuation_enabled': bool(row.get('continuation_enabled')),
        'enable_subagents': bool(row.get('enable_subagents')),
        'created_by_kind': row.get('created_by_kind') or 'owner',
        'created_at': _iso(row.get('created_time')),
        'updated_at': _iso(row.get('updated_time')),
    }


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


class AgentTaskService:
    """Agent JWT 通道的任务读写（owner = agent.owner_hasn_id，跨户恒 NotFound）。"""

    # ---------- 读 ----------

    @staticmethod
    async def get_task_row(db: AsyncSession, *, owner_id: str, task_uuid: str) -> dict[str, Any]:
        result = await db.execute(
            sa.text(
                f'SELECT {_TASK_COLUMNS} FROM hasn_task.task '
                "WHERE owner_id = :o AND task_uuid = :u AND state <> 'deleted'"
            ),
            {'o': owner_id, 'u': task_uuid},
        )
        row = result.mappings().first()
        if row is None:
            raise errors.NotFoundError(msg='任务不存在')
        return dict(row)

    @staticmethod
    async def list_tasks(
        db: AsyncSession, *, owner_id: str, state: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        clause = "owner_id = :o AND state <> 'deleted'"
        params: dict[str, Any] = {'o': owner_id, 'limit': max(1, min(limit, 100))}
        if state:
            clause += ' AND state = :s'
            params['s'] = state
        result = await db.execute(
            sa.text(
                f'SELECT {_TASK_COLUMNS} FROM hasn_task.task WHERE {clause} '
                'ORDER BY updated_time DESC LIMIT :limit'
            ),
            params,
        )
        return [task_to_public(dict(r)) for r in result.mappings().all()]

    @staticmethod
    async def list_run_summaries(
        db: AsyncSession,
        *,
        owner_id: str,
        task_uuid: str,
        limit: int = 10,
        status: str | None = None,
        include_artifacts: bool = True,
    ) -> list[dict[str, Any]]:
        """读 v2.1 云端权威 run 摘要（query_results / list_runs 共用，D2 深查出口）。"""
        clause = 'owner_id = :o AND task_uuid = :u'
        params: dict[str, Any] = {'o': owner_id, 'u': task_uuid, 'limit': max(1, min(limit, 50))}
        if status:
            clause += ' AND status = :s'
            params['s'] = status
        result = await db.execute(
            sa.text(
                'SELECT run_uuid, task_uuid, status, scheduled_fire_at, started_at, finished_at, '
                'output_summary, error, model, duration_ms, continued_from_run_id, artifacts_json '
                f'FROM hasn_task.run_summary WHERE {clause} '
                'ORDER BY coalesce(finished_at, started_at, scheduled_fire_at) DESC LIMIT :limit'
            ),
            params,
        )
        out = []
        for r in result.mappings().all():
            item = {
                'run_id': r['run_uuid'],
                'status': r['status'],
                'scheduled_fire_at': _iso(r['scheduled_fire_at']),
                'started_at': _iso(r['started_at']),
                'finished_at': _iso(r['finished_at']),
                'output_summary': r['output_summary'],
                'error': r['error'],
                'model': r['model'],
                'duration_ms': r['duration_ms'],
                'continued_from_run_id': r['continued_from_run_id'],
            }
            if include_artifacts:
                item['artifacts'] = _as_list(r['artifacts_json'])
            out.append(item)
        return out

    @staticmethod
    async def get_run_summary(db: AsyncSession, *, owner_id: str, run_uuid: str) -> dict[str, Any]:
        result = await db.execute(
            sa.text(
                'SELECT run_uuid, task_uuid, status, scheduled_fire_at, started_at, finished_at, '
                'output_summary, error, model, duration_ms, continued_from_run_id, artifacts_json '
                'FROM hasn_task.run_summary WHERE owner_id = :o AND run_uuid = :r'
            ),
            {'o': owner_id, 'r': run_uuid},
        )
        r = result.mappings().first()
        if r is None:
            raise errors.NotFoundError(msg='执行记录不存在')
        return {
            'run_id': r['run_uuid'],
            'task_id': r['task_uuid'],
            'status': r['status'],
            'scheduled_fire_at': _iso(r['scheduled_fire_at']),
            'started_at': _iso(r['started_at']),
            'finished_at': _iso(r['finished_at']),
            'output_summary': r['output_summary'],
            'error': r['error'],
            'model': r['model'],
            'duration_ms': r['duration_ms'],
            'continued_from_run_id': r['continued_from_run_id'],
            'artifacts': _as_list(r['artifacts_json']),
        }

    # ---------- 写（事件通道） ----------

    @classmethod
    async def _emit_task_event(
        cls,
        db: AsyncSession,
        *,
        owner_id: str,
        event_type: str,
        payload: dict[str, Any],
        agent_hasn_id: str | None = None,
    ) -> None:
        event = ClientEvent(
            client_event_id=f'cae_{uuid.uuid4().hex}',
            event_type=event_type,
            payload={'task': payload},
            hasn_id=agent_hasn_id,
        )
        await hasn_sync_service.gateway.save_task_event(
            db, owner_id=owner_id, node_id=CLOUD_AGENT_NODE_ID, event=event
        )
        # LF-P3：任务变更后向该 owner 在线节点 push hasn.sync.invalidate{kind:tasks}，
        # 在线 daemon 秒级对账任务镜像 → daemon→webui 失效桥 → 任务页即时刷新（不再绑死轮询）。
        from backend.app.hasn.service.sync_invalidate_service import KIND_TASKS, bump_owner

        await bump_owner(KIND_TASKS, db, owner_id)

    @classmethod
    def _event_payload_from_row(cls, row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        """以当前任务行为底 + 局部覆盖，构造 task.* 事件 payload（base_revision 防丢更新）。"""
        payload = {
            'task_id': row['task_uuid'],
            'base_revision': row.get('task_revision'),
            'agent_id': row.get('agent_id'),
            'name': row.get('name'),
            'description': row.get('description'),
            'prompt': row.get('prompt'),
            'system_prompt': row.get('system_prompt'),
            'skill_bundle_ids': _as_list(row.get('skill_bundle_ids')),
            'skill_bundle_refs': _as_list(row.get('skill_bundle_refs')),
            'skill_ids': _as_list(row.get('skill_ids')),
            'skill_refs': _as_list(row.get('skill_refs')),
            'schedule_type': row.get('schedule_type'),
            'schedule_config': _as_dict(row.get('schedule_config')),
            'schedule_display': row.get('schedule_display'),
            'timezone': row.get('timezone'),
            'misfire_policy': row.get('misfire_policy'),
            'enabled': row.get('enabled'),
            'state': row.get('state'),
            'next_run_at': _iso(row.get('next_run_at')),
            'run_count': row.get('run_count'),
            'repeat_times': row.get('repeat_times'),
            'repeat_completed': row.get('repeat_completed'),
            'created_by': row.get('created_by'),
            'created_by_kind': row.get('created_by_kind') or 'owner',
            'continuation_enabled': bool(row.get('continuation_enabled')),
            'enable_subagents': bool(row.get('enable_subagents')),
            'created_at': _iso(row.get('created_time')),
            'updated_at': timezone.now().isoformat(),
        }
        payload.update(overrides)
        return payload

    @classmethod
    async def create_task(
        cls, db: AsyncSession, *, agent: AgentTokenPayload, params: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """agent 建任务（D4/R4）。返回 (任务投影, 是否新建)。"""
        owner_id = agent.owner_hasn_id
        target_agent = str(params.get('agent_id') or agent.agent_hasn_id)
        name = str(params['name'])
        schedule_type = str(params['schedule_type'])
        schedule_config = dict(params.get('schedule_config') or {})
        if schedule_type not in ('once', *PERIODIC_TYPES):
            raise errors.RequestError(msg=f'未知调度类型: {schedule_type}')

        now = timezone.now()
        # R4 幂等键：短窗口同参 → 返回已建任务而非新建
        existing = await db.execute(
            sa.text(
                f'SELECT {_TASK_COLUMNS} FROM hasn_task.task '
                'WHERE owner_id = :o AND agent_id = :a AND name = :n AND schedule_type = :st '
                "AND schedule_config = CAST(:sc AS jsonb) AND state <> 'deleted' AND created_time > :since "
                'ORDER BY created_time DESC LIMIT 1'
            ),
            {
                'o': owner_id,
                'a': target_agent,
                'n': name,
                'st': schedule_type,
                'sc': json.dumps(schedule_config, ensure_ascii=False, sort_keys=True),
                'since': now - IDEMPOTENCY_WINDOW,
            },
        )
        row = existing.mappings().first()
        if row is not None:
            return task_to_public(dict(row)), False

        # R4 频控：单 agent 每小时建任务上限
        count = await db.execute(
            sa.text(
                'SELECT count(*) FROM hasn_task.task '
                "WHERE owner_id = :o AND created_by = :a AND created_by_kind = 'agent' AND created_time > :since"
            ),
            {'o': owner_id, 'a': agent.agent_hasn_id, 'since': now - timedelta(hours=1)},
        )
        if (count.scalar() or 0) >= CREATE_RATE_LIMIT_PER_HOUR:
            raise errors.RequestError(msg=f'建任务过于频繁（上限 {CREATE_RATE_LIMIT_PER_HOUR}/小时），稍后再试')

        # D4 业务态
        is_periodic = schedule_type in PERIODIC_TYPES
        state = 'pending_approval' if is_periodic else 'scheduled'
        next_run_at = None if is_periodic else calc_next_run_at(schedule_type, schedule_config)

        task_uuid = f'tsk_{uuid.uuid4().hex}'
        payload = {
            'task_id': task_uuid,
            'agent_id': target_agent,
            'name': name,
            'description': params.get('description'),
            'prompt': str(params['prompt']),
            'system_prompt': params.get('system_prompt'),
            'skill_bundle_refs': list(params.get('skill_bundle_refs') or []),
            'schedule_type': schedule_type,
            'schedule_config': schedule_config,
            'timezone': str(params.get('timezone') or 'Asia/Shanghai'),
            'enabled': True,
            'state': state,
            'next_run_at': _iso(next_run_at),
            'created_by': agent.agent_hasn_id,
            'created_by_kind': 'agent',
            'continuation_enabled': bool(params.get('continuation_enabled')),
            'enable_subagents': bool(params.get('enable_subagents')),
            'created_at': now.isoformat(),
            'updated_at': now.isoformat(),
        }
        await cls._emit_task_event(
            db, owner_id=owner_id, event_type='task.created', payload=payload, agent_hasn_id=target_agent
        )
        if state == 'pending_approval':
            await cls._notify_pending_approval(db, agent=agent, task_uuid=task_uuid, name=name)
        row = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        return task_to_public(row), True

    @classmethod
    async def _notify_pending_approval(
        cls, db: AsyncSession, *, agent: AgentTokenPayload, task_uuid: str, name: str
    ) -> None:
        """D4：周期任务待确认 → 提醒卡片（通知，非审批票据）。"""
        await notification_service.emit(
            db,
            recipient_id=agent.owner_hasn_id,
            source={
                'kind': 'agent',
                'id': agent.agent_hasn_id,
                'display_name': agent.agent_name,
                'on_behalf_of': agent.owner_hasn_id,
            },
            category='reminder',
            type='task.pending_approval',
            title=f'分身 {agent.agent_name} 为你创建了定时任务「{name}」，待你确认',
            payload={'target': {'kind': 'task', 'id': task_uuid}, 'deep_link': f'/tasks/{task_uuid}'},
            dedupe_key=f'task.pending_approval:{task_uuid}',
        )

    @staticmethod
    def _patch_overrides(row: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        """patch → 事件 overrides（含 R2：调度变更且结果为周期 → 回 pending_approval）。"""
        overrides: dict[str, Any] = {}
        for field in ('name', 'description', 'prompt', 'system_prompt', 'timezone'):
            if patch.get(field) is not None:
                overrides[field] = patch[field]
        for field in ('continuation_enabled', 'enable_subagents'):
            if patch.get(field) is not None:
                overrides[field] = bool(patch[field])

        schedule_changed = False
        new_schedule_type = row['schedule_type']
        if patch.get('schedule_type') is not None and patch['schedule_type'] != row['schedule_type']:
            overrides['schedule_type'] = new_schedule_type = str(patch['schedule_type'])
            schedule_changed = True
        if patch.get('schedule_config') is not None:
            overrides['schedule_config'] = dict(patch['schedule_config'])
            schedule_changed = True
        if patch.get('timezone') is not None and patch['timezone'] != row['timezone']:
            schedule_changed = True

        # R2：agent 改调度且结果为周期 → 回 pending_approval（含 once→周期升级）
        if schedule_changed and new_schedule_type in PERIODIC_TYPES:
            overrides['state'] = 'pending_approval'
            overrides['next_run_at'] = None
        elif schedule_changed:
            cfg = overrides.get('schedule_config', _as_dict(row['schedule_config']))
            overrides['next_run_at'] = _iso(calc_next_run_at(new_schedule_type, cfg))
        return overrides

    @classmethod
    async def update_task(
        cls, db: AsyncSession, *, agent: AgentTokenPayload, task_uuid: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """agent 改任务（R2 状态守卫）。"""
        row = await cls.get_task_row(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
        if row['state'] == 'rejected':
            raise errors.RequestError(msg='任务已被主人拒绝，不可修改')

        overrides = cls._patch_overrides(row, patch)
        payload = cls._event_payload_from_row(row, **overrides)
        await cls._emit_task_event(
            db,
            owner_id=agent.owner_hasn_id,
            event_type='task.updated',
            payload=payload,
            agent_hasn_id=row['agent_id'],
        )
        if overrides.get('state') == 'pending_approval':
            await cls._notify_pending_approval(db, agent=agent, task_uuid=task_uuid, name=payload['name'])
        fresh = await cls.get_task_row(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
        return task_to_public(fresh)

    @classmethod
    async def _transition(
        cls,
        db: AsyncSession,
        *,
        owner_id: str,
        task_uuid: str,
        allowed_from: tuple[str, ...],
        action: str,
        **overrides: Any,
    ) -> dict[str, Any]:
        row = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        if row['state'] not in allowed_from:
            raise errors.RequestError(msg=f'当前状态 {row["state"]} 不允许{action}')
        payload = cls._event_payload_from_row(row, **overrides)
        await cls._emit_task_event(
            db, owner_id=owner_id, event_type='task.updated', payload=payload, agent_hasn_id=row['agent_id']
        )
        fresh = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        return task_to_public(fresh)

    @classmethod
    async def pause_task(cls, db: AsyncSession, *, owner_id: str, task_uuid: str) -> dict[str, Any]:
        return await cls._transition(
            db, owner_id=owner_id, task_uuid=task_uuid, allowed_from=('scheduled',), action='暂停', state='paused'
        )

    @classmethod
    async def resume_task(cls, db: AsyncSession, *, owner_id: str, task_uuid: str) -> dict[str, Any]:
        # R2：仅 paused → scheduled；pending_approval/rejected 不得经 resume 绕过审批
        return await cls._transition(
            db, owner_id=owner_id, task_uuid=task_uuid, allowed_from=('paused',), action='恢复', state='scheduled'
        )

    @classmethod
    async def run_now(cls, db: AsyncSession, *, owner_id: str, task_uuid: str) -> dict[str, Any]:
        # R2：仅 scheduled/paused 可触发；置 next_run_at=now，由持有 runtime 的节点本地 tick 拾取（中心不 tick）
        return await cls._transition(
            db,
            owner_id=owner_id,
            task_uuid=task_uuid,
            allowed_from=('scheduled', 'paused'),
            action='立即执行',
            state='scheduled',
            next_run_at=timezone.now().isoformat(),
        )

    @classmethod
    async def delete_task(cls, db: AsyncSession, *, owner_id: str, task_uuid: str) -> None:
        row = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        payload = cls._event_payload_from_row(row, state='deleted', deleted_at=timezone.now().isoformat())
        await cls._emit_task_event(
            db, owner_id=owner_id, event_type='task.deleted', payload=payload, agent_hasn_id=row['agent_id']
        )

    # ---------- 主人审批（D4 业务态，owner JWT 经 app 面调用） ----------

    @classmethod
    async def approve_task(cls, db: AsyncSession, *, owner_id: str, task_uuid: str) -> dict[str, Any]:
        row = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        if row['state'] != 'pending_approval':
            raise errors.RequestError(msg=f'当前状态 {row["state"]} 无待审批事项')
        next_run_at = calc_next_run_at(row['schedule_type'], _as_dict(row['schedule_config']))
        payload = cls._event_payload_from_row(row, state='scheduled', next_run_at=_iso(next_run_at))
        await cls._emit_task_event(
            db, owner_id=owner_id, event_type='task.updated', payload=payload, agent_hasn_id=row['agent_id']
        )
        fresh = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        return task_to_public(fresh)

    @classmethod
    async def reject_task(cls, db: AsyncSession, *, owner_id: str, task_uuid: str) -> dict[str, Any]:
        row = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        if row['state'] != 'pending_approval':
            raise errors.RequestError(msg=f'当前状态 {row["state"]} 无待审批事项')
        payload = cls._event_payload_from_row(row, state='rejected', next_run_at=None)
        await cls._emit_task_event(
            db, owner_id=owner_id, event_type='task.updated', payload=payload, agent_hasn_id=row['agent_id']
        )
        fresh = await cls.get_task_row(db, owner_id=owner_id, task_uuid=task_uuid)
        return task_to_public(fresh)


agent_task_service = AgentTaskService()
