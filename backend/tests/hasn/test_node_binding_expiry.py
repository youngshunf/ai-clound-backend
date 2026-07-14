"""C3 安全修复真实 PG 集成测试：Owner Binding 租约过期后 MUST NOT 参与路由 / 鉴权。

事实源：Core/05 §5.2 租约失效。旧实现 get_active_binding 只查 status=='active'，
过期（expires_at <= now）仍 active 的租约会被当有效返回 → 永久可路由 / 鉴权。本修复加
expires_at 过滤（expires_at 为空视为永久）+ expire_stale_bindings sweeper。

零 mock：真实本地 PostgreSQL(15432)，async_db_session 退出即回滚，不污染权威表。
需要：export DATABASE_PORT=15432。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app.hasn.model import HasnNodeBindings
from backend.app.hasn.service.hasn_node_bindings_service import (
    hasn_node_bindings_service as svc,
)
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio(loop_scope='module')

_NODE = 'n_c3_test_node'
_OWNER = 'h_c3_test_owner'


def _binding(*, suffix: str, status: str, expires_delta_days: float) -> HasnNodeBindings:
    # expires_at 列 NOT NULL（Core/05 §5.2 租约必有到期）；负数 = 已过期，正数 = 未过期。
    now = timezone.now()
    return HasnNodeBindings(
        binding_id=f'ob_c3_{suffix}',
        node_id=_NODE,
        owner_id=f'{_OWNER}_{suffix}',
        auth_profile='bearer_token',
        scopes={'bind_owner': True},
        status=status,
        bound_at=now,
        expires_at=now + timedelta(days=expires_delta_days),
    )


async def test_expired_active_binding_not_returned() -> None:
    async with async_db_session() as db:
        # 活跃但昨天已过期 → 不应被 get_active_binding 当有效返回
        db.add(_binding(suffix='expired', status='active', expires_delta_days=-1))
        await db.flush()
        got = await svc.get_active_binding(db=db, node_id=_NODE, owner_id=f'{_OWNER}_expired')
        assert got is None


async def test_unexpired_active_binding_returned() -> None:
    async with async_db_session() as db:
        db.add(_binding(suffix='live', status='active', expires_delta_days=7))
        await db.flush()
        got = await svc.get_active_binding(db=db, node_id=_NODE, owner_id=f'{_OWNER}_live')
        assert got is not None
        assert got.owner_id == f'{_OWNER}_live'


async def test_list_active_bindings_excludes_expired() -> None:
    async with async_db_session() as db:
        db.add(_binding(suffix='l_live', status='active', expires_delta_days=3))
        db.add(_binding(suffix='l_exp', status='active', expires_delta_days=-2))
        await db.flush()
        rows = await svc.list_active_bindings(db=db, node_id=_NODE)
        owners = {r.owner_id for r in rows}
        assert f'{_OWNER}_l_live' in owners
        assert f'{_OWNER}_l_exp' not in owners


async def test_expire_stale_bindings_sweeps_to_expired() -> None:
    async with async_db_session() as db:
        stale = _binding(suffix='sweep', status='active', expires_delta_days=-5)
        db.add(stale)
        await db.flush()
        swept = await svc.expire_stale_bindings(db=db)
        assert swept >= 1
        await db.refresh(stale)
        assert stale.status == 'expired'


async def test_add_owner_binding_slides_existing_lease() -> None:
    """滑动续期：握手命中 existing 必须刷新 expires_at（2026-07-14 福仔裁决1配套）。

    旧实现命中直接短路返回不续期 → 设备持续在线也会撞 7 天悬崖后新建行、旧行堆积 active。
    """
    async with async_db_session() as db:
        # 快到期（还剩 1 天）的有效租约
        existing = _binding(suffix='slide', status='active', expires_delta_days=1)
        db.add(existing)
        await db.flush()
        got = await svc.add_owner_binding(
            db=db,
            node_id=_NODE,
            owner_id=f'{_OWNER}_slide',
            auth_profile='bearer_token',
        )
        # 复用同一行（不新建），且 expires_at 已滑动到 ~+7 天
        assert got.binding_id == existing.binding_id
        assert got.expires_at > timezone.now() + timedelta(days=6)
