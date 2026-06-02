"""S4 HASN message hub service.

Scope guard:
- Implements only S4 message hub / inbox / suppressed-inbox behavior.
- Does not implement S3 Sandbox Manager, S5 Channel Bridge, or S6 anti-abuse.
- Does not mutate OpenAPI, HASN Protocol schemas, or error-code contracts.
- RuntimeUnavailable is a non-blocking dispatch status, never a message-delivery failure.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.hasn_message_hub import (
    ErrorObject,
    InboxItem,
    InboxPullRequest,
    InboxPullResponse,
    MessageHubSendRequest,
    MessageHubSendResponse,
)
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.common.exception import errors
from backend.utils.timezone import timezone

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
}

_RUNTIME_UNAVAILABLE_WARNING = ErrorObject(
    code=0,
    name='ERR_RUNTIME_UNAVAILABLE_NON_BLOCKING',
    message='Runtime unavailable; message was still delivered to inbox.',
)
_RUNTIME_DISPATCH_FAILED_WARNING = ErrorObject(
    code=0,
    name='ERR_RUNTIME_DISPATCH_FAILED_NON_BLOCKING',
    message='Runtime dispatch failed; message was still delivered to inbox.',
)


@dataclass(slots=True)
class Recipient:
    hasn_id: str
    entity_type: str
    owner_id: str
    name: str | None = None


@dataclass(slots=True)
class RuntimeSummary:
    agent_hasn_id: str
    runtime_status: str
    adapter_registered: bool
    handle_available: bool
    binding_id: str | None = None
    runtime_type: str | None = None
    summary_json: dict[str, Any] = field(default_factory=dict)
    node_id: str | None = None
    binding_node_id: str | None = None
    presence: str | None = None

    @property
    def is_reachable(self) -> bool:
        binding_node_id = self.binding_node_id or self.node_id
        same_node = self.node_id is None or binding_node_id == self.node_id
        presence_online = self.presence in {None, '', 'online'}
        return (
            self.runtime_status in {'online', 'active', 'degraded'}
            and self.adapter_registered
            and self.handle_available
            and bool(self.binding_id)
            and same_node
            and presence_online
        )

    @property
    def is_dispatchable(self) -> bool:
        return self.is_reachable

    @property
    def unreachable_reason(self) -> str:
        if not self.binding_id:
            return 'NoRuntimeBinding'
        binding_node_id = self.binding_node_id or self.node_id
        if self.node_id is not None and binding_node_id != self.node_id:
            return 'RuntimeBindingNodeMismatch'
        if self.presence not in {None, '', 'online'}:
            return 'PresenceOffline'
        if self.runtime_status not in {'online', 'active', 'degraded'}:
            return 'RuntimeOffline'
        if not self.adapter_registered:
            return 'RuntimeAdapterMissing'
        if not self.handle_available:
            return 'RuntimeHandleUnavailable'
        return 'AgentUnreachable'


@dataclass(slots=True)
class StoredMessage:
    message_id: str
    owner_id: str
    hasn_id: str
    conversation_id: str
    inbox_kind: str
    envelope: dict[str, Any]
    dispatch_status: str
    created_at: datetime


@dataclass(slots=True)
class MessageRecord:
    conversation_id: str
    owner_id: str
    hasn_id: str
    from_id: str
    to_id: str
    content: dict[str, Any]
    envelope: dict[str, Any]
    inbox_kind: str
    dispatch_status: str
    delivery_status: str = 'delivered'
    owner_copy_of_message_id: str | None = None
    runtime_summary: RuntimeSummary | None = None
    msg_type: str = 'message'
    content_type: int = 1
    priority: str = 'normal'
    client_message_id: str | None = None
    process_blocks: list[dict[str, Any]] = field(default_factory=list)


class MessageHubGateway(Protocol):
    async def resolve_recipient(self, db: AsyncSession, target_hasn_id: str) -> Recipient | None: ...
    async def store_inbox_message(self, db: AsyncSession, record: MessageRecord) -> StoredMessage: ...
    async def latest_runtime_summary(
        self, db: AsyncSession, *, owner_id: str, agent_hasn_id: str
    ) -> RuntimeSummary | None: ...
    async def store_suppressed(
        self,
        db: AsyncSession,
        *,
        source_message: StoredMessage,
        reason: str,
        dispatch_status: str,
        runtime_summary: RuntimeSummary | None,
    ) -> None: ...
    async def pull_inbox(
        self, db: AsyncSession, request: InboxPullRequest, *, limit: int = 100
    ) -> InboxPullResponse: ...
    async def mark_dispatch_status(
        self, db: AsyncSession, *, message_id: str, dispatch_status: str
    ) -> None: ...


class FanoutGateway(Protocol):
    async def push(self, target_hasn_id: str, payload: dict[str, Any]) -> bool: ...


class RuntimeDispatcher(Protocol):
    async def dispatch(self, target_agent_id: str, payload: dict[str, Any], runtime: RuntimeSummary) -> bool: ...


class SideEffectDispatcher(Protocol):
    async def dispatch(self, envelope: dict[str, Any], message: StoredMessage) -> None: ...


class WsFanoutGateway:
    async def push(self, target_hasn_id: str, payload: dict[str, Any]) -> bool:
        """Lazy-import ws_router to avoid service import cycles."""
        from backend.app.hasn.service.ws_router import ws_router

        return await ws_router.push_message_to(target_hasn_id, payload)


class WsRuntimeDispatcher:
    async def dispatch(self, target_agent_id: str, payload: dict[str, Any], runtime: RuntimeSummary) -> bool:
        """Minimal S4 dispatch intent: only emits when runtime is already known dispatchable."""
        from backend.app.hasn.service.ws_router import ws_router

        dispatch_payload = {
            'hasn': 'hasn/0.2',
            'method': 'hasn.runtime.dispatch',
            'params': {
                'agent_id': target_agent_id,
                'binding_id': runtime.binding_id,
                'runtime_type': runtime.runtime_type,
                'message': payload['params']['message'],
            },
        }
        return await ws_router.push_message_to(target_agent_id, dispatch_payload)


class NoopServerSideEffectDispatcher:
    """S4 placeholder for server-side effects; failures must not block message delivery."""

    SUPPORTED_ACTIONS = {'trade.accept', 'bridge.delivery_receipt'}

    async def dispatch(self, envelope: dict[str, Any], message: StoredMessage) -> None:
        action = _side_effect_action(envelope)
        if action not in self.SUPPORTED_ACTIONS:
            return
        # S5/S6 will own concrete bridge/abuse flows. S4 only reserves the non-blocking hook.


class SqlAlchemyMessageHubGateway:
    """Persistence adapter for S4 business writes.

    This is not a generic CRUD surface. S1 tables are codegen inputs, while S4 owns
    message-hub business writes and uses explicit owner_id + hasn_id on every row.
    """

    async def resolve_recipient(self, db: AsyncSession, target_hasn_id: str) -> Recipient | None:
        if target_hasn_id.startswith('h_'):
            result = await db.execute(
                sa.text(
                    '''
                    SELECT hasn_id, name
                    FROM public.hasn_humans
                    WHERE hasn_id = :hasn_id
                      AND status = 'active'
                    LIMIT 1
                    '''
                ),
                {'hasn_id': target_hasn_id},
            )
            row = result.mappings().first()
            if row:
                return Recipient(
                    hasn_id=row['hasn_id'],
                    owner_id=row['hasn_id'],
                    entity_type='human',
                    name=row.get('name'),
                )
            return None

        if target_hasn_id.startswith('a_'):
            result = await db.execute(
                sa.text(
                    '''
                    SELECT hasn_id, owner_id, name
                    FROM public.hasn_agents
                    WHERE hasn_id = :hasn_id
                      AND status = 'active'
                    LIMIT 1
                    '''
                ),
                {'hasn_id': target_hasn_id},
            )
            row = result.mappings().first()
            if row:
                return Recipient(
                    hasn_id=row['hasn_id'],
                    owner_id=row['owner_id'],
                    entity_type='agent',
                    name=row.get('name'),
                )
        return None

    async def store_inbox_message(self, db: AsyncSession, record: MessageRecord) -> StoredMessage:
        context = {
            's4_inbox_kind': record.inbox_kind,
            's4_envelope': record.envelope,
        }
        if record.runtime_summary:
            context['runtime_summary'] = _public_runtime_summary(record.runtime_summary)

        result = await db.execute(
            sa.text(
                '''
                INSERT INTO public.hasn_messages (
                    conversation_id,
                    owner_id,
                    hasn_id,
                    from_id,
                    sender_hasn_id,
                    from_type,
                    to_id,
                    recipient_hasn_id,
                    to_type,
                    content_type,
                    content,
                    process_blocks,
                    msg_type,
                    status,
                    priority,
                    runtime_type,
                    binding_id,
                    client_message_id,
                    sync_status,
                    delivery_status,
                    dispatch_status,
                    owner_copy_of_message_id,
                    context,
                    session_id,
                    session_seq,
                    server_received_at,
                    created_time
                ) VALUES (
                    CAST(:conversation_id AS uuid),
                    :owner_id,
                    :hasn_id,
                    :from_id,
                    :sender_hasn_id,
                    :from_type,
                    :to_id,
                    :recipient_hasn_id,
                    :to_type,
                    :content_type,
                    CAST(:content AS jsonb),
                    CAST(:process_blocks AS jsonb),
                    :msg_type,
                    1,
                    :priority,
                    :runtime_type,
                    :binding_id,
                    :client_message_id,
                    'pending',
                    :delivery_status,
                    :dispatch_status,
                    CAST(:owner_copy_of_message_id AS bigint),
                    CAST(:context AS jsonb),
                    :session_id,
                    :session_seq,
                    now(),
                    now()
                )
                RETURNING id, created_time
                '''
            ),
            {
                'conversation_id': record.conversation_id,
                'owner_id': record.owner_id,
                'hasn_id': record.hasn_id,
                'from_id': record.from_id,
                'sender_hasn_id': record.from_id,
                'from_type': _entity_type_int(record.from_id),
                'to_id': record.to_id,
                'recipient_hasn_id': record.to_id,
                'to_type': _entity_type_int(record.to_id),
                'content_type': record.content_type,
                'content': json.dumps(record.content, ensure_ascii=False, sort_keys=True),
                'process_blocks': json.dumps(record.process_blocks, ensure_ascii=False, sort_keys=True, default=str),
                'msg_type': record.msg_type,
                'priority': record.priority,
                'runtime_type': record.runtime_summary.runtime_type if record.runtime_summary else None,
                'binding_id': record.runtime_summary.binding_id if record.runtime_summary else None,
                'client_message_id': record.client_message_id,
                'delivery_status': record.delivery_status,
                'dispatch_status': record.dispatch_status,
                'owner_copy_of_message_id': record.owner_copy_of_message_id,
                'context': json.dumps(context, ensure_ascii=False, sort_keys=True, default=str),
                'session_id': getattr(record, 'session_id', None),
                'session_seq': getattr(record, 'session_seq', None),
            },
        )
        row = result.mappings().one()
        return StoredMessage(
            message_id=str(row['id']),
            owner_id=record.owner_id,
            hasn_id=record.hasn_id,
            conversation_id=record.conversation_id,
            inbox_kind=record.inbox_kind,
            envelope=copy.deepcopy(record.envelope),
            dispatch_status=record.dispatch_status,
            created_at=row['created_time'],
        )

    async def latest_runtime_summary(
        self, db: AsyncSession, *, owner_id: str, agent_hasn_id: str
    ) -> RuntimeSummary | None:
        result = await db.execute(
            sa.text(
                '''
                SELECT agent_hasn_id,
                       runtime_status,
                       adapter_registered,
                       handle_available,
                       binding_id,
                       runtime_type,
                       summary_json,
                       node_id
                FROM public.hasn_agent_runtime_reports
                WHERE owner_id = :owner_id
                  AND agent_hasn_id = :agent_hasn_id
                ORDER BY reported_at DESC, id DESC
                LIMIT 1
                '''
            ),
            {'owner_id': owner_id, 'agent_hasn_id': agent_hasn_id},
        )
        row = result.mappings().first()
        if not row:
            return None
        return RuntimeSummary(
            agent_hasn_id=row['agent_hasn_id'],
            runtime_status=row['runtime_status'],
            adapter_registered=bool(row['adapter_registered']),
            handle_available=bool(row['handle_available']),
            binding_id=row.get('binding_id'),
            runtime_type=row.get('runtime_type'),
            summary_json=_redact_runtime_summary(row.get('summary_json') or {}),
            node_id=row.get('node_id'),
            binding_node_id=(row.get('summary_json') or {}).get('binding_node_id')
            if isinstance(row.get('summary_json'), dict)
            else None,
            presence=(row.get('summary_json') or {}).get('presence')
            if isinstance(row.get('summary_json'), dict)
            else None,
        )

    async def store_suppressed(
        self,
        db: AsyncSession,
        *,
        source_message: StoredMessage,
        reason: str,
        dispatch_status: str,
        runtime_summary: RuntimeSummary | None,
    ) -> None:
        await db.execute(
            sa.text(
                '''
                INSERT INTO public.hasn_suppressed_messages (
                    message_id,
                    owner_id,
                    hasn_id,
                    conversation_id,
                    suppress_reason,
                    dispatch_status,
                    runtime_summary,
                    policy_snapshot,
                    visible_to_owner,
                    created_time
                ) VALUES (
                    CAST(:message_id AS bigint),
                    :owner_id,
                    :hasn_id,
                    CAST(:conversation_id AS uuid),
                    :suppress_reason,
                    :dispatch_status,
                    CAST(:runtime_summary AS jsonb),
                    CAST(:policy_snapshot AS jsonb),
                    true,
                    now()
                )
                ON CONFLICT (message_id) DO UPDATE SET
                    suppress_reason = EXCLUDED.suppress_reason,
                    dispatch_status = EXCLUDED.dispatch_status,
                    runtime_summary = EXCLUDED.runtime_summary,
                    visible_to_owner = true,
                    updated_time = now()
                '''
            ),
            {
                'message_id': source_message.message_id,
                'owner_id': source_message.owner_id,
                'hasn_id': source_message.hasn_id,
                'conversation_id': source_message.conversation_id,
                'suppress_reason': reason,
                'dispatch_status': dispatch_status,
                'runtime_summary': json.dumps(
                    _public_runtime_summary(runtime_summary) if runtime_summary else {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                'policy_snapshot': json.dumps({'runtime_optional': False}, ensure_ascii=False, sort_keys=True),
            },
        )

    async def pull_inbox(
        self, db: AsyncSession, request: InboxPullRequest, *, limit: int = 100
    ) -> InboxPullResponse:
        cursor_id = _parse_cursor(request.cursor)
        message_rows = await self._pull_message_rows(db, request.owner_id, cursor_id, limit + 1)
        items = [_inbox_item_from_message_row(row) for row in message_rows[:limit]]
        suppressed_rows: list[dict[str, Any]] = []

        if request.include_suppressed:
            suppressed_rows = await self._pull_suppressed_rows(db, request.owner_id, cursor_id, limit + 1)
            items.extend(_inbox_item_from_suppressed_row(row) for row in suppressed_rows[:limit])
            items.sort(key=lambda item: (item.created_at, item.inbox_kind, item.message_id))
            items = items[:limit]

        has_more = len(message_rows) > limit
        if request.include_suppressed:
            has_more = has_more or len(suppressed_rows) > limit
        next_cursor = f's4:{items[-1].message_id}' if items else (request.cursor or 's4:0')
        return InboxPullResponse(items=items, next_cursor=next_cursor, has_more=has_more)

    async def _pull_message_rows(
        self, db: AsyncSession, owner_id: str, cursor_id: int | None, limit: int
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            sa.text(
                '''
                SELECT id,
                       owner_id,
                       hasn_id,
                       conversation_id::text AS conversation_id,
                       owner_copy_of_message_id,
                       content,
                       process_blocks,
                       context,
                       dispatch_status,
                       created_time
                FROM public.hasn_messages
                WHERE owner_id = :owner_id
                  AND (CAST(:cursor_id AS bigint) IS NULL OR id > CAST(:cursor_id AS bigint))
                ORDER BY id ASC
                LIMIT :limit
                '''
            ),
            {'owner_id': owner_id, 'cursor_id': cursor_id, 'limit': limit},
        )
        return list(result.mappings().all())

    async def mark_dispatch_status(
        self, db: AsyncSession, *, message_id: str, dispatch_status: str
    ) -> None:
        await db.execute(
            sa.text(
                '''
                UPDATE public.hasn_messages
                SET dispatch_status = :dispatch_status,
                    updated_time = now()
                WHERE id = CAST(:message_id AS bigint)
                   OR owner_copy_of_message_id = CAST(:message_id AS bigint)
                '''
            ),
            {'message_id': message_id, 'dispatch_status': dispatch_status},
        )

    async def _pull_suppressed_rows(
        self, db: AsyncSession, owner_id: str, cursor_id: int | None, limit: int
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            sa.text(
                '''
                SELECT s.id,
                       s.message_id,
                       s.owner_id,
                       s.hasn_id,
                       s.conversation_id::text AS conversation_id,
                       s.dispatch_status,
                       s.created_time,
                       m.context,
                       m.content,
                       m.process_blocks
                FROM public.hasn_suppressed_messages s
                LEFT JOIN public.hasn_messages m ON m.id = s.message_id
                WHERE s.owner_id = :owner_id
                  AND s.visible_to_owner = true
                  AND (CAST(:cursor_id AS bigint) IS NULL OR s.message_id > CAST(:cursor_id AS bigint))
                ORDER BY s.message_id ASC
                LIMIT :limit
                '''
            ),
            {'owner_id': owner_id, 'cursor_id': cursor_id, 'limit': limit},
        )
        return list(result.mappings().all())


@dataclass(slots=True)
class HasnMessageHubService:
    gateway: MessageHubGateway = field(default_factory=SqlAlchemyMessageHubGateway)
    fanout: FanoutGateway = field(default_factory=WsFanoutGateway)
    runtime_dispatcher: RuntimeDispatcher = field(default_factory=WsRuntimeDispatcher)
    side_effect_dispatcher: SideEffectDispatcher = field(default_factory=NoopServerSideEffectDispatcher)

    async def send(self, db: AsyncSession, request: MessageHubSendRequest) -> MessageHubSendResponse:
        envelope = _normalize_envelope(request.envelope, request.owner_id)
        _validate_message_content(envelope)
        sender_id = _sender_id(envelope, request.owner_id)
        target_id = _target_id(envelope)
        if not target_id:
            raise errors.RequestError(msg='HASN envelope 缺少目标 hasn_id')
        if target_id.startswith('g:'):
            raise errors.RequestError(msg='S4 message hub 暂不处理群组目标；群聊仍由现有 IM router 承接')

        recipient = await self.gateway.resolve_recipient(db, target_id)
        if recipient is None:
            raise errors.NotFoundError(msg='HASN message target not found')

        conversation_id = _conversation_id(envelope, sender_id, target_id)
        envelope['conversation_id'] = conversation_id
        envelope.setdefault('from_id', sender_id)
        envelope.setdefault('to_id', target_id)
        envelope.setdefault('owner_id', request.owner_id)

        dispatch_status = 'not_required'
        runtime: RuntimeSummary | None = None
        if recipient.entity_type == 'agent':
            runtime = await self.gateway.latest_runtime_summary(
                db,
                owner_id=recipient.owner_id,
                agent_hasn_id=recipient.hasn_id,
            )
            if runtime is None or not runtime.is_reachable:
                dispatch_status = 'runtime_unavailable'
            else:
                dispatch_status = 'dispatched'

        primary_kind = 'agent_inbox' if recipient.entity_type == 'agent' else 'human_inbox'
        primary = await self.gateway.store_inbox_message(
            db,
            MessageRecord(
                conversation_id=conversation_id,
                owner_id=recipient.owner_id,
                hasn_id=recipient.hasn_id,
                from_id=sender_id,
                to_id=recipient.hasn_id,
                content=_content(envelope),
                process_blocks=_process_blocks(envelope),
                envelope=envelope,
                inbox_kind=primary_kind,
                dispatch_status=dispatch_status,
                runtime_summary=runtime,
                msg_type=_msg_type(envelope),
                content_type=_content_type(envelope),
                priority=_priority(envelope),
                client_message_id=_client_message_id(envelope),
            ),
        )

        owner_copy: StoredMessage | None = None
        if recipient.entity_type == 'agent':
            owner_copy = await self.gateway.store_inbox_message(
                db,
                MessageRecord(
                    conversation_id=conversation_id,
                    owner_id=recipient.owner_id,
                    hasn_id=recipient.owner_id,
                    from_id=sender_id,
                    to_id=recipient.hasn_id,
                    content=_content(envelope),
                    process_blocks=_process_blocks(envelope),
                    envelope=envelope,
                    inbox_kind='owner_copy',
                    dispatch_status=dispatch_status,
                    owner_copy_of_message_id=primary.message_id,
                    runtime_summary=runtime,
                    msg_type=_msg_type(envelope),
                    content_type=_content_type(envelope),
                    priority=_priority(envelope),
                    client_message_id=_client_message_id(envelope),
                ),
            )

        warnings: list[ErrorObject] = []
        suppressed_created = False
        payload = _message_received_payload(primary, recipient, envelope)
        await self.fanout.push(recipient.hasn_id, payload)
        if owner_copy:
            await self.fanout.push(recipient.owner_id, _message_received_payload(owner_copy, recipient, envelope))

        if recipient.entity_type == 'agent' and dispatch_status == 'runtime_unavailable':
            await self.gateway.store_suppressed(
                db,
                source_message=primary,
                reason='runtime_unavailable',
                dispatch_status=dispatch_status,
                runtime_summary=runtime,
            )
            suppressed_created = True
            warnings.append(_RUNTIME_UNAVAILABLE_WARNING)

        if recipient.entity_type == 'agent' and dispatch_status == 'dispatched' and runtime:
            try:
                dispatched = await self.runtime_dispatcher.dispatch(recipient.hasn_id, payload, runtime)
            except Exception:
                dispatched = False
            if not dispatched:
                dispatch_status = 'dispatch_failed'
                primary.dispatch_status = dispatch_status
                if owner_copy:
                    owner_copy.dispatch_status = dispatch_status
                await self.gateway.mark_dispatch_status(
                    db,
                    message_id=primary.message_id,
                    dispatch_status=dispatch_status,
                )
                await self.gateway.store_suppressed(
                    db,
                    source_message=primary,
                    reason='runtime_unavailable_after_accept',
                    dispatch_status=dispatch_status,
                    runtime_summary=runtime,
                )
                suppressed_created = True
                warnings.append(_RUNTIME_DISPATCH_FAILED_WARNING)

        if suppressed_created:
            await self.fanout.push(recipient.owner_id, _runtime_warning_payload(primary, envelope))

        await self._dispatch_side_effect(envelope, primary)

        return MessageHubSendResponse(
            message_id=primary.message_id,
            conversation_id=conversation_id,
            delivery_status='delivered',
            dispatch_status=dispatch_status,
            owner_copy_created=owner_copy is not None,
            suppressed_inbox_created=suppressed_created,
            warnings=warnings,
        )

    async def pull_inbox(self, db: AsyncSession, request: InboxPullRequest) -> InboxPullResponse:
        return await self.gateway.pull_inbox(db, request)

    async def _dispatch_side_effect(self, envelope: dict[str, Any], message: StoredMessage) -> None:
        try:
            await self.side_effect_dispatcher.dispatch(envelope, message)
        except Exception:
            # S4 side effects must never block the message path. Concrete retry/audit belongs to later phases.
            return


def _normalize_envelope(envelope: dict[str, Any], owner_id: str) -> dict[str, Any]:
    normalized = _redact_private_runtime_fields(copy.deepcopy(envelope or {}))
    normalized.setdefault('owner_id', owner_id)
    normalized.pop('node_role', None)
    return normalized


def _sender_id(envelope: dict[str, Any], owner_id: str) -> str:
    return str(
        envelope.get('from_id')
        or envelope.get('sender_hasn_id')
        or envelope.get('sender', {}).get('hasn_id')
        or envelope.get('source', {}).get('hasn_id')
        or owner_id
    )


def _target_id(envelope: dict[str, Any]) -> str | None:
    target = envelope.get('to_id') or envelope.get('recipient_hasn_id')
    if target:
        return str(target)
    target_block = envelope.get('target') or envelope.get('recipient') or {}
    if isinstance(target_block, dict):
        value = target_block.get('hasn_id') or target_block.get('id')
        if value:
            return str(value)
    return None


def _conversation_id(envelope: dict[str, Any], sender_id: str, target_id: str) -> str:
    value = envelope.get('conversation_id')
    if value:
        return str(value)
    low, high = sorted([sender_id, target_id])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'hasn:conversation:{low}:{high}'))


def _content(envelope: dict[str, Any]) -> dict[str, Any]:
    content = envelope.get('content') or envelope.get('message_content') or {}
    return content if isinstance(content, dict) else {'text': str(content)}


def _process_blocks(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    raw = envelope.get('process_blocks')
    if raw is None:
        raw = envelope.get('process')
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _msg_type(envelope: dict[str, Any]) -> str:
    return str(envelope.get('msg_type') or envelope.get('type') or envelope.get('method') or 'message')[:30]


def _content_type(envelope: dict[str, Any]) -> int:
    raw = envelope.get('content_type', 1)
    if isinstance(raw, int):
        return raw
    mapping = {'text': 1, 'image': 2, 'file': 3, 'voice': 4, 'card': 5}
    return mapping.get(str(raw), 1)


def _validate_message_content(envelope: dict[str, Any]) -> None:
    if _content_type(envelope) == 5:
        validate_card_message_body(_content(envelope))


def _priority(envelope: dict[str, Any]) -> str:
    priority = str(envelope.get('priority') or 'normal')
    return priority if priority in {'critical', 'high', 'normal', 'low'} else 'normal'


def _client_message_id(envelope: dict[str, Any]) -> str | None:
    value = envelope.get('client_message_id') or envelope.get('local_id')
    return str(value) if value else None


def _entity_type_int(hasn_id: str) -> int:
    if hasn_id.startswith('h_'):
        return 1
    if hasn_id.startswith('a_'):
        return 2
    if hasn_id.startswith('g:'):
        return 4
    if hasn_id.startswith('sv_'):
        return 5  # service（服务号，统一通知服务 D8）
    return 3


def _redact_runtime_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dict(summary).items() if k not in PRIVATE_RUNTIME_KEYS}


def _redact_private_runtime_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop Runtime-private metadata without altering user message content."""
    redacted: dict[str, Any] = {}
    metadata_keys = {'runtime', 'runtime_summary', 'node', 'node_info', 'metadata'}
    for key, value in payload.items():
        if key in PRIVATE_RUNTIME_KEYS:
            continue
        if key in metadata_keys and isinstance(value, dict):
            redacted[key] = _redact_private_runtime_fields(value)
            continue
        redacted[key] = value
    return redacted


