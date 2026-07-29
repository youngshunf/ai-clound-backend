"""Agent 产物登记 outbox 的领取、重试和修复逻辑。"""

from __future__ import annotations

import logging

from datetime import timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, DataError, IntegrityError

from backend.app.hasn.model import HasnArtifactContributions, HasnArtifactRegistrationOutbox
from backend.common.exception.errors import BaseExceptionError
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor


logger = logging.getLogger(__name__)
_REPAIR_INTENT_MAX_ATTEMPTS = 8


class ArtifactRegistrationOutboxService:
    """以 lease 保护并发 worker，并将可恢复与终局错误分开表达。"""

    @staticmethod
    def _idempotency_key(contribution: HasnArtifactContributions) -> str:
        """与统一登记服务使用相同的 outbox 幂等键。"""
        return f'{contribution.agent_hasn_id}:{contribution.idempotency_key}'

    @staticmethod
    def _payload(contribution: HasnArtifactContributions) -> dict[str, object]:
        """reconcile 仅保存可审计元数据，绝不复制正文或本地路径。"""
        return {
            'artifact_id': contribution.artifact_id,
            'source_kind': contribution.source_kind,
            'source_tool': contribution.source_tool,
            'source_app_id': contribution.source_app_id,
            'dispatch_id': contribution.dispatch_id,
        }

    async def enqueue_app_resource_repair_intent(
        self,
        db: AsyncSession,
        *,
        descriptor: ResourceDescriptor,
        server_id: str,
        agent_hasn_id: str,
        owner_hasn_id: str,
        title: str,
        summary: str | None,
        source_tool: str,
        work_session_id: str | None,
        project_id: str | None,
        action: str,
        dispatch_id: str | None,
        metadata: dict[str, object] | None = None,
        accumulate_metadata_keys: list[str] | None = None,
    ) -> None:
        """在 best-effort 登记失败后保留可重放的应用资源意图。"""
        app_id = descriptor.resource_kind.split('.', 1)[0]
        resource_uri = descriptor.build_uri(server_id)
        effective_dispatch_id = dispatch_id or f'{app_id}:{server_id}'
        idempotency_key = f'repair:{agent_hasn_id}:{effective_dispatch_id}:{resource_uri}'
        payload: dict[str, object] = {
            'intent_kind': 'app_resource',
            'app_id': app_id,
            'resource_kind': descriptor.resource_kind,
            'server_id': server_id,
            'agent_hasn_id': agent_hasn_id,
            'title': title,
            'summary': summary,
            'source_tool': source_tool,
            'work_session_id': work_session_id,
            'project_id': project_id,
            'action': action,
            'dispatch_id': effective_dispatch_id,
            'metadata': metadata or {},
            'accumulate_metadata_keys': accumulate_metadata_keys or [],
        }
        statement = (
            insert(HasnArtifactRegistrationOutbox)
            .values(
                outbox_id=f'aor_{uuid4().hex}',
                owner_hasn_id=owner_hasn_id,
                artifact_id=None,
                idempotency_key=idempotency_key,
                payload=payload,
                status='pending',
            )
            .on_conflict_do_nothing(index_elements=['owner_hasn_id', 'idempotency_key'])
        )
        await db.execute(statement)

    @staticmethod
    def _intent_value(payload: dict[object, object], key: str) -> str | None:
        """从 JSON 意图读取字符串，遇到坏载荷时明确拒绝重放。"""
        value = payload.get(key)
        return value if isinstance(value, str) else None

    @staticmethod
    def _intent_metadata(payload: dict[object, object]) -> dict[str, object] | None:
        """读取修复意图中的产物元数据，坏载荷不得进入统一登记服务。"""
        value = payload.get('metadata', {})
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            return None
        return cast('dict[str, object]', value)

    @staticmethod
    def _intent_accumulate_keys(payload: dict[object, object]) -> list[str] | None:
        """读取修复意图中的累计计数键，并保持原始顺序。"""
        value = payload.get('accumulate_metadata_keys', [])
        if not isinstance(value, list) or not all(isinstance(key, str) for key in value):
            return None
        return cast('list[str]', value)

    @staticmethod
    def _is_permanent_repair_error(exc: Exception) -> bool:
        """判断修复重放是否遇到不可通过重试解决的契约或数据错误。"""
        if isinstance(exc, (BaseExceptionError, DataError, IntegrityError, ValueError)):
            return True
        if isinstance(exc, DBAPIError):
            sqlstate = getattr(exc.orig, 'sqlstate', '')
            return isinstance(sqlstate, str) and sqlstate.startswith(('22', '23'))
        return False

    async def _reconcile_app_resource_intents(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        limit: int,
    ) -> int:
        """重放业务已成功、首次登记失败后留下的应用资源意图。"""
        now = timezone.now()
        rows = list(
            (
                await db.execute(
                    select(HasnArtifactRegistrationOutbox)
                    .where(HasnArtifactRegistrationOutbox.owner_hasn_id == owner_hasn_id)
                    .where(HasnArtifactRegistrationOutbox.artifact_id.is_(None))
                    .where(HasnArtifactRegistrationOutbox.status == 'pending')
                    .where(HasnArtifactRegistrationOutbox.next_retry_at <= now)
                    .where(HasnArtifactRegistrationOutbox.payload['intent_kind'].astext == 'app_resource')
                    .order_by(
                        HasnArtifactRegistrationOutbox.next_retry_at,
                        HasnArtifactRegistrationOutbox.id,
                    )
                    .limit(max(1, min(limit, 1000)))
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if not rows:
            return 0

        from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
        from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService

        repaired = 0
        for row in rows:
            payload = row.payload
            app_id = self._intent_value(payload, 'app_id')
            resource_kind = self._intent_value(payload, 'resource_kind')
            server_id = self._intent_value(payload, 'server_id')
            agent_hasn_id = self._intent_value(payload, 'agent_hasn_id')
            title = self._intent_value(payload, 'title')
            source_tool = self._intent_value(payload, 'source_tool')
            action = self._intent_value(payload, 'action')
            dispatch_id = self._intent_value(payload, 'dispatch_id')
            summary = self._intent_value(payload, 'summary')
            work_session_id = self._intent_value(payload, 'work_session_id')
            project_id = self._intent_value(payload, 'project_id')
            metadata = self._intent_metadata(payload)
            accumulate_metadata_keys = self._intent_accumulate_keys(payload)
            if (
                app_id is None
                or resource_kind is None
                or server_id is None
                or agent_hasn_id is None
                or title is None
                or source_tool is None
                or action is None
                or dispatch_id is None
                or metadata is None
                or accumulate_metadata_keys is None
            ):
                row.status = 'dead_letter'
                row.last_error = '应用资源修复意图字段不完整'
                row.lease_until = None
                continue
            if action not in ('create', 'update'):
                row.status = 'dead_letter'
                row.last_error = '应用资源修复意图的动作不符合契约'
                row.lease_until = None
                continue

            descriptor = ai_native_app_registry.resource_descriptor(app_id, resource_kind)
            if descriptor is None or descriptor.resource_kind != resource_kind:
                row.status = 'dead_letter'
                row.last_error = '应用资源修复意图引用的资源描述符不存在'
                row.lease_until = None
                continue

            try:
                # 重放与首次 best-effort 登记一样必须隔离在 SAVEPOINT 内：数据库拒绝单条意图时，
                # 外层事务仍能持久化 attempt_count、退避时间或 dead-letter 状态。
                async with db.begin_nested():
                    registered = await HasnArtifactsService.record_app_resource_artifact(
                        db,
                        descriptor=descriptor,
                        server_id=server_id,
                        session_id=work_session_id,
                        agent_hasn_id=agent_hasn_id,
                        owner_hasn_id=owner_hasn_id,
                        title=title,
                        summary=summary,
                        source_tool=source_tool,
                        dispatch_id=dispatch_id,
                        project_id=project_id,
                        action=cast('Literal["create", "update"]', action),
                        metadata=metadata,
                        accumulate_metadata_keys=accumulate_metadata_keys,
                    )
            except Exception as exc:
                row.attempt_count += 1
                if self._is_permanent_repair_error(exc):
                    row.status = 'dead_letter'
                    row.lease_until = None
                    row.last_error = str(exc)
                    logger.error('产物登记修复意图进入死信: outbox_id=%s, reason=%s', row.outbox_id, exc)
                    continue
                if row.attempt_count >= _REPAIR_INTENT_MAX_ATTEMPTS:
                    row.status = 'dead_letter'
                    row.lease_until = None
                    row.last_error = str(exc)
                    logger.error('产物登记修复意图重试耗尽: outbox_id=%s, reason=%s', row.outbox_id, exc)
                    continue
                row.next_retry_at = timezone.now() + timedelta(
                    seconds=min(300, 2 ** min(row.attempt_count, 8))
                )
                row.last_error = str(exc)
                logger.warning('产物登记修复意图将重试: outbox_id=%s, reason=%s', row.outbox_id, exc)
                continue

            row.artifact_id = registered.artifact_id
            row.status = 'completed'
            row.lease_until = None
            row.last_error = None
            repaired += 1

        await db.flush()
        return repaired

    async def claim(
        self,
        db: AsyncSession,
        *,
        limit: int = 100,
        lease_seconds: int = 60,
    ) -> list[HasnArtifactRegistrationOutbox]:
        """原子领取待处理或 lease 已过期的记录，避免多进程重复消费。"""
        now = timezone.now()
        candidates = (
            select(HasnArtifactRegistrationOutbox)
            .where(
                HasnArtifactRegistrationOutbox.artifact_id.is_not(None),
                or_(
                    and_(
                        HasnArtifactRegistrationOutbox.status == 'pending',
                        HasnArtifactRegistrationOutbox.next_retry_at <= now,
                    ),
                    and_(
                        HasnArtifactRegistrationOutbox.status == 'processing',
                        HasnArtifactRegistrationOutbox.lease_until < now,
                    ),
                )
            )
            .order_by(HasnArtifactRegistrationOutbox.next_retry_at, HasnArtifactRegistrationOutbox.id)
            .limit(max(1, min(limit, 500)))
            .with_for_update(skip_locked=True)
        )
        rows = list((await db.execute(candidates)).scalars())
        for row in rows:
            row.status = 'processing'
            row.attempt_count += 1
            row.lease_until = now + timedelta(seconds=max(1, lease_seconds))
        await db.flush()
        return rows

    async def mark_completed(self, db: AsyncSession, *, outbox_id: str) -> bool:
        """确认投递成功并释放 lease。"""
        result = await db.execute(
            update(HasnArtifactRegistrationOutbox)
            .where(HasnArtifactRegistrationOutbox.outbox_id == outbox_id)
            .values(status='completed', lease_until=None, last_error=None, updated_time=func.now())
        )
        return bool(getattr(result, 'rowcount', 0))

    async def mark_retry(
        self,
        db: AsyncSession,
        *,
        outbox_id: str,
        reason: str,
        contract_error: bool = False,
    ) -> bool:
        """4xx 契约错误进入 dead-letter；其他失败按指数退避保留重试机会。"""
        row = (
            await db.execute(
                select(HasnArtifactRegistrationOutbox)
                .where(HasnArtifactRegistrationOutbox.outbox_id == outbox_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        if contract_error:
            row.status = 'dead_letter'
            row.lease_until = None
            row.last_error = reason
            await db.flush()
            return True

        seconds = min(300, 2 ** min(row.attempt_count, 8))
        row.status = 'pending'
        row.lease_until = None
        row.next_retry_at = timezone.now() + timedelta(seconds=seconds)
        row.last_error = reason
        await db.flush()
        return True

    async def reconcile(self, db: AsyncSession, *, owner_hasn_id: str, limit: int = 500) -> int:
        """重放失败意图，并补齐已有 contribution 但缺 outbox 的异常中断窗口。"""
        repaired = await self._reconcile_app_resource_intents(
            db,
            owner_hasn_id=owner_hasn_id,
            limit=limit,
        )
        outbox_key = (
            HasnArtifactContributions.agent_hasn_id
            + ':'
            + HasnArtifactContributions.idempotency_key
        )
        contributions = (
            await db.execute(
                select(HasnArtifactContributions)
                .outerjoin(
                    HasnArtifactRegistrationOutbox,
                    and_(
                        HasnArtifactRegistrationOutbox.owner_hasn_id
                        == HasnArtifactContributions.owner_hasn_id,
                        HasnArtifactRegistrationOutbox.idempotency_key == outbox_key,
                    ),
                )
                .where(HasnArtifactContributions.owner_hasn_id == owner_hasn_id)
                .where(HasnArtifactRegistrationOutbox.id.is_(None))
                .order_by(HasnArtifactContributions.id)
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
        inserted = repaired
        for contribution in contributions:
            key = self._idempotency_key(contribution)
            statement = (
                insert(HasnArtifactRegistrationOutbox)
                .values(
                    outbox_id=f'aor_{contribution.contribution_id.removeprefix("con_")}',
                    owner_hasn_id=owner_hasn_id,
                    artifact_id=contribution.artifact_id,
                    idempotency_key=key,
                    payload=self._payload(contribution),
                    status='completed',
                )
                .on_conflict_do_nothing(index_elements=['owner_hasn_id', 'idempotency_key'])
            )
            result = await db.execute(statement)
            inserted += getattr(result, 'rowcount', 0) or 0
        return inserted


artifact_registration_outbox_service = ArtifactRegistrationOutboxService()
