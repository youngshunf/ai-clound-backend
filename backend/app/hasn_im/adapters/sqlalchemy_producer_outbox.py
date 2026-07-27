"""生产方自有消息 outbox 的通用 SQLAlchemy 存储适配器。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.ports.dto import (
    ActorKind,
    SendMessageCommand,
    ServicePrincipal,
)
from backend.app.hasn_im.ports.outbox import OutboxRecord

_IDENTIFIER_RE = re.compile(r'^[a-z][a-z0-9_]*$')
_COMMAND_TYPE = 'send_message'
_PAYLOAD_VERSION = 1
_LEASE_SECONDS = 120


@dataclass(frozen=True, slots=True)
class ProducerOutboxTable:
    """一个生产方自有 outbox 表的受信任描述。"""

    schema: str
    table: str
    producer: str

    def __post_init__(self) -> None:
        """仅允许代码内声明的普通 PostgreSQL 标识符。"""
        for name, value in (
            ('schema', self.schema),
            ('table', self.table),
            ('producer', self.producer),
        ):
            if not _IDENTIFIER_RE.fullmatch(value):
                raise ValueError(f'非法生产 outbox {name}：{value!r}')

    @property
    def qualified_name(self) -> str:
        """返回安全引用的 schema.table。"""
        return f'"{self.schema}"."{self.table}"'


def _payload_for(
    command: SendMessageCommand,
    principal: ServicePrincipal,
) -> dict[str, Any]:
    """把发送命令与受信任主体编码为版本化 JSON。"""
    return {
        'version': _PAYLOAD_VERSION,
        'principal': {
            'canonical_sender': principal.canonical_sender,
            'actor_kind': principal.actor_kind.value,
            'origin_node_id': principal.origin_node_id,
            'send_as': principal.send_as,
            'origin_session_id': principal.origin_session_id,
        },
        'message': {
            'content': command.content,
            'content_type': command.content_type,
            'msg_type': command.msg_type,
            'priority': command.priority,
            'reply_to_id': command.reply_to_id,
            'context': command.context,
            'mentions': command.mentions,
            'mention_all': command.mention_all,
        },
    }


def _canonical_json(value: dict[str, Any]) -> str:
    """生成稳定 JSON，作为 payload hash 的唯一输入。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )


async def enqueue_send_message(
    db: AsyncSession,
    *,
    table: ProducerOutboxTable,
    command: SendMessageCommand,
    principal: ServicePrincipal,
    trace_id: str | None = None,
    causation_id: str | None = None,
) -> str:
    """在调用方业务事务内幂等登记一条发送命令。"""
    if not command.idempotency_key:
        raise ValueError('生产 outbox 命令必须提供稳定 idempotency_key')
    try:
        conversation_id = str(uuid.UUID(command.conversation_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('生产 outbox 命令必须提供 UUID conversation_id') from exc

    payload = _payload_for(command, principal)
    canonical_payload = _canonical_json(payload)
    payload_hash = hashlib.sha256(
        f'{conversation_id}\n{_COMMAND_TYPE}\n{canonical_payload}'.encode()
    ).hexdigest()
    command_id = str(uuid.uuid4())
    qualified = table.qualified_name
    inserted = (
        await db.execute(
            sa.text(
                f'INSERT INTO {qualified} ('  # noqa: S608 表名来自受校验的代码常量
                'command_id, producer, conversation_id, command_type, payload, '
                'payload_hash, idempotency_key, status, attempt_count, next_attempt_at, '
                'trace_id, causation_id'
                ') VALUES ('
                ':command_id, :producer, CAST(:conversation_id AS uuid), :command_type, '
                'CAST(:payload AS jsonb), :payload_hash, :idempotency_key, '
                "'pending', 0, now(), :trace_id, :causation_id"
                ') ON CONFLICT (idempotency_key) DO NOTHING '
                'RETURNING command_id'
            ),
            {
                'command_id': command_id,
                'producer': table.producer,
                'conversation_id': conversation_id,
                'command_type': _COMMAND_TYPE,
                'payload': canonical_payload,
                'payload_hash': payload_hash,
                'idempotency_key': command.idempotency_key,
                'trace_id': trace_id,
                'causation_id': causation_id,
            },
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return str(inserted)

    existing = (
        await db.execute(
            sa.text(
                f'SELECT command_id, payload_hash FROM {qualified} '  # noqa: S608
                'WHERE idempotency_key = :idempotency_key'
            ),
            {'idempotency_key': command.idempotency_key},
        )
    ).one_or_none()
    if existing is None:
        raise RuntimeError('生产 outbox 幂等写入后无法读取命令')
    if str(existing.payload_hash).strip() != payload_hash:
        raise ValueError(
            f'生产 outbox 幂等键冲突：{command.idempotency_key}'
        )
    return str(existing.command_id)


def build_send_message_command(
    record: OutboxRecord,
) -> tuple[SendMessageCommand, ServicePrincipal]:
    """从公共版本化 payload 还原严格发送命令。"""
    payload = record.payload
    if payload.get('version') != _PAYLOAD_VERSION:
        raise ValueError('不支持的生产 outbox payload 版本')
    principal_data = payload.get('principal')
    message_data = payload.get('message')
    if not isinstance(principal_data, dict) or not isinstance(message_data, dict):
        raise ValueError('生产 outbox payload 缺少 principal 或 message')
    content = message_data.get('content')
    if not isinstance(content, dict):
        raise ValueError('生产 outbox message.content 必须是对象')

    principal = ServicePrincipal(
        canonical_sender=str(principal_data['canonical_sender']),
        actor_kind=ActorKind(str(principal_data['actor_kind'])),
        origin_node_id=principal_data.get('origin_node_id'),
        send_as=principal_data.get('send_as'),
        origin_session_id=principal_data.get('origin_session_id'),
    )
    command = SendMessageCommand(
        conversation_id=record.conversation_id,
        content=content,
        content_type=int(message_data.get('content_type', 1)),
        idempotency_key=record.idempotency_key,
        msg_type=str(message_data.get('msg_type') or 'message'),
        priority=str(message_data.get('priority') or 'normal'),
        reply_to_id=message_data.get('reply_to_id'),
        context=message_data.get('context'),
        mentions=message_data.get('mentions'),
        mention_all=bool(message_data.get('mention_all', False)),
    )
    return command, principal


class SQLAlchemyProducerOutboxStore:
    """以租约和 ``FOR UPDATE SKIP LOCKED`` 实现公共 OutboxStore。"""

    def __init__(
        self,
        *,
        table: ProducerOutboxTable,
        session_factory: async_sessionmaker[AsyncSession],
        instance_id: str,
        lease_seconds: int = _LEASE_SECONDS,
    ) -> None:
        if not instance_id:
            raise ValueError('producer relay instance_id 不能为空')
        self._table = table
        self._session_factory = session_factory
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds

    async def claim_batch(self, *, limit: int, now: int) -> list[OutboxRecord]:
        """领取到期 pending 或租约已过期的 processing 命令。"""
        if limit <= 0:
            return []
        qualified = self._table.qualified_name
        async with self._session_factory.begin() as db:
            rows = (
                await db.execute(
                    sa.text(
                        'WITH candidates AS ('
                        f' SELECT id FROM {qualified}'  # noqa: S608
                        ' WHERE ('
                        "   (status = 'pending' AND next_attempt_at <= to_timestamp(:now))"
                        '   OR '
                        "   (status = 'processing' AND lease_until <= to_timestamp(:now))"
                        ' )'
                        ' ORDER BY id'
                        ' LIMIT :limit'
                        ' FOR UPDATE SKIP LOCKED'
                        ') '
                        f'UPDATE {qualified} AS outbox '  # noqa: S608
                        "SET status = 'processing', "
                        'lease_until = to_timestamp(:now) + make_interval(secs => :lease_seconds), '
                        'locked_by = :instance_id, updated_time = now() '
                        'FROM candidates '
                        'WHERE outbox.id = candidates.id '
                        'RETURNING outbox.command_id, outbox.producer, '
                        'outbox.conversation_id::text AS conversation_id, '
                        'outbox.command_type, outbox.payload, outbox.idempotency_key, '
                        'outbox.attempt_count, outbox.trace_id, outbox.causation_id'
                    ),
                    {
                        'now': now,
                        'limit': limit,
                        'lease_seconds': self._lease_seconds,
                        'instance_id': self._instance_id,
                    },
                )
            ).all()
        return [
            OutboxRecord(
                command_id=str(row.command_id),
                producer=str(row.producer),
                conversation_id=str(row.conversation_id),
                command_type=str(row.command_type),
                payload=dict(row.payload),
                idempotency_key=str(row.idempotency_key),
                attempts=int(row.attempt_count),
                trace_id=row.trace_id,
                causation_id=row.causation_id,
            )
            for row in rows
        ]

    async def mark_completed(
        self,
        command_id: str,
        *,
        message_id: int | None,
    ) -> None:
        """仅由当前租约持有者确认成功；重复确认保持幂等。"""
        qualified = self._table.qualified_name
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.text(
                    f'UPDATE {qualified} '  # noqa: S608
                    "SET status = 'completed', message_id = :message_id, "
                    'completed_at = COALESCE(completed_at, now()), '
                    'lease_until = NULL, locked_by = NULL, last_error = NULL, '
                    'updated_time = now() '
                    'WHERE command_id = :command_id AND ('
                    "(status = 'processing' AND locked_by = :instance_id) "
                    "OR status = 'completed'"
                    ')'
                ),
                {
                    'command_id': command_id,
                    'instance_id': self._instance_id,
                    'message_id': message_id,
                },
            )

    async def mark_retry(
        self,
        command_id: str,
        *,
        error: str,
        attempts: int,
        next_attempt_at: int,
    ) -> None:
        """记录一次可恢复失败并释放租约。"""
        qualified = self._table.qualified_name
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.text(
                    f'UPDATE {qualified} '  # noqa: S608
                    "SET status = 'pending', attempt_count = :attempts, "
                    'next_attempt_at = to_timestamp(:next_attempt_at), '
                    'lease_until = NULL, locked_by = NULL, last_error = :error, '
                    'updated_time = now() '
                    "WHERE command_id = :command_id AND status = 'processing' "
                    'AND locked_by = :instance_id'
                ),
                {
                    'command_id': command_id,
                    'instance_id': self._instance_id,
                    'attempts': attempts,
                    'next_attempt_at': next_attempt_at,
                    'error': error,
                },
            )

    async def mark_dead_letter(
        self,
        command_id: str,
        *,
        error: str,
        attempts: int,
    ) -> None:
        """记录终局失败并释放租约，供 Runbook 人工重放。"""
        qualified = self._table.qualified_name
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.text(
                    f'UPDATE {qualified} '  # noqa: S608
                    "SET status = 'dead_letter', attempt_count = :attempts, "
                    'lease_until = NULL, locked_by = NULL, last_error = :error, '
                    'updated_time = now() '
                    "WHERE command_id = :command_id AND status = 'processing' "
                    'AND locked_by = :instance_id'
                ),
                {
                    'command_id': command_id,
                    'instance_id': self._instance_id,
                    'attempts': attempts,
                    'error': error,
                },
            )
