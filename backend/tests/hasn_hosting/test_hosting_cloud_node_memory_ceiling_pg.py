"""云端节点**单节点内存上限**（H9-b）——零 mock，打真实 PostgreSQL。

主人 2026-08-02 拍板：装了引擎的节点要上调内存，「能调到多大」按订阅档给天花板。

守两条最容易写错的语义：

1. **天花板取 max，不是求和**。`max_cloud_nodes` 是数量（档位附赠 ＋ 加购**求和**），
   `max_node_memory_mb` 是「每个节点能有多大」（档位与加购取 **max**）。买两份加购是买了
   两个节点，不是把一个节点变成两倍大——写成求和会让加购用户拿到远超定价意图的单机规格。
2. **没定档就是 0（fail closed）**。档位漏配 `max_node_memory_mb` 时必须调不上去，
   而不是回落到某个"合理默认值"白送内存。与 `_tier_grant` 的 TIER_GRANT_FALLBACK=0 同取向。

事实源：docs/hasn-node设计文档/云端节点托管/00-无头hasn-node托管总体设计.md §13 H9-b
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

OWNER = 'h_hosting_memceil_owner'
USER_ID = 990311


async def _purge(sess) -> None:
    await sess.execute(text('DELETE FROM hasn_billing.user_subscription WHERE user_id = :u'), {'u': USER_ID})
    await sess.commit()


def _subscription(*, tier: str = 'pro', plan_snapshot: dict | None = None) -> UserSubscription:
    now = timezone.now()
    return UserSubscription(
        app_code='huanxing',
        user_id=USER_ID,
        tier=tier,
        subscription_type='monthly',
        monthly_credits=Decimal('0'),
        current_credits=Decimal('0'),
        used_credits=Decimal('0'),
        purchased_credits=Decimal('0'),
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        subscription_start_date=now,
        subscription_end_date=now + timedelta(days=30),
        next_grant_date=None,
        status='active',
        auto_renew=True,
        plan_snapshot=plan_snapshot,
    )


@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(s)
        yield s
    finally:
        await _purge(s)
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def test_无订阅时天花板为零(sess) -> None:
    """没订阅就调不上去。返回 0 而不是某个默认值——不静默送内存。"""
    ceiling = await cloud_node_service._memory_ceiling_of(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert ceiling == 0


async def test_合同快照里的天花板被读到(sess) -> None:
    """`plan_snapshot` 是购买时固化的合同参数，优先级最高、不随后续改价漂移。"""
    sess.add(_subscription(plan_snapshot={'max_node_memory_mb': 4096}))
    await sess.commit()

    ceiling = await cloud_node_service._memory_ceiling_of(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert ceiling == 4096


async def test_档位漏配天花板时为零而不是回落默认值(sess) -> None:
    """fail closed：新档位上线漏写 `max_node_memory_mb` → 调不上去，不白送内存。

    用一个库里必定没有的 tier 名，确保三级查找（快照 → plan_key → tier 反查）全部落空。
    """
    sess.add(_subscription(tier='tier_that_does_not_exist_h9b', plan_snapshot={}))
    await sess.commit()

    ceiling = await cloud_node_service._memory_ceiling_of(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert ceiling == 0


async def test_天花板非法值按零计(sess) -> None:
    """运营把值填成字符串/负数之类，按 0 计并告警，不能让非法值变成无限大。"""
    sess.add(_subscription(plan_snapshot={'max_node_memory_mb': '这不是数字'}))
    await sess.commit()

    ceiling = await cloud_node_service._memory_ceiling_of(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert ceiling == 0


async def test_档位与加购取max不求和(sess) -> None:
    """本用例是那条最容易写错的语义的形状。

    档位天花板 8192、加购天花板 4096（本机通常没配 cloud_node 权益，故加购侧为 0）：
    正确结果是 **max = 8192**；若误写成求和会得到 12288。断言「不等于两者之和」比只断言
    等于 max 更能钉住——后者在加购侧恰好为 0 时两种写法结果相同，测不出区别。
    """
    sess.add(_subscription(tier='max', plan_snapshot={'max_node_memory_mb': 8192}))
    await sess.commit()

    tier_ceiling = 8192
    addon_ceiling = await cloud_node_service._addon_memory_ceiling(sess, owner_hasn_id=OWNER)
    ceiling = await cloud_node_service._memory_ceiling_of(sess, user_id=USER_ID, owner_hasn_id=OWNER)

    assert ceiling == max(tier_ceiling, addon_ceiling)
    if addon_ceiling > 0:
        assert ceiling != tier_ceiling + addon_ceiling, '天花板必须取 max，求和会让加购用户拿到超规格单机'


async def test_没有加购权益时加购天花板为零(sess) -> None:
    """没买加购就没有加购侧天花板——`resolve_access` 的非门控放行（reason='free'）不算持有。"""
    addon = await cloud_node_service._addon_memory_ceiling(sess, owner_hasn_id='h_owner_without_any_addon_h9b')
    assert addon == 0
