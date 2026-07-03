"""P1 企业维度准入 + 双维度合并 真实 PostgreSQL 测试（零 mock）。

覆盖（实施清单 §P1 + 设计 §6.3/§6.6）：
- P1-1 破 subject_id 硬编码：enterprise 维度查企业权益而非 owner；owner 维度不传时行为不变
- P1-2 企业席位判定（S1 仅 purchase 分支）：买了+有席→entitled；买了+无席→need_seat_assignment；没买→need_purchase
- S1 免费/订阅企业应用不过席位
- merge_access（M1 顺序即优先级 + 自解优先；S3 纯企业应用个人空间 need_enterprise_space）
- P1-4 purchasable_by 下单校验（check_purchasable_by）

事实源: docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_app_seat import HasnAppSeat
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _catalog(app_id: str, **over) -> HasnAppCatalog:
    base = {
        'app_id': app_id,
        'name': '席位准入测试',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'P1',
        'source': 'first_party',
        'status': 'published',
        'execution_mode': 'cloud',
        'scope': ['enterprise'],
        'collaboration_mode': 'none',
        'entry_route': '/x',
        'sort_order': 100,
        'default_mount': False,
        'requires_role': None,
        'access_type': 'purchase',
        'min_tier': None,
        'price_amount': Decimal('99.00'),
        'price_unit': 'cny',
        'billing_cycle': 'month',
        'trial_days': 0,
        'sku_ref': None,
        'manifest_present': True,
    }
    base.update(over)
    return HasnAppCatalog(**base)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_owner(db: AsyncSession) -> str:
    user_id = 930_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=owner_hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'P1 owner {_uid()}'))
    await db.flush()
    return owner_hasn_id


async def _seed_enterprise_entitlement(
    db: AsyncSession, *, app_id: str, enterprise_id: int, seats_total: int | None
) -> None:
    db.add(
        HasnAppEntitlement(
            app_id=app_id,
            subject_type='enterprise',
            subject_id=str(enterprise_id),
            source='purchase',
            status='active',
            seats_total=seats_total,
            expires_at=timezone.now() + timedelta(days=30),
        )
    )
    await db.flush()


# ============================ P1-1 破 subject_id 硬编码 ============================


async def test_enterprise_subject_reads_enterprise_entitlement_not_owner(db: AsyncSession) -> None:
    """传 subject_type='enterprise'+subject_id 查企业权益（企业买了+有席→entitled），
    证明 subject_id 不再硬编码成 owner_hasn_id。"""
    app_id = f'ent_{_uid()}'
    eid = 700_000 + int(_uid(), 16) % 100_000
    cat = _catalog(app_id)
    db.add(cat)
    owner = await _seed_owner(db)
    await _seed_enterprise_entitlement(db, app_id=app_id, enterprise_id=eid, seats_total=3)
    db.add(
        HasnAppSeat(
            entitlement_id=0,
            enterprise_id=eid,
            app_id=app_id,
            member_hasn_id=owner,
            assigned_by='admin',
            status='assigned',
        )
    )
    await db.flush()
    access = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=owner,
        subject_type='enterprise',
        subject_id=str(eid),
        member_hasn_id=owner,
    )
    assert access['allowed'] is True
    assert access['reason'] == 'entitled'


async def test_owner_dimension_unchanged_when_subject_id_omitted(db: AsyncSession) -> None:
    """不传 subject_id 时按 owner 维度：purchase 无 owner 权益 → need_purchase（回归，行为不变）。"""
    app_id = f'own_{_uid()}'
    cat = _catalog(app_id, scope=['personal', 'enterprise'])
    db.add(cat)
    owner = await _seed_owner(db)
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_purchase'


# ============================ P1-2 企业席位判定（S1 仅 purchase） ============================


async def test_enterprise_bought_no_seat_needs_assignment(db: AsyncSession) -> None:
    app_id = f'ent_{_uid()}'
    eid = 700_000 + int(_uid(), 16) % 100_000
    cat = _catalog(app_id)
    db.add(cat)
    owner = await _seed_owner(db)
    await _seed_enterprise_entitlement(db, app_id=app_id, enterprise_id=eid, seats_total=3)
    access = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=owner,
        subject_type='enterprise',
        subject_id=str(eid),
        member_hasn_id=owner,
    )
    assert access['allowed'] is False
    assert access['reason'] == 'need_seat_assignment'
    assert access['requires'] == 'seat'


async def test_enterprise_not_bought_needs_purchase(db: AsyncSession) -> None:
    app_id = f'ent_{_uid()}'
    eid = 700_000 + int(_uid(), 16) % 100_000
    cat = _catalog(app_id)
    db.add(cat)
    owner = await _seed_owner(db)
    # 企业没买（无 entitlement）
    access = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=owner,
        subject_type='enterprise',
        subject_id=str(eid),
        member_hasn_id=owner,
    )
    assert access['allowed'] is False
    assert access['reason'] == 'need_purchase'


async def test_s1_free_enterprise_app_no_seat_gate(db: AsyncSession) -> None:
    """S1：免费企业应用不过席位——直接 free 放行（不误锁成 need_seat_assignment）。"""
    app_id = f'free_{_uid()}'
    eid = 700_000 + int(_uid(), 16) % 100_000
    cat = _catalog(app_id, access_type='free', price_amount=None)
    db.add(cat)
    owner = await _seed_owner(db)
    access = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=owner,
        subject_type='enterprise',
        subject_id=str(eid),
        member_hasn_id=owner,
    )
    assert access['allowed'] is True
    assert access['reason'] == 'free'


async def test_s1_enterprise_entitlement_without_seats_is_entitled(db: AsyncSession) -> None:
    """企业 purchase 权益但 seats_total=None（如 admin 整企业授予、非席位制）→ 不过席位，entitled。"""
    app_id = f'ent_{_uid()}'
    eid = 700_000 + int(_uid(), 16) % 100_000
    cat = _catalog(app_id)
    db.add(cat)
    owner = await _seed_owner(db)
    await _seed_enterprise_entitlement(db, app_id=app_id, enterprise_id=eid, seats_total=None)
    access = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=owner,
        subject_type='enterprise',
        subject_id=str(eid),
        member_hasn_id=owner,
    )
    assert access['allowed'] is True
    assert access['reason'] == 'entitled'


# ============================ merge_access（M1 + S3） 纯函数 ============================


def _acc(*, allowed: bool, reason: str, requires: str | None = None) -> dict:
    return {'allowed': allowed, 'reason': reason, 'requires': requires}


def test_merge_enterprise_allowed_wins() -> None:
    merged = app_catalog_service.merge_access(
        _acc(allowed=False, reason='need_purchase', requires='purchase'), _acc(allowed=True, reason='entitled')
    )
    assert merged['allowed'] is True
    assert merged['reason'] == 'entitled'


def test_merge_owner_allowed_when_enterprise_denies() -> None:
    merged = app_catalog_service.merge_access(
        _acc(allowed=True, reason='entitled'), _acc(allowed=False, reason='need_seat_assignment', requires='seat')
    )
    assert merged['allowed'] is True
    assert merged['reason'] == 'entitled'


def test_merge_self_serve_priority_need_purchase_over_seat() -> None:
    """M1：both 应用「个人可自购（need_purchase）+ 企业没席（need_seat_assignment）」→ 展示 need_purchase。"""
    merged = app_catalog_service.merge_access(
        _acc(allowed=False, reason='need_purchase', requires='purchase'),
        _acc(allowed=False, reason='need_seat_assignment', requires='seat'),
    )
    assert merged['reason'] == 'need_purchase'


def test_merge_seat_when_owner_not_self_serve() -> None:
    """owner 不可自解（disabled）+ 企业没席 → 展示 need_seat_assignment（找管理员）。"""
    merged = app_catalog_service.merge_access(
        _acc(allowed=False, reason='disabled'), _acc(allowed=False, reason='need_seat_assignment', requires='seat')
    )
    assert merged['reason'] == 'need_seat_assignment'


def test_merge_none_enterprise_returns_owner() -> None:
    """S3：纯企业应用个人空间——enterprise_access=None，owner 已被覆写 need_enterprise_space → 稳定展示之。"""
    owner = _acc(allowed=False, reason='need_enterprise_space', requires='enterprise_space')
    merged = app_catalog_service.merge_access(owner, None)
    assert merged['reason'] == 'need_enterprise_space'


# ============================ P1-4 purchasable_by 校验 ============================


def test_check_purchasable_by_owner_buying_enterprise_only_rejected() -> None:
    cat = _catalog(f'ent_{_uid()}', purchasable_by='enterprise')
    with pytest.raises(errors.RequestError):
        app_catalog_service.check_purchasable_by(cat, buyer='owner')


def test_check_purchasable_by_enterprise_buying_owner_only_rejected() -> None:
    cat = _catalog(f'own_{_uid()}', purchasable_by='owner')
    with pytest.raises(errors.RequestError):
        app_catalog_service.check_purchasable_by(cat, buyer='enterprise')


def test_check_purchasable_by_both_allows_either() -> None:
    cat = _catalog(f'both_{_uid()}', purchasable_by='both')
    # 不抛即通过
    app_catalog_service.check_purchasable_by(cat, buyer='owner')
    app_catalog_service.check_purchasable_by(cat, buyer='enterprise')