def _public_runtime_summary(runtime: RuntimeSummary | None) -> dict[str, Any]:
    if runtime is None:
        return {}
    return {
        'agent_hasn_id': runtime.agent_hasn_id,
        'runtime_status': runtime.runtime_status,
        'adapter_registered': runtime.adapter_registered,
        'handle_available': runtime.handle_available,
        'binding_id': runtime.binding_id,
        'runtime_type': runtime.runtime_type,
        'summary_json': _redact_runtime_summary(runtime.summary_json),
    }


def _message_received_payload(
    message: StoredMessage, recipient: Recipient, envelope: dict[str, Any]
) -> dict[str, Any]:
    return {
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.received',
        'params': {
            'to_id': message.hasn_id,
            'owner_id': message.owner_id,
            'inbox_kind': message.inbox_kind,
            'message': _envelope_for_delivery(message, recipient, envelope),
        },
    }


def _envelope_for_delivery(
    message: StoredMessage, recipient: Recipient, envelope: dict[str, Any]
) -> dict[str, Any]:
    delivered = copy.deepcopy(envelope)
    delivered.update(
        {
            'id': message.message_id,
            'message_id': message.message_id,
            'conversation_id': message.conversation_id,
            'owner_id': message.owner_id,
            'hasn_id': message.hasn_id,
            'to_owner_id': recipient.owner_id,
            'dispatch_status': message.dispatch_status,
            'process_blocks': _process_blocks(envelope),
        }
    )
    delivered.pop('node_role', None)
    return delivered


