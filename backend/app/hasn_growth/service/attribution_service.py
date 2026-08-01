"""获客追加式归因与幂等用量事实写入。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.growth_attribution_event import GrowthAttributionEvent
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.utils.timezone import timezone


def _playbook_trace(message: OutreachMessage) -> dict[str, int | None]:
    """数据库要求打法追踪三元组同时存在或同时为空。"""
    project_playbook_id = message.growth_project_playbook_id
    playbook_id = message.playbook_id
    playbook_version = message.playbook_version
    trace = (project_playbook_id, playbook_id, playbook_version)
    if any(value is None for value in trace):
        return {
            'growth_project_playbook_id': None,
            'playbook_id': None,
            'playbook_version': None,
        }
    assert project_playbook_id is not None
    assert playbook_id is not None
    assert playbook_version is not None
    return {
        'growth_project_playbook_id': project_playbook_id,
        'playbook_id': playbook_id,
        'playbook_version': playbook_version,
    }


async def _insert_event(db: AsyncSession, values: dict[str, Any]) -> None:
    await db.execute(
        pg_insert(GrowthAttributionEvent)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[
                GrowthAttributionEvent.growth_project_id,
                GrowthAttributionEvent.idempotency_key,
            ]
        )
    )


class GrowthAttributionService:
    """所有外部事件重放都由项目级幂等键收敛为一条归因事实。"""

    @staticmethod
    async def record_outreach(
        db: AsyncSession,
        *,
        message: OutreachMessage,
        event_type: str,
        event_key: str,
        channel: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if message.growth_project_id is None:
            return
        await _insert_event(
            db,
            {
                'growth_project_id': message.growth_project_id,
                'event_type': event_type,
                'customer_id': message.customer_id,
                **_playbook_trace(message),
                'source_kind': channel,
                'source_ref': f'outreach:{message.id}',
                'occurred_time': timezone.now(),
                'idempotency_key': f'outreach:{message.id}:{event_key}'[:200],
                'meta_data': {
                    'outreach_message_id': message.id,
                    'channel': channel,
                    **(metadata or {}),
                },
            },
        )

    @staticmethod
    async def record_cost(
        db: AsyncSession,
        *,
        message: OutreachMessage,
        event_key: str,
        channel: str,
        amount: Decimal | None,
        currency: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if message.growth_project_id is None:
            return
        await _insert_event(
            db,
            {
                'growth_project_id': message.growth_project_id,
                'event_type': 'cost',
                'customer_id': message.customer_id,
                **_playbook_trace(message),
                'source_kind': channel,
                'source_ref': f'outreach:{message.id}',
                'amount': amount,
                'currency': currency,
                'occurred_time': timezone.now(),
                'idempotency_key': f'cost:outreach:{message.id}:{event_key}'[:200],
                'meta_data': {
                    'outreach_message_id': message.id,
                    'usage_kind': 'outreach',
                    'channel': channel,
                    'cost_state': 'unknown' if amount is None else 'known',
                    **(metadata or {}),
                },
            },
        )


growth_attribution_service = GrowthAttributionService()
