"""MK-4 应用付费直迁内核·契约锁死（实施/92 MK-4）——零 mock。

MK-4 把「应用付费判定改问内核」，但因 `resolve_app_access`（单维叶子）被工作台/kernel
以**单维**方式调用、各自 `merge_access`，而 `resolve_access` 恒返合并结果——直接把
`resolve_app_access` 倒置成 `resolve_access` 薄壳会**无限递归 + 破坏单维合并调用方**
（`resolve_access._resolve_app`→`resolve_merged_app_access`→`resolve_app_access`）。

故 MK-4 的「统一」以**契约锁死**落地，不做不安全的倒置：
1. `_norm_app_reason` 映射表锁死——AppAccess（resolve_app_access 产出）的每个 reason
   都被内核归一到 AccessDecision 十态 / 结构性透传态，口径永不漂移；
2. 应用/席位权益行带 feature_key='app:<id>'（付费墙通用语言），真 PG 验。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/实施/92 MK-4。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.schema.access import CANONICAL_REASONS
from backend.app.billing.service.access_service import _norm_app_reason
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.service import app_catalog_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 结构性闸 reason（非十态，但 AccessDecision.reason 开放字符串合法透传——席位/内测/企业空间）。
STRUCTURAL_REASONS = frozenset({'need_seat_assignment', 'need_enterprise_space', 'need_beta', 'beta_pending'})


def _appaccess(reason: str, *, allowed: bool, trial_available: bool = False) -> dict:
    """构造 resolve_app_access 产出形状的 7 键 AppAccess dict（_access helper 契约）。"""
    return {
        'allowed': allowed,
        'reason': reason,
        'requires': None,
        'min_tier': 'free',
        'price': None,
        'trial_available': trial_available,
        'entitlement_expires_at': None,
    }


# ── 1. _norm_app_reason 映射表锁死（resolve_app_access 全 reason 集）──
def test_norm_app_reason_mapping_table() -> None:
    """resolve_app_access 能产出的每个 reason → _norm_app_reason 归一目标锁死。"""
    cases = [
        # (AppAccess reason, allowed, trial_available) → 期望归一 reason
        (('free', True, False), 'free'),
        (('tier_ok', True, False), 'tier_ok'),
        (('entitled', True, False), 'entitled'),
        (('trialing', True, False), 'trialing'),
        (('need_upgrade', False, False), 'need_upgrade'),
        (('need_purchase', False, False), 'need_purchase'),
        (('disabled', False, False), 'disabled'),
        # 未准入但可开试用 → 统一收敛 trial_available（无论原 reason 是 need_upgrade/need_purchase）
        (('need_upgrade', False, True), 'trial_available'),
        (('need_purchase', False, True), 'trial_available'),
        # 结构性闸原样透传
        (('need_seat_assignment', False, False), 'need_seat_assignment'),
        (('need_enterprise_space', False, False), 'need_enterprise_space'),
        (('need_beta', False, False), 'need_beta'),
        (('beta_pending', False, False), 'beta_pending'),
        # not_in_catalog / None（放行）→ free
        (('not_in_catalog', True, False), 'free'),
    ]
    for (reason, allowed, trial), expected in cases:
        got = _norm_app_reason(_appaccess(reason, allowed=allowed, trial_available=trial))
        assert got == expected, f'{reason}(allowed={allowed},trial={trial}) → {got}, 期望 {expected}'
    # allowed=True 且 reason=None → free
    assert _norm_app_reason({'allowed': True, 'reason': None}) == 'free'
    # allowed=False 且 reason 缺失 → 兜底 need_purchase（绝不静默放行付费墙）
    assert _norm_app_reason({'allowed': False}) == 'need_purchase'


def test_norm_app_reason_outputs_are_canonical_or_structural() -> None:
    """归一输出恒落在「十态 ∪ 结构性闸」内——口径不漂移。"""
    all_app_reasons = [
        'free', 'tier_ok', 'entitled', 'trialing', 'need_upgrade', 'need_purchase',
        'disabled', 'not_in_catalog', 'need_seat_assignment', 'need_enterprise_space',
        'need_beta', 'beta_pending',
    ]
    allowed_set = {'free', 'tier_ok', 'entitled', 'trialing', 'not_in_catalog'}
    for r in all_app_reasons:
        out = _norm_app_reason(_appaccess(r, allowed=r in allowed_set))
        assert out in (CANONICAL_REASONS | STRUCTURAL_REASONS), f'{r} 归一到未知态 {out}'
    # trial_available 分支也在十态内
    assert _norm_app_reason(_appaccess('need_purchase', allowed=False, trial_available=True)) in CANONICAL_REASONS


# ── 2. 应用/席位权益行带 feature_key（真 PG）──
_MK4_OWNER = 'h_mk4_feature_key'
_MK4_APP = 'mk4_feature_key_app'


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

    async def _purge() -> None:
        await s.execute(text("DELETE FROM hasn_app_entitlement WHERE app_id = :a"), {'a': _MK4_APP})
        await s.commit()

    try:
        await _purge()
        yield s
    finally:
        await _purge()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def test_grant_entitlement_sets_feature_key(sess) -> None:
    """grant_entitlement 写行带 feature_key='app:<id>'（MK-4 席位/应用权益的付费墙通用语言）。"""
    ent = await app_catalog_service.grant_entitlement(
        sess, app_id=_MK4_APP, subject_type='owner', subject_id=_MK4_OWNER, source='purchase'
    )
    await sess.commit()
    assert ent.feature_key == f'app:{_MK4_APP}', f'feature_key 未落 app: 前缀: {ent.feature_key!r}'

    reloaded = (
        await sess.execute(
            select(HasnAppEntitlement).where(HasnAppEntitlement.app_id == _MK4_APP)
        )
    ).scalar_one()
    assert reloaded.feature_key == f'app:{_MK4_APP}'
