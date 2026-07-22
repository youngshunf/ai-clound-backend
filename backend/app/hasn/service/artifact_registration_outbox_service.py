"""Agent 产物登记 outbox 的领取、重试和修复逻辑。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from backend.app.hasn.model import HasnArtifactContributions, HasnArtifactRegistrationOutbox
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
        """补齐已有 contribution 但缺 outbox intent 的异常中断窗口。"""
        contributions = (
            await db.execute(
                select(HasnArtifactContributions)
                .where(HasnArtifactContributions.owner_hasn_id == owner_hasn_id)
                .order_by(HasnArtifactContributions.id)
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
        inserted = 0
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
