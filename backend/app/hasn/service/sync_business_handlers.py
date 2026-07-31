"""sync inbox 的业务侧 handler；本模块不得反向被 hasn_sync 导入。"""

from __future__ import annotations

import hashlib
import json

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service.hasn_sync_service import (
    SqlAlchemySyncGateway,
    TaskSyncConflictError,
)
from backend.app.hasn_memory.service.fact_uplink_service import (
    MEMORY_FACT_UPLINK_EVENTS,
    FactUplinkConflictError,
    FactUplinkPermanentError,
    fact_uplink_service,
)
from backend.app.hasn_sync.adapters.sqlalchemy_appender import SqlAlchemySyncAppender
from backend.app.hasn_sync.ports.dto import InboxEnvelope, SyncEnvelope
from backend.common.exception import errors


SESSION_SYNC_EVENT = 'session.sync'
# doc16 时代的三类 memory 事件：走 `MemorySyncHandler` → `save_client_event`，只推游标不落业务表。
# 实施/98 起客户端已不再产出它们；接收面保留只为兜底未升级 daemon（实施/98 决策 3.5）。
MEMORY_LEGACY_SYNC_EVENTS = frozenset(
    {
        'memory.owner_event.upserted',
        'memory.owner_fact.upserted',
        'memory.agent_self_event.upserted',
    }
)
# doc19 §8.3-5：上行事件类型必须**同时**登记进云端白名单与本地事件构造——
# **两侧白名单不对称正是实施/98 事故的直接成因**（本地能产 12 类、云端只认 3 类，
# 客户端零过滤全推 → 每条被拒 → 无退避无丢弃 → 5s 无限重推 → 日志洪水 94%）。
# 语义事实六件套的定义与本地对照点见
# `backend/app/hasn_memory/service/fact_uplink_service.py::MEMORY_FACT_UPLINK_EVENTS`
# （本地构造点：`hasn-node/crates/hasn-memory/src/storage/authority.rs::MemoryOp::fact_event_type`）。
# 改动任一侧都必须同步另一侧，评审时逐条对照。
MEMORY_SYNC_EVENTS = MEMORY_LEGACY_SYNC_EVENTS | MEMORY_FACT_UPLINK_EVENTS
TASK_SYNC_EVENTS = frozenset({'task.created', 'task.updated', 'task.deleted'})
_SESSION_KINDS = frozenset({'interactive', 'task', 'temporary', 'external', 'system'})
_SESSION_SCOPES = frozenset({'conversation_visible', 'summary_only'})
_SESSION_STATUSES = frozenset({'active', 'waiting_for_user', 'completed', 'error', 'cancelled'})
_ORIGIN_TYPES = frozenset(
    {
        'ui',
        'scheduler',
        'task_run',
        'workflow_run',
        'external_app',
        'api',
        'system',
        'app',
        'manual',
        'copilot',
    }
)


class SyncBusinessHandler(Protocol):
    """单类业务事件的幂等应用端口。"""

    async def apply(
        self,
        db: AsyncSession,
        envelope: InboxEnvelope,
        *,
        idempotency_key: str,
    ) -> None: ...


class PermanentSyncBusinessError(ValueError):
    """载荷或版本冲突等重试不会自愈的业务错误。"""


