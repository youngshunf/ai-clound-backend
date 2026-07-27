"""身份事实到 IM 关系域的可靠命令 outbox。"""

from __future__ import annotations

import logging
import uuid

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.hasn.crud.crud_hasn_relation_command_outbox import hasn_relation_command_outbox_dao
from backend.app.hasn.model import HasnRelationCommandOutbox
from backend.app.hasn.schema.hasn_relation_command_outbox import (
    CreateHasnRelationCommandOutboxParam,
    DeleteHasnRelationCommandOutboxParam,
    UpdateHasnRelationCommandOutboxParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone

log = logging.getLogger(__name__)
_CONTROL_EDGE_COMMAND = 'ensure_owner_agent_control_edge'
_DEFAULT_BACKOFF_SECONDS = (1, 5, 30, 120, 600)
_DEFAULT_MAX_ATTEMPTS = 5
_LEASE_SECONDS = 120


@dataclass(frozen=True)
class RelationOutboxStats:
    """单轮关系 outbox 投递统计。"""

    claimed: int = 0
    completed: int = 0
    retried: int = 0
    dead_lettered: int = 0
    # 与 IM consumer 统一 relay 统计契约对齐；关系命令的幂等发生在数据库 upsert 内。
    deduped: int = 0


@dataclass(frozen=True)
class _ClaimedCommand:
    """脱离领取事务后仍可安全使用的命令快照。"""

    command_id: str
    command_type: str
    owner_hasn_id: str
    peer_hasn_id: str
    attempt_count: int


class ControlEdgeWriter(Protocol):
    """关系 outbox relay 实际依赖的最小控制边写能力。"""

    async def ensure_owner_agent_control_edge(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
    ) -> dict[str, Any]: ...


class HasnRelationCommandOutboxService:
    @staticmethod
    async def enqueue_owner_agent_control_edge(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
    ) -> str:
        """与 Agent 身份写同事务登记控制边投影命令。

        独立 IM consumer 每 0.5 秒扫描本 outbox；Celery beat 仍保留分钟级灾备扫描。
        事务提交回调不得同步导入 Celery 并连接 broker，否则登录请求会被 broker 首连阻塞。
        """
        command_id = str(uuid.uuid4())
        idempotency_key = f'owner-agent-control:{owner_hasn_id}:{agent_hasn_id}'
        result = await db.execute(
            pg_insert(HasnRelationCommandOutbox)
            .values(
                command_id=command_id,
                command_type=_CONTROL_EDGE_COMMAND,
                owner_hasn_id=owner_hasn_id,
                peer_hasn_id=agent_hasn_id,
                idempotency_key=idempotency_key,
                status='pending',
                attempt_count=0,
                next_retry_at=timezone.now(),
            )
            .on_conflict_do_nothing(index_elements=['idempotency_key'])
            .returning(HasnRelationCommandOutbox.command_id)
        )
        persisted_id = result.scalar_one_or_none()
        if persisted_id is None:
            persisted_id = await db.scalar(
                sa.select(HasnRelationCommandOutbox.command_id).where(
                    HasnRelationCommandOutbox.idempotency_key == idempotency_key,
                )
            )
        if persisted_id is None:
            raise RuntimeError('控制边 outbox 幂等写入后无法读取命令')
        return str(persisted_id)

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnRelationCommandOutbox:
        """
        获取身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param pk: 身份事实投影为 IM 关系的可靠命令队列 ID
        :return:
        """
        hasn_relation_command_outbox = await hasn_relation_command_outbox_dao.get(db, pk)
        if not hasn_relation_command_outbox:
            raise errors.NotFoundError(msg='身份事实投影为 IM 关系的可靠命令队列不存在')
        return hasn_relation_command_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取身份事实投影为 IM 关系的可靠命令队列列表

        :param db: 数据库会话
        :return:
        """
        hasn_relation_command_outbox_select = await hasn_relation_command_outbox_dao.get_select()
        return await paging_data(db, hasn_relation_command_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnRelationCommandOutbox]:
        """
        获取所有身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :return:
        """
        hasn_relation_command_outbox_list = await hasn_relation_command_outbox_dao.get_all(db)
        return hasn_relation_command_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnRelationCommandOutboxParam) -> None:
        """
        创建身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param obj: 创建身份事实投影为 IM 关系的可靠命令队列参数
        :return:
        """
        await hasn_relation_command_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnRelationCommandOutboxParam) -> int:
        """
        更新身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param pk: 身份事实投影为 IM 关系的可靠命令队列 ID
        :param obj: 更新身份事实投影为 IM 关系的可靠命令队列参数
        :return:
        """
        count = await hasn_relation_command_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnRelationCommandOutboxParam) -> int:
        """
        删除身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param obj: 身份事实投影为 IM 关系的可靠命令队列 ID 列表
        :return:
        """
        count = await hasn_relation_command_outbox_dao.delete(db, obj.pks)
        return count


hasn_relation_command_outbox_service: HasnRelationCommandOutboxService = HasnRelationCommandOutboxService()


class RelationCommandOutboxRelay:
    """以租约领取身份域命令，并只经 RelationGateway 投影控制边。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        relation_gateway: ControlEdgeWriter,
        backoff_seconds: tuple[int, ...] = _DEFAULT_BACKOFF_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._session_factory = session_factory
        self._relation_gateway = relation_gateway
        self._backoff_seconds = backoff_seconds
        self._max_attempts = max_attempts

    async def drain_once(
        self,
        *,
        now: datetime | int | None = None,
        batch_limit: int = 50,
    ) -> RelationOutboxStats:
        """投递一批到期命令；每条独立确认、退避或进入死信。"""
        resolved_now = (
            timezone.to_utc(now)
            if isinstance(now, int)
            else now or timezone.now()
        )
        commands = await self._claim_batch(
            now=resolved_now,
            batch_limit=batch_limit,
        )
        completed = retried = dead_lettered = 0
        for command in commands:
            try:
                if command.command_type != _CONTROL_EDGE_COMMAND:
                    raise ValueError(f'不支持的关系命令：{command.command_type}')
                await self._relation_gateway.ensure_owner_agent_control_edge(
                    owner_hasn_id=command.owner_hasn_id,
                    agent_hasn_id=command.peer_hasn_id,
                )
            except Exception as exc:
                terminal = await self._mark_failure(
                    command,
                    now=resolved_now,
                    error=repr(exc),
                )
                if terminal:
                    dead_lettered += 1
                else:
                    retried += 1
                continue
            await self._mark_completed(command.command_id, now=resolved_now)
            completed += 1
        return RelationOutboxStats(
            claimed=len(commands),
            completed=completed,
            retried=retried,
            dead_lettered=dead_lettered,
        )

    async def _claim_batch(
        self,
        *,
        now: datetime,
        batch_limit: int,
    ) -> list[_ClaimedCommand]:
        """领取 pending 或租约过期的 processing 命令。"""
        async with self._session_factory.begin() as db:
            rows = (
                await db.execute(
                    sa.select(HasnRelationCommandOutbox)
                    .where(
                        sa.or_(
                            sa.and_(
                                HasnRelationCommandOutbox.status == 'pending',
                                HasnRelationCommandOutbox.next_retry_at <= now,
                            ),
                            sa.and_(
                                HasnRelationCommandOutbox.status == 'processing',
                                HasnRelationCommandOutbox.lease_until <= now,
                            ),
                        )
                    )
                    .order_by(HasnRelationCommandOutbox.id)
                    .limit(batch_limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            lease_until = now + timedelta(seconds=_LEASE_SECONDS)
            commands: list[_ClaimedCommand] = []
            for row in rows:
                row.status = 'processing'
                row.lease_until = lease_until
                row.updated_time = now
                commands.append(
                    _ClaimedCommand(
                        command_id=row.command_id,
                        command_type=row.command_type,
                        owner_hasn_id=row.owner_hasn_id,
                        peer_hasn_id=row.peer_hasn_id,
                        attempt_count=row.attempt_count,
                    )
                )
            return commands

    async def _mark_completed(self, command_id: str, *, now: datetime) -> None:
        """幂等确认已完成命令。"""
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.update(HasnRelationCommandOutbox)
                .where(
                    HasnRelationCommandOutbox.command_id == command_id,
                    HasnRelationCommandOutbox.status == 'processing',
                )
                .values(
                    status='completed',
                    completed_at=now,
                    lease_until=None,
                    last_error=None,
                    updated_time=now,
                )
            )

    async def _mark_failure(
        self,
        command: _ClaimedCommand,
        *,
        now: datetime,
        error: str,
    ) -> bool:
        """记录一次失败；达到上限时进入终局死信。"""
        attempts = command.attempt_count + 1
        terminal = attempts >= self._max_attempts
        if terminal:
            status = 'dead_letter'
            next_retry_at = now
            log.error(
                '关系 outbox 命令进入死信（command=%s attempts=%d）：%s',
                command.command_id,
                attempts,
                error,
            )
        else:
            status = 'pending'
            delay = self._backoff_seconds[
                min(attempts, len(self._backoff_seconds)) - 1
            ]
            next_retry_at = now + timedelta(seconds=delay)
            log.warning(
                '关系 outbox 命令失败，将退避重试（command=%s attempts=%d）：%s',
                command.command_id,
                attempts,
                error,
            )
        async with self._session_factory.begin() as db:
            await db.execute(
                sa.update(HasnRelationCommandOutbox)
                .where(
                    HasnRelationCommandOutbox.command_id == command.command_id,
                    HasnRelationCommandOutbox.status == 'processing',
                )
                .values(
                    status=status,
                    attempt_count=attempts,
                    next_retry_at=next_retry_at,
                    lease_until=None,
                    last_error=error[:4000],
                    updated_time=now,
                )
            )
        return terminal
