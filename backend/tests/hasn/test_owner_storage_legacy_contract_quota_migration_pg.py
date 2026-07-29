"""旧订阅合同存储权益修复迁移的真实 PostgreSQL 契约测试。"""

from __future__ import annotations

import json
import uuid

from pathlib import Path

import pytest

from sqlalchemy import text

from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio

_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = (
    _BACKEND
    / 'sql'
    / 'billing'
    / 'migrations'
    / '2026-07-30-owner-storage-legacy-contract-quota.sql'
)


async def test_migration_repairs_only_missing_known_tier_quota() -> None:
    """迁移补齐旧档位、保留其他快照键，且不覆盖已有或无法识别的合同值。"""
    base_user_id = 990_000_000 + int(uuid.uuid4().hex[:6], 16)
    rows = (
        (base_user_id, 'free', None),
        (base_user_id + 1, 'max', {'credits_per_cycle': '10'}),
        (base_user_id + 2, 'flagship', {'storage_bytes': 123}),
        (base_user_id + 3, 'legacy_custom', None),
    )

    async with async_db_session() as db:
        transaction = await db.begin()
        try:
            for user_id, tier, plan_snapshot in rows:
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_billing.user_subscription
                            (user_id, tier, monthly_credits, current_credits, used_credits,
                             purchased_credits, billing_cycle_start, billing_cycle_end,
                             status, app_code, plan_snapshot)
                        VALUES
                            (:user_id, :tier, 0, 0, 0, 0, now(), now(), 'expired',
                             'huanxing', CAST(:plan_snapshot AS jsonb))
                        """
                    ),
                    {
                        'user_id': user_id,
                        'tier': tier,
                        'plan_snapshot': (
                            json.dumps(plan_snapshot, ensure_ascii=False)
                            if plan_snapshot is not None
                            else None
                        ),
                    },
                )

            migration_sql = text(_MIGRATION.read_text(encoding='utf-8'))
            await db.execute(migration_sql)
            await db.execute(migration_sql)

            snapshots = (
                (
                    await db.execute(
                        text(
                            """
                            SELECT tier, plan_snapshot
                            FROM hasn_billing.user_subscription
                            WHERE user_id BETWEEN :first_user_id AND :last_user_id
                            ORDER BY user_id
                            """
                        ),
                        {
                            'first_user_id': base_user_id,
                            'last_user_id': base_user_id + 3,
                        },
                    )
                )
                .mappings()
                .all()
            )
            by_tier = {str(row['tier']): row['plan_snapshot'] for row in snapshots}

            assert by_tier['free']['storage_bytes'] == 10 * 1024**3
            assert by_tier['max'] == {
                'credits_per_cycle': '10',
                'storage_bytes': 100 * 1024**3,
            }
            assert by_tier['flagship']['storage_bytes'] == 123
            assert by_tier['legacy_custom'] is None
        finally:
            await transaction.rollback()
