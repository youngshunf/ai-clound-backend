"""APPBETA 灰度内测门控 真实 PostgreSQL 测试（零 mock）。

覆盖（应用中心「内测状态 + 自定义角标」需求）：
- ``resolve_app_access`` 灰度门控：beta_gray 未授权 → need_beta / 申请中 → beta_pending / 已通过 → 落商业化准入
- ``apply_beta``：published+beta_gray 才可申请；幂等；被拒后可重申；非灰度应用拒绝申请
- ``invite_beta``：管理员邀请直接 approved（source=invite）
- ``decide_beta``：通过 / 拒绝，写 decided_by/decided_at
- ``catalog_to_manifest``：release_phase + 自定义 badge（文字+颜色）映射，无文字时 badge=None
- beta_full（全量内测）/ ga 不门控

门控落在商业化准入之前（设计 §5.2 之上叠加）：灰度通过后仍叠加 free/tier/purchase。
"""
from __future__ import annotations

import uuid

from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _catalog(app_id: str, **over) -> HasnAppCatalog:
    base: dict[str, Any] = {
        'app_id': app_id,
        'name': '内测测试',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'APPBETA',
        'source': 'first_party',
        'status': 'published',
        'execution_mode': 'cloud',
        'scope': ['personal'],
        'collaboration_mode': 'none',
        'entry_route': '/x',
        'sort_order': 100,
        'default_mount': False,
        'requires_role': None,
        'access_type': 'free',
        'min_tier': None,
        'price_amount': None,
        'price_unit': 'cny',
        'billing_cycle': 'once',
        'trial_days': 0,
        'sku_ref': None,
        'manifest_present': True,
    }
    base.update(over)
    return HasnAppCatalog(**base)


@pytest_asyncio.fixture
async def db():
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


async def _seed_owner(db) -> str:
    """造一个 owner（HasnHumans），返回 owner_hasn_id。"""
    user_id = 920_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn_id = f'h_{_uid()}{_uid()}'[:38]
    # nickname 有唯一约束，给唯一值避免同测试造多 owner 撞键。
    db.add(HasnHumans(hasn_id=owner_hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'内测owner-{_uid()}'))
    await db.flush()
    return owner_hasn_id


# ============================ 灰度门控（resolve_app_access） ============================


async def test_beta_gray_unauthorized_returns_need_beta(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_beta'
    assert access['requires'] == 'beta'


async def test_apply_then_resolve_beta_pending(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)

    row = await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner, note='想试试')
    assert row.status == 'pending'
    assert row.source == 'apply'
    assert row.note == '想试试'

    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'beta_pending'
    assert access['requires'] == 'beta'


async def test_approve_then_resolve_allowed_free(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray', access_type='free')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)

    row = await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner)
    decided = await app_catalog_service.decide_beta(db, pk=row.id, approve=True, decided_by='admin-1')
    assert decided.status == 'approved'
    assert decided.decided_by == 'admin-1'
    assert decided.decided_at is not None

    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    # 灰度通过 → 落到下方免费准入 → allowed/free。
    assert access['allowed'] is True
    assert access['reason'] == 'free'


async def test_invite_directly_approved_and_allowed(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)

    row = await app_catalog_service.invite_beta(
        db, app_id=cat.app_id, subject_id=owner, decided_by='admin-7', note='定向邀请'
    )
    assert row.status == 'approved'
    assert row.source == 'invite'
    assert row.decided_by == 'admin-7'

    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True


async def test_beta_gray_approved_still_gated_by_tier(db) -> None:
    """灰度通过 ≠ 绕过付费墙：beta_gray + tier(pro)，approved 后仍因档位不足 need_upgrade。"""
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray', access_type='tier', min_tier='pro')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)  # 无订阅 → free 档

    await app_catalog_service.invite_beta(db, app_id=cat.app_id, subject_id=owner, decided_by='admin-1')
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    # beta 闸已过，落到商业化闸 → need_upgrade（而非 need_beta），证两闸叠加且顺序正确。
    assert access['reason'] == 'need_upgrade'
    assert access['requires'] == 'upgrade'


async def test_rejected_then_reapply_resets_pending(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)

    row = await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner)
    rejected = await app_catalog_service.decide_beta(db, pk=row.id, approve=False, decided_by='admin-1', note='暂不开放')
    assert rejected.status == 'rejected'

    # 被拒后未授权 → need_beta（非 beta_pending）。
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['reason'] == 'need_beta'

    # 再申请 → 重置回 pending（同一行，唯一约束）。
    reapplied = await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner, note='再试一次')
    assert reapplied.id == row.id
    assert reapplied.status == 'pending'
    assert reapplied.decided_by is None


async def test_apply_idempotent_while_pending(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)

    r1 = await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner)
    r2 = await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner)
    assert r1.id == r2.id
    assert r2.status == 'pending'


async def test_apply_on_non_beta_gray_rejected(db) -> None:
    cat = _catalog(f'ga_{_uid()}', release_phase='ga')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)
    with pytest.raises(errors.RequestError):
        await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner)


async def test_apply_on_unpublished_forbidden(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray', status='draft')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)
    with pytest.raises(errors.ForbiddenError):
        await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=owner)


async def test_beta_full_not_gated(db) -> None:
    """全量内测：标识为内测但人人可见可用，不走灰度门控。"""
    cat = _catalog(f'bf_{_uid()}', release_phase='beta_full', access_type='free')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db)
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True
    assert access['reason'] == 'free'


# ============================ 角标 + 发布阶段（catalog_to_manifest） ============================


async def test_manifest_emits_badge_and_release_phase(db) -> None:
    cat = _catalog(
        f'badge_{_uid()}', release_phase='beta_full', badge_text='热门', badge_color='#10B981'
    )
    db.add(cat)
    await db.flush()
    manifest = app_catalog_service.catalog_to_manifest(cat)
    assert manifest['release_phase'] == 'beta_full'
    assert manifest['badge'] == {'text': '热门', 'color': '#10B981'}


async def test_manifest_no_badge_when_text_empty(db) -> None:
    cat = _catalog(f'nobadge_{_uid()}')  # 默认 release_phase=ga，无 badge_text
    db.add(cat)
    await db.flush()
    manifest = app_catalog_service.catalog_to_manifest(cat)
    assert manifest['release_phase'] == 'ga'
    assert manifest['badge'] is None


# ============================ 管理端列举（list_beta_access） ============================


async def test_list_beta_access_filter_by_app_and_status(db) -> None:
    cat = _catalog(f'beta_{_uid()}', release_phase='beta_gray')
    db.add(cat)
    await db.flush()
    o1 = await _seed_owner(db)
    o2 = await _seed_owner(db)
    await app_catalog_service.apply_beta(db, catalog=cat, owner_hasn_id=o1)  # pending
    invited = await app_catalog_service.invite_beta(db, app_id=cat.app_id, subject_id=o2, decided_by='admin')  # approved

    rows_all = await app_catalog_service.list_beta_access(db, app_id=cat.app_id)
    assert {r.subject_id for r in rows_all} == {o1, o2}

    pendings = await app_catalog_service.list_beta_access(db, app_id=cat.app_id, status='pending')
    assert [r.subject_id for r in pendings] == [o1]

    approved = await app_catalog_service.list_beta_access(db, app_id=cat.app_id, status='approved')
    assert [r.id for r in approved] == [invited.id]