def _runtime_warning_payload(message: StoredMessage, envelope: dict[str, Any]) -> dict[str, Any]:
    warning = (
        _RUNTIME_DISPATCH_FAILED_WARNING
        if message.dispatch_status == 'dispatch_failed'
        else _RUNTIME_UNAVAILABLE_WARNING
    )
    return {
        'hasn': 'hasn/0.2',
        'method': 'hasn.runtime.warning',
        'params': {
            'owner_id': message.owner_id,
            'message_id': message.message_id,
            'conversation_id': message.conversation_id,
            'dispatch_status': message.dispatch_status,
            'warning': warning.model_dump(),
            'message': _envelope_for_delivery(
                message,
                Recipient(message.hasn_id, 'agent', message.owner_id),
                envelope,
            ),
        },
    }


def _parse_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        return int(str(cursor).rsplit(':', maxsplit=1)[-1])
    except (TypeError, ValueError):
        return None


def _row_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _row_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _row_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return timezone.now()


def _inbox_item_from_message_row(row: dict[str, Any]) -> InboxItem:
    context = _row_json(row.get('context'))
    inbox_kind = context.get('s4_inbox_kind')
    if not inbox_kind:
        inbox_kind = 'owner_copy' if row.get('owner_copy_of_message_id') else _inbox_kind_for_hasn(row['hasn_id'])
    envelope = context.get('s4_envelope') or {'content': _row_json(row.get('content'))}
    envelope = copy.deepcopy(envelope)
    envelope['process_blocks'] = _row_list(row.get('process_blocks'))
    envelope.update(
        {
            'message_id': str(row['id']),
            'conversation_id': str(row['conversation_id']),
            'owner_id': row['owner_id'],
            'hasn_id': row['hasn_id'],
            'dispatch_status': row['dispatch_status'],
        }
    )
    envelope.pop('node_role', None)
    return InboxItem(
        message_id=str(row['id']),
        owner_id=row['owner_id'],
        hasn_id=row['hasn_id'],
        conversation_id=str(row['conversation_id']),
        inbox_kind=inbox_kind,
        envelope=envelope,
        dispatch_status=row['dispatch_status'],
        created_at=_row_datetime(row.get('created_time')),
    )


