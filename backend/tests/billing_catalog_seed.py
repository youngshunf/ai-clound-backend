"""商品目录档位种子（doc94 D1 测试夹具）。

doc94 D1 之后，档位配置的唯一事实源是 `billing_offering` / `billing_plan`，
`subscription_tier` / `credit_package` 随之删除。于是用例的档位种子也必须落在目录里。

麻烦在于**目录不按 app_code 分区**：plan_key 就是档名（`free`/`pro`/…），本机开发库里
很可能已经存在同名真实行。所以这里采用「快照—覆写—还原」：

1. 先把要动的 plan 行原样读出来；
2. 覆写成用例需要的值（不存在则插入）；
3. 用例结束把原行**逐字段还原**，原本不存在的行删掉。

这样多个用例、以及用例与本机真实数据之间都不会互相污染——比起「插一行然后全表删」，
它不会顺手抹掉别人的数据。
"""

from __future__ import annotations

import json

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from backend.app.billing.service.offering_pricing import OFFERING_CREDITS_TOPUP, OFFERING_LLM_TIER

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: 30 天固定周期（doc94 §0.3）。目录里的 plan 快照必须带它，否则合同建不出来。
CYCLE_SECONDS = 30 * 24 * 60 * 60
DEFAULT_STORAGE_BYTES = 100 * 1024**3

_PLAN_COLUMNS = (
    'price_amount',
    'price_unit',
    'cycle',
    'quota_json',
    'trial_json',
    'grace_json',
    'display_json',
    'status',
    'sort_order',
)


async def _snapshot(db: AsyncSession, offering_key: str, plan_key: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(f"""
                SELECT {', '.join(f'"{c}"' for c in _PLAN_COLUMNS)}
                  FROM hasn_billing.billing_plan
                 WHERE offering_key = :o AND plan_key = :p
            """),
            {'o': offering_key, 'p': plan_key},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _upsert(db: AsyncSession, offering_key: str, plan_key: str, values: dict[str, Any]) -> None:
    payload = {'o': offering_key, 'p': plan_key, **values}
    await db.execute(
        text("""
            INSERT INTO hasn_billing.billing_plan
                (offering_key, plan_key, price_amount, price_unit, cycle,
                 quota_json, trial_json, grace_json, display_json, status, sort_order, created_time)
            VALUES (:o, :p, :price_amount, :price_unit, :cycle,
                    CAST(:quota_json AS jsonb), CAST(:trial_json AS jsonb), CAST(:grace_json AS jsonb),
                    CAST(:display_json AS jsonb), :status, :sort_order, NOW())
            ON CONFLICT (offering_key, plan_key) DO UPDATE SET
                price_amount = EXCLUDED.price_amount,
                price_unit   = EXCLUDED.price_unit,
                cycle        = EXCLUDED.cycle,
                quota_json   = EXCLUDED.quota_json,
                trial_json   = EXCLUDED.trial_json,
                grace_json   = EXCLUDED.grace_json,
                display_json = EXCLUDED.display_json,
                status       = EXCLUDED.status,
                sort_order   = EXCLUDED.sort_order
        """),
        payload,
    )


async def _delete(db: AsyncSession, offering_key: str, plan_key: str) -> None:
    await db.execute(
        text('DELETE FROM hasn_billing.billing_plan WHERE offering_key = :o AND plan_key = :p'),
        {'o': offering_key, 'p': plan_key},
    )


class CatalogSeed:
    """按「快照—覆写—还原」管理一批 plan 行。"""

    def __init__(self) -> None:
        self._restore: list[tuple[str, str, dict[str, Any] | None]] = []

    async def ensure_offering(self, db: AsyncSession, key: str, kind: str, display_name: str) -> None:
        """确保 offering 行存在（存在即不动，避免覆盖真实配置）。"""
        await db.execute(
            text("""
                INSERT INTO hasn_billing.billing_offering
                    (key, kind, feature_key, display_name, status, source, sort_order, created_time)
                VALUES (:key, :kind, :key, :display_name, 'active', 'platform', 0, NOW())
                ON CONFLICT (key) DO NOTHING
            """),
            {'key': key, 'kind': kind, 'display_name': display_name},
        )

    async def seed_tier(
        self,
        db: AsyncSession,
        *,
        tier_name: str,
        credits_per_cycle: Decimal | int,
        monthly_price: Decimal | int = 100,
        yearly_price: Decimal | int | None = 1000,
        max_agents: int = 5,
        storage_bytes: int = DEFAULT_STORAGE_BYTES,
        sort_order: int = 0,
        display_name: str | None = None,
    ) -> None:
        """种一个订阅档（月付 plan 为主档，有年价时同时种年付 plan）。"""
        await self.ensure_offering(db, OFFERING_LLM_TIER, 'llm_tier', 'LLM 订阅')
        quota = json.dumps(
            {
                'tier': tier_name,
                'credits_per_cycle': str(credits_per_cycle),
                'cycle_seconds': CYCLE_SECONDS,
                'cycle_count': 1,
                'max_agents': max_agents,
                'storage_bytes': storage_bytes,
            }
        )
        display = json.dumps({'display_name': display_name or tier_name, 'tier_name': tier_name, 'features': {}})
        base = {
            'price_unit': 'cny',
            'quota_json': quota,
            'trial_json': '{}',
            'grace_json': '{}',
            'display_json': display,
            'status': 'active',
            'sort_order': sort_order,
        }

        await self._apply(db, OFFERING_LLM_TIER, tier_name, {**base, 'price_amount': monthly_price, 'cycle': 'month'})
        if yearly_price is not None:
            await self._apply(
                db,
                OFFERING_LLM_TIER,
                f'{tier_name}_yearly',
                {**base, 'price_amount': yearly_price, 'cycle': 'year'},
            )

    async def seed_credit_pack(
        self,
        db: AsyncSession,
        *,
        package_name: str,
        credits: Decimal | int,
        bonus_credits: Decimal | int = 0,
        price: Decimal | int = 100,
        sort_order: int = 0,
        description: str | None = None,
    ) -> None:
        """种一个积分包 plan。"""
        await self.ensure_offering(db, OFFERING_CREDITS_TOPUP, 'credit_pack', '积分充值')
        await self._apply(
            db,
            OFFERING_CREDITS_TOPUP,
            package_name,
            {
                'price_amount': price,
                'price_unit': 'cny',
                'cycle': 'once',
                'quota_json': json.dumps({'credits': str(credits), 'bonus_credits': str(bonus_credits)}),
                'trial_json': '{}',
                'grace_json': '{}',
                'display_json': json.dumps({'package_name': package_name, 'description': description}),
                'status': 'active',
                'sort_order': sort_order,
            },
        )

    async def _apply(self, db: AsyncSession, offering_key: str, plan_key: str, values: dict[str, Any]) -> None:
        before = await _snapshot(db, offering_key, plan_key)
        self._restore.append((offering_key, plan_key, before))
        await _upsert(db, offering_key, plan_key, values)

    async def restore(self, db: AsyncSession) -> None:
        """把动过的行逐字段还原；原本不存在的删除。倒序还原，覆盖同一行多次也正确。"""
        for offering_key, plan_key, before in reversed(self._restore):
            if before is None:
                await _delete(db, offering_key, plan_key)
                continue
            payload = dict(before)
            for json_column in ('quota_json', 'trial_json', 'grace_json', 'display_json'):
                payload[json_column] = json.dumps(payload[json_column] or {})
            await _upsert(db, offering_key, plan_key, payload)
        self._restore.clear()