def _required_text(payload: Mapping[str, Any], field: str, max_length: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PermanentSyncBusinessError(f'{field} 必须是非空字符串')
    if len(value) > max_length:
        raise PermanentSyncBusinessError(f'{field} 长度不能超过 {max_length}')
    return value


def _optional_text(payload: Mapping[str, Any], field: str, max_length: int) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise PermanentSyncBusinessError(
            f'{field} 必须是长度不超过 {max_length} 的字符串或 null'
        )
    return value


def _enum_text(
    payload: Mapping[str, Any],
    field: str,
    allowed: frozenset[str],
) -> str:
    value = _required_text(payload, field, 40)
    if value not in allowed:
        raise PermanentSyncBusinessError(f'{field} 取值不受支持：{value}')
    return value


async def _require_owned_subject(
    db: AsyncSession,
    *,
    owner_id: str,
    subject_hasn_id: str,
) -> None:
    """只允许主人本人或其名下活跃分身成为同步事件主体。"""
    if subject_hasn_id == owner_id:
        return
    owned = (
        await db.execute(
            sa.text(
                'SELECT 1 FROM public.hasn_agents '
                'WHERE hasn_id = :subject_hasn_id '
                'AND owner_id = :owner_id '
                "AND status = 'active' "
                'AND deleted_at IS NULL '
                'LIMIT 1'
            ),
            {
                'subject_hasn_id': subject_hasn_id,
                'owner_id': owner_id,
            },
        )
    ).scalar_one_or_none()
    if owned is None:
        raise PermanentSyncBusinessError(
            f'事件主体分身不属于认证主人：{subject_hasn_id}'
        )


@dataclass(frozen=True)
class SessionSyncHandler:
    """把 session.sync 完整载荷幂等投影到云端会话表。"""

    appender: SqlAlchemySyncAppender = field(default_factory=SqlAlchemySyncAppender)

    async def apply(
        self,
        db: AsyncSession,
        envelope: InboxEnvelope,
        *,
        idempotency_key: str,
    ) -> None:
        payload = envelope.payload
        payload_owner = payload.get('owner_id')
        if payload_owner is not None and payload_owner != envelope.owner_id:
            raise PermanentSyncBusinessError(
                'session.sync owner_id 与认证信封不一致'
            )
        payload_hasn_id = _required_text(payload, 'hasn_id', 64)
        if payload_hasn_id != envelope.hasn_id:
            raise PermanentSyncBusinessError(
                'session.sync hasn_id 与认证信封不一致'
            )
        await _require_owned_subject(
            db,
            owner_id=envelope.owner_id,
            subject_hasn_id=envelope.hasn_id,
        )

        session_id = _required_text(payload, 'session_id', 64)
        values = {
            'session_id': session_id,
            'conversation_id': _optional_text(payload, 'conversation_id', 64),
            'owner_id': envelope.owner_id,
            'hasn_id': payload_hasn_id,
            'session_kind': _enum_text(payload, 'session_kind', _SESSION_KINDS),
            'session_scope': _enum_text(payload, 'session_scope', _SESSION_SCOPES),
            'session_status': _enum_text(payload, 'session_status', _SESSION_STATUSES),
            'parent_session_id': _optional_text(payload, 'parent_session_id', 64),
            'continuation_from_session_id': _optional_text(
                payload,
                'continuation_from_session_id',
                64,
            ),
            'origin_type': _enum_text(payload, 'origin_type', _ORIGIN_TYPES),
            'origin_ref': _optional_text(payload, 'origin_ref', 200),
            'title': _optional_text(payload, 'title', 500),
            'summary_checkpoint_json': json.dumps(
                payload.get('summary_checkpoint_json') or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(',', ':'),
                default=str,
            ),
            'active_binding_id': _optional_text(payload, 'active_binding_id', 64),
            'last_message_id': _optional_text(payload, 'last_message_id', 64),
        }
        row = (
            await db.execute(
                sa.text(
                    'INSERT INTO public.hasn_sessions ('
                    'session_id, conversation_id, owner_id, hasn_id, '
                    'session_kind, session_scope, session_status, '
                    'parent_session_id, continuation_from_session_id, '
                    'origin_type, origin_ref, title, summary_checkpoint_json, '
                    'active_binding_id, last_message_id, created_time, updated_time'
                    ') VALUES ('
                    ':session_id, :conversation_id, :owner_id, :hasn_id, '
                    ':session_kind, :session_scope, :session_status, '
                    ':parent_session_id, :continuation_from_session_id, '
                    ':origin_type, :origin_ref, :title, '
                    'CAST(:summary_checkpoint_json AS jsonb), '
                    ':active_binding_id, :last_message_id, now(), now()'
                    ') ON CONFLICT (session_id) DO UPDATE SET '
                    'conversation_id = EXCLUDED.conversation_id, '
                    'session_kind = EXCLUDED.session_kind, '
                    'session_scope = EXCLUDED.session_scope, '
                    'session_status = EXCLUDED.session_status, '
                    'parent_session_id = EXCLUDED.parent_session_id, '
                    'continuation_from_session_id = EXCLUDED.continuation_from_session_id, '
                    'origin_type = EXCLUDED.origin_type, '
                    'origin_ref = EXCLUDED.origin_ref, '
                    'title = EXCLUDED.title, '
                    'summary_checkpoint_json = EXCLUDED.summary_checkpoint_json, '
                    'active_binding_id = EXCLUDED.active_binding_id, '
                    'last_message_id = EXCLUDED.last_message_id, '
                    'updated_time = now() '
                    'WHERE public.hasn_sessions.owner_id = EXCLUDED.owner_id '
                    '  AND public.hasn_sessions.hasn_id = EXCLUDED.hasn_id '
                    'RETURNING session_id'
                ),
                values,
            )
        ).scalar_one_or_none()
        if row is None:
            raise PermanentSyncBusinessError(
                f'session.sync 拒绝覆盖其他身份的会话；幂等键={idempotency_key}'
            )
        await self.appender.append(
            db,
            SyncEnvelope(
                owner_id=envelope.owner_id,
                hasn_id=payload_hasn_id,
                event_type=SESSION_SYNC_EVENT,
                aggregate_type='session',
                aggregate_id=session_id,
                payload={**payload, 'owner_id': envelope.owner_id},
                producer='sync_inbox',
                source_event_id=hashlib.sha256(
                    idempotency_key.encode('utf-8')
                ).hexdigest(),
            ),
        )


@dataclass(frozen=True)
class MemorySyncHandler:
    """应用 daemon 上行的记忆事件；sync 表只经 append_event 函数写入。"""

    gateway: SqlAlchemySyncGateway = field(default_factory=SqlAlchemySyncGateway)

    async def apply(
        self,
        db: AsyncSession,
        envelope: InboxEnvelope,
        *,
        idempotency_key: str,
    ) -> None:
        # 只认 doc16 时代那三类；语义事实六件套走 `FactUplinkHandler`，别落错路由——
        # 走这条路只推游标不落 `semantic_fact`，事实会静默丢失。
        if envelope.event_type not in MEMORY_LEGACY_SYNC_EVENTS:
            raise PermanentSyncBusinessError(
                f'不支持的 memory sync 事件：{envelope.event_type}'
            )
        await _require_owned_subject(
            db,
            owner_id=envelope.owner_id,
            subject_hasn_id=envelope.hasn_id,
        )
        event = ClientEvent(
            client_event_id=envelope.client_event_id,
            event_type=envelope.event_type,
            payload=envelope.payload,
            hasn_id=envelope.hasn_id,
            dedupe_key=envelope.dedupe_key,
        )
        try:
            await self.gateway.save_client_event(
                db,
                owner_id=envelope.owner_id,
                node_id=envelope.node_id,
                event=event,
                manage_inbox=False,
            )
        except (
            errors.RequestError,
            errors.ForbiddenError,
            errors.NotFoundError,
            errors.ConflictError,
        ) as exc:
            raise PermanentSyncBusinessError(str(exc)) from exc


@dataclass(frozen=True)
class FactUplinkHandler:
    """应用 daemon 上行的语义事实业务事件（doc19 §8.3）。

    与 `MemorySyncHandler` 的根本区别：**这条路真的落 `hasn_memory.semantic_fact`**，
    再由 `emit_memory_event` 推进命名空间 revision 把结果回灌全网；那条路只推游标。

    拒绝三分类（§8.3-4）在此收口：

    - `FactUplinkPermanentError` → `PermanentSyncBusinessError`：inbox 立即置 dead，不重试
      （载荷非法 / 越权 / 墓碑命中。push 端点已在入队前拦掉绝大多数，这里兜 push→apply 竞态）；
    - `FactUplinkConflictError` → 原样上抛：worker 走既有重试计数，**有退避**；
    - 其余异常（DB 抖动）同样上抛重试。
    """

    async def apply(
        self,
        db: AsyncSession,
        envelope: InboxEnvelope,
        *,
        idempotency_key: str,
    ) -> None:
        if envelope.event_type not in MEMORY_FACT_UPLINK_EVENTS:
            raise PermanentSyncBusinessError(
                f'不支持的语义事实上行事件：{envelope.event_type}'
            )
        await _require_owned_subject(
            db,
            owner_id=envelope.owner_id,
            subject_hasn_id=envelope.hasn_id,
        )
        try:
            await fact_uplink_service.apply_fact_event(
                db,
                owner_id=envelope.owner_id,
                node_id=envelope.node_id,
                event_type=envelope.event_type,
                payload=envelope.payload,
            )
        except FactUplinkPermanentError as exc:
            raise PermanentSyncBusinessError(
                f'{exc.error.name}({exc.error.code}): {exc.reason}；幂等键={idempotency_key}'
            ) from exc
        except FactUplinkConflictError:
            # 可退避重试：不要包成 PermanentSyncBusinessError，否则一次竞态就把事实永久丢弃。
            raise


@dataclass(frozen=True)
class TaskSyncHandler:
    """应用 daemon 上行的任务定义事件。"""

    gateway: SqlAlchemySyncGateway = field(default_factory=SqlAlchemySyncGateway)

    async def apply(
        self,
        db: AsyncSession,
        envelope: InboxEnvelope,
        *,
        idempotency_key: str,
    ) -> None:
        if envelope.event_type not in TASK_SYNC_EVENTS:
            raise PermanentSyncBusinessError(
                f'不支持的 task sync 事件：{envelope.event_type}'
            )
        payload_owner = envelope.payload.get('owner_id')
        if payload_owner is not None and payload_owner != envelope.owner_id:
            raise PermanentSyncBusinessError(
                'task sync owner_id 与认证信封不一致'
            )
        payload_agent = envelope.payload.get('agent_id')
        if payload_agent is not None and payload_agent != envelope.hasn_id:
            raise PermanentSyncBusinessError(
                'task sync agent_id 与认证信封不一致'
            )
        await _require_owned_subject(
            db,
            owner_id=envelope.owner_id,
            subject_hasn_id=envelope.hasn_id,
        )
        event = ClientEvent(
            client_event_id=envelope.client_event_id,
            event_type=envelope.event_type,
            payload=envelope.payload,
            hasn_id=envelope.hasn_id,
            dedupe_key=envelope.dedupe_key,
        )
        try:
            await self.gateway.save_task_event(
                db,
                owner_id=envelope.owner_id,
                node_id=envelope.node_id,
                event=event,
                manage_inbox=False,
            )
        except TaskSyncConflictError as exc:
            raise PermanentSyncBusinessError(str(exc)) from exc
        except (
            errors.RequestError,
            errors.ForbiddenError,
            errors.NotFoundError,
            errors.ConflictError,
        ) as exc:
            raise PermanentSyncBusinessError(str(exc)) from exc


def build_sync_handler_registry() -> dict[str, SyncBusinessHandler]:
    """构造业务事件类型到幂等 handler 的显式注册表。"""
    memory_handler = MemorySyncHandler()
    fact_handler = FactUplinkHandler()
    task_handler = TaskSyncHandler()
    return {
        SESSION_SYNC_EVENT: SessionSyncHandler(),
        **dict.fromkeys(MEMORY_LEGACY_SYNC_EVENTS, memory_handler),
        **dict.fromkeys(MEMORY_FACT_UPLINK_EVENTS, fact_handler),
        **dict.fromkeys(TASK_SYNC_EVENTS, task_handler),
    }
