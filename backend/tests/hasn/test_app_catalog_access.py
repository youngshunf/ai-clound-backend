"""C4 商业化准入 真实 PostgreSQL 测试（零 mock）。

覆盖（设计 §5.2 / §5.3，实施清单 §C4）：
- ``resolve_app_access`` 准入矩阵：free / tier(档位足/不足/过期回落) / purchase(有权益/无权益/试用) / disabled
- ``_has_used_trial`` 强制「试用只能开一次」
- 闸门②挂载前置准入：付费未准入 → ForbiddenError 携 access(reason/requires/price)
- 闸门③Runtime Gateway entitlement 维度：**两条到达面**（中继 skip_mode_gate=False / MCP 直连面 True）
  都拦付费未准入，写审计 error_code=15030（见 [[feedback_ai_native_gateway_two_call_faces]]）

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §5。
"""
from __future__ import annotations

import uuid

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.app.billing.model import UserSubscription
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _catalog(app_id: str, **over) -> HasnAppCatalog:
    base = {
        'app_id': app_id,
        'name': '准入测试',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'C4',
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


def _subscription(
    user_id: int, *, tier: str, status: str = 'active', end_offset_days: int | None = 30
) -> UserSubscription:
    now = timezone.now()
    return UserSubscription(
        app_code='huanxing',
        user_id=user_id,
        tier=tier,
        subscription_type='monthly',
        monthly_credits=Decimal(0),
        current_credits=Decimal(0),
        used_credits=Decimal(0),
        purchased_credits=Decimal(0),
        billing_cycle_start=now - timedelta(days=1),
        billing_cycle_end=now + timedelta(days=29),
        subscription_start_date=now - timedelta(days=1),
        subscription_end_date=(now + timedelta(days=end_offset_days)) if end_offset_days is not None else None,
        status=status,
        auto_renew=True,
        max_agents=1,
    )


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


async def _seed_owner(db, *, tier: str | None, status: str = 'active', end_offset_days: int | None = 30) -> str:
    """造一个 owner（HasnHumans）+ 可选订阅，返回 owner_hasn_id。"""
    user_id = 910_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn_id = f'h_{_uid()}{_uid()}'[:38]
    # star_id 有唯一约束，给唯一值避免撞 dev DB 既有空串行。
    db.add(HasnHumans(hasn_id=owner_hasn_id, star_id=f's{user_id}', user_id=user_id, nickname='C4 owner'))
    if tier is not None:
        db.add(_subscription(user_id, tier=tier, status=status, end_offset_days=end_offset_days))
    await db.flush()
    return owner_hasn_id


# ============================ resolve_app_access 矩阵 ============================


async def test_free_always_allowed(db) -> None:
    cat = _catalog(f'free_{_uid()}', access_type='free')
    db.add(cat)
    await db.flush()
    owner = await _seed_owner(db, tier=None)
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True
    assert access['reason'] == 'free'


async def test_tier_owner_free_needs_upgrade_with_trial_flag(db) -> None:
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='pro', trial_days=7)
    db.add(cat)
    owner = await _seed_owner(db, tier='free')  # 免费档 < pro
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_upgrade'
    assert access['requires'] == 'upgrade'
    assert access['min_tier'] == 'pro'
    assert access['trial_available'] is True  # trial_days>0 且未用过


async def test_tier_sufficient_allows(db) -> None:
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='pro')
    db.add(cat)
    owner = await _seed_owner(db, tier='pro')
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True
    assert access['reason'] == 'tier_ok'


async def test_tier_higher_owner_meets_lower_requirement(db) -> None:
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='pro')
    db.add(cat)
    owner = await _seed_owner(db, tier='flagship')  # flagship > pro
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True


async def test_tier_insufficient_when_below_required(db) -> None:
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='flagship')
    db.add(cat)
    owner = await _seed_owner(db, tier='pro')  # pro < flagship
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_upgrade'