def _inbox_item_from_suppressed_row(row: dict[str, Any]) -> InboxItem:
    context = _row_json(row.get('context'))
    envelope = context.get('s4_envelope') or {'content': _row_json(row.get('content'))}
    envelope = copy.deepcopy(envelope)
    envelope['process_blocks'] = _row_list(row.get('process_blocks'))
    envelope.update(
        {
            'message_id': str(row['message_id']),
            'conversation_id': str(row['conversation_id']),
            'owner_id': row['owner_id'],
            'hasn_id': row['hasn_id'],
            'dispatch_status': row['dispatch_status'],
        }
    )
    envelope.pop('node_role', None)
    return InboxItem(
        message_id=str(row['message_id']),
        owner_id=row['owner_id'],
        hasn_id=row['hasn_id'],
        conversation_id=str(row['conversation_id']),
        inbox_kind='suppressed_inbox',
        envelope=envelope,
        dispatch_status=row['dispatch_status'],
        created_at=_row_datetime(row.get('created_time')),
    )


def _inbox_kind_for_hasn(hasn_id: str) -> str:
    return 'agent_inbox' if str(hasn_id).startswith('a_') else 'human_inbox'


def _side_effect_action(envelope: dict[str, Any]) -> str | None:
    return envelope.get('action') or envelope.get('method') or envelope.get('event_type')


hasn_message_hub_service = HasnMessageHubService()