async def test_tier_expired_subscription_falls_back_to_free(db) -> None:
    """存储 tier=pro 但 status=expired → 有效档位回落 free → 不放行（设计：过期不降级只重算）。"""
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='pro')
    db.add(cat)
    owner = await _seed_owner(db, tier='pro', status='expired')
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_upgrade'


async def test_tier_past_end_date_falls_back_to_free(db) -> None:
    """status 仍 active 但订阅结束日已过 → 按日期重算回落 free。"""
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='pro')
    db.add(cat)
    owner = await _seed_owner(db, tier='pro', status='active', end_offset_days=-1)
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False


async def test_purchase_no_entitlement_needs_purchase_with_price(db) -> None:
    cat = _catalog(
        f'buy_{_uid()}', access_type='purchase', price_amount=Decimal('39.90'),
        price_unit='cny', billing_cycle='month', trial_days=0,
    )
    db.add(cat)
    owner = await _seed_owner(db, tier='flagship')  # 即使顶级订阅，purchase 仍需买
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_purchase'
    assert access['requires'] == 'purchase'
    assert access['price'] == {'amount': 39.9, 'unit': 'cny', 'cycle': 'month'}
    assert access['trial_available'] is False  # trial_days=0


async def test_purchase_active_entitlement_allows(db) -> None:
    app_id = f'buy_{_uid()}'
    cat = _catalog(app_id, access_type='purchase', price_amount=Decimal('39.90'))
    db.add(cat)
    owner = await _seed_owner(db, tier='free')
    db.add(HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=owner, source='purchase',
        status='active', expires_at=timezone.now() + timedelta(days=30),
    ))
    await db.flush()
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True
    assert access['reason'] == 'entitled'
    assert access['entitlement_expires_at'] is not None


async def test_purchase_trial_entitlement_is_trialing(db) -> None:
    app_id = f'buy_{_uid()}'
    cat = _catalog(app_id, access_type='purchase', price_amount=Decimal('39.90'), trial_days=7)
    db.add(cat)
    owner = await _seed_owner(db, tier='free')
    db.add(HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=owner, source='trial',
        status='active', expires_at=timezone.now() + timedelta(days=7),
    ))
    await db.flush()
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True
    assert access['reason'] == 'trialing'
    # 已开过试用 → trial_available 关闭（但本来就 allowed，trial_available 不再相关）
    assert await app_catalog_service._has_used_trial(
        db, app_id=app_id, subject_type='owner', subject_id=owner
    ) is True


async def test_expired_entitlement_does_not_grant_access(db) -> None:
    app_id = f'buy_{_uid()}'
    cat = _catalog(app_id, access_type='purchase', price_amount=Decimal('9.90'), trial_days=7)
    db.add(cat)
    owner = await _seed_owner(db, tier='free')
    db.add(HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=owner, source='trial',
        status='active', expires_at=timezone.now() - timedelta(days=1),  # 已过期
    ))
    await db.flush()
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'need_purchase'
    # 试用已用过（含过期行）→ 不能再开
    assert access['trial_available'] is False


async def test_disabled_status_denies(db) -> None:
    cat = _catalog(f'off_{_uid()}', access_type='free', status='disabled')
    db.add(cat)
    owner = await _seed_owner(db, tier='flagship')
    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False
    assert access['reason'] == 'disabled'


# ============================ 闸门②挂载前置准入 ============================


async def test_mount_gate_blocks_unpaid_app(db) -> None:
    """付费未准入 app 挂载被拒，ForbiddenError 携 access（前端据此弹升级/购买）。"""
    app_id = f'tier_{_uid()}'
    db.add(_catalog(app_id, access_type='tier', min_tier='pro', trial_days=7))
    await db.flush()
    fresh_user = 920_000_000 + int(_uid(), 16) % 1_000_000  # 无订阅 → free
    with pytest.raises(errors.ForbiddenError) as ei:
        await workbench_domain_service.enable_current_workspace_app(db, user_id=fresh_user, app_id=app_id)
    assert ei.value.data['reason'] == 'need_upgrade'
    assert ei.value.data['requires'] == 'upgrade'


# ============================ 闸门③Runtime Gateway entitlement 维度 ============================


def _agent(owner_hasn_id: str) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=f'a_{_uid()}',
        agent_name='c4-agent',
        owner_hasn_id=owner_hasn_id,
        owner_user_id=0,
        scopes=[],
        session_uuid=f's-{_uid()}',
        expire_time=timezone.now() + timedelta(days=1),
    )


async def test_entitlement_gate_skips_free_app(db) -> None:
    app_id = f'free_{_uid()}'
    db.add(_catalog(app_id, access_type='free'))
    owner = await _seed_owner(db, tier='free')
    denial = await ai_native_runtime_gateway._entitlement_denial(db, app_id=app_id, agent=_agent(owner))
    assert denial is None


async def test_entitlement_gate_allows_entitled_owner(db) -> None:
    app_id = f'buy_{_uid()}'
    db.add(_catalog(app_id, access_type='purchase', price_amount=Decimal('39.90')))
    owner = await _seed_owner(db, tier='free')
    db.add(HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=owner, source='purchase',
        status='active', expires_at=timezone.now() + timedelta(days=30),
    ))
    await db.flush()
    denial = await ai_native_runtime_gateway._entitlement_denial(db, app_id=app_id, agent=_agent(owner))
    assert denial is None


@pytest.mark.parametrize('skip_mode_gate', [False, True], ids=['relay_face', 'mcp_direct_face'])
async def test_entitlement_gate_denies_unpaid_on_both_faces(db, skip_mode_gate: bool) -> None:
    """付费未准入：**两条到达面**（中继 + MCP 直连 shim）都经 _authorize_tool_call 拦下，
    返回 15030 deny 并写审计——证 gate③ 不在 skip_mode_gate 块内，分身无法经 MCP 面绕过付费墙。"""
    from backend.app.hasn.model import HasnAiNativeAppAudit

    app_id = f'tier_{_uid()}'
    db.add(_catalog(app_id, access_type='tier', min_tier='pro'))
    owner = await _seed_owner(db, tier='free')  # 免费档 → 不准入
    trace_id = f'c4-gate3-{_uid()}'

    deny = await ai_native_runtime_gateway._authorize_tool_call(
        db,
        body=SimpleNamespace(trace_id=trace_id),
        workspace={'kind': 'personal', 'user_id': 0, 'enterprise_id': None},
        agent=_agent(owner),
        manifest={'app_id': app_id, 'version': '1.0.0'},
        tool={'tool_id': 't1', 'mcp_name': 'hasn.x.t1', 'required_scopes': [], 'risk_level': 'low'},
        capability={'capability_id': 'c1', 'tool_id': 't1'},
        input_payload={},
        skip_mode_gate=skip_mode_gate,
    )
    assert deny is not None, 'gate③ 应拦下付费未准入'
    assert deny['decision'] == 'deny'
    assert deny['error']['code'] == '15030'

    audit = (
        await db.execute(
            sa.select(HasnAiNativeAppAudit).where(HasnAiNativeAppAudit.trace_id == trace_id)
        )
    ).scalars().first()
    assert audit is not None and audit.error_code == '15030'
    assert (audit.context or {}).get('reason') == 'entitlement_denied'


async def test_entitlement_gate_allows_after_upgrade_via_authorize(db) -> None:
    """owner 升级到足够档位后，gate③ 不再拦（_entitlement_denial 返回 None）。"""
    app_id = f'tier_{_uid()}'
    db.add(_catalog(app_id, access_type='tier', min_tier='pro'))
    owner = await _seed_owner(db, tier='pro')
    denial = await ai_native_runtime_gateway._entitlement_denial(db, app_id=app_id, agent=_agent(owner))
    assert denial is None
