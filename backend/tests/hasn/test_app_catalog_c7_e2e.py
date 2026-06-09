"""C7 商业化准入 五场景全栈 E2E（真实 PostgreSQL，零 mock）。

实施清单 §C7 验收：免费 app / tier 准入(升级前后) / 一次性购买 / 试用(开通+到期) / 下架——
每个场景**同时**断言两个面：
- 工作台展示面（``resolve_app_access`` = 闸门①数据，节点付费墙据此渲染）；
- **真实 Runtime Gateway 工具调用路径**（``_authorize_tool_call`` 闸门③ + 审计 15030），
  证明分身侧的实际能力放行/拦截与展示一致——不只打 service 层
  （见 [[feedback_ai_native_gateway_two_call_faces]]）。

每个场景走真实写操作（订阅 UserSubscription / 购买回调 apply_app_purchase / 试用 open_trial +
过期兜底 sweep_expired_entitlements），不预置「终态」行而是经业务路径达到终态。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §5；实施清单 §C7。
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
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.app_purchase_callback import apply_app_purchase
from backend.app.user_tier.model import UserSubscription
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _catalog(app_id: str, **over) -> HasnAppCatalog:
    base = {
        'app_id': app_id,
        'name': 'C7 应用',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'C7 E2E',
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


def _agent(owner_hasn_id: str) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=f'a_{_uid()}',
        agent_name='c7-agent',
        owner_hasn_id=owner_hasn_id,
        owner_user_id=0,
        scopes=[],
        session_uuid=f's-{_uid()}',
        expire_time=timezone.now() + timedelta(days=1),
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


async def _seed_owner(db) -> tuple[str, int]:
    user_id = 930_000_000 + int(_uid(), 16) % 1_000_000
    owner = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=owner, star_id=f's{user_id}', user_id=user_id, nickname='C7 owner'))
    await db.flush()
    return owner, user_id


def _activate_subscription(user_id: int, tier: str) -> UserSubscription:
    """模拟订阅支付成功后的终态（subscribe 回调写 UserSubscription 的数据结果）。"""
    now = timezone.now()
    return UserSubscription(
        app_code='huanxing', user_id=user_id, tier=tier, subscription_type='monthly',
        monthly_credits=Decimal(0), current_credits=Decimal(0), used_credits=Decimal(0),
        purchased_credits=Decimal(0), billing_cycle_start=now - timedelta(days=1),
        billing_cycle_end=now + timedelta(days=29), subscription_start_date=now - timedelta(days=1),
        subscription_end_date=now + timedelta(days=30), status='active', auto_renew=True, max_agents=3,
    )


async def _gate3_denial(db, app_id: str, owner: str):
    """闸门③：分身工具调用维度是否拦截（None=放行）。"""
    return await ai_native_runtime_gateway._entitlement_denial(db, app_id=app_id, agent=_agent(owner))


async def _gate3_authorize_denies(db, app_id: str, owner: str, *, skip_mode_gate: bool) -> dict | None:
    """闸门③ 经真实 _authorize_tool_call 全路径（含审计写入）。"""
    return await ai_native_runtime_gateway._authorize_tool_call(
        db,
        body=SimpleNamespace(trace_id=f'c7-{_uid()}'),
        workspace={'kind': 'personal', 'user_id': 0, 'enterprise_id': None},
        agent=_agent(owner),
        manifest={'app_id': app_id, 'version': '1.0.0'},
        tool={'tool_id': 't1', 'mcp_name': 'hasn.x.t1', 'required_scopes': [], 'risk_level': 'low'},
        capability={'capability_id': 'c1', 'tool_id': 't1'},
        input_payload={},
        skip_mode_gate=skip_mode_gate,
    )


# ============================ 场景一：免费 app ============================


async def test_scenario_free_app_always_open(db) -> None:
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'free_{_uid()}', access_type='free')
    db.add(cat)
    await db.flush()

    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True and access['reason'] == 'free'
    # 闸门③ 放行（免费 app 不进 entitlement 维度）。
    assert await _gate3_denial(db, cat.app_id, owner) is None


# ============================ 场景二：tier 准入（升级前后）============================


async def test_scenario_tier_upgrade_flips_access(db) -> None:
    owner, user_id = await _seed_owner(db)
    cat = _catalog(f'tier_{_uid()}', access_type='tier', min_tier='pro', trial_days=0)
    db.add(cat)
    await db.flush()

    # 升级前：免费档 → 展示 need_upgrade，分身工具调用被拦（两条到达面都拦 + 审计 15030）。
    before = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert before['allowed'] is False and before['reason'] == 'need_upgrade'
    assert before['requires'] == 'upgrade' and before['min_tier'] == 'pro'
    for skip in (False, True):
        deny = await _gate3_authorize_denies(db, cat.app_id, owner, skip_mode_gate=skip)
        assert deny is not None and deny['decision'] == 'deny' and deny['error']['code'] == '15030'

    # 升级：写入 active pro 订阅（subscribe 支付成功终态）。
    db.add(_activate_subscription(user_id, 'pro'))
    await db.flush()

    # 升级后：展示 tier_ok，分身工具调用放行。
    after = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert after['allowed'] is True and after['reason'] == 'tier_ok'
    assert await _gate3_denial(db, cat.app_id, owner) is None


# ============================ 场景三：一次性购买 ============================


async def test_scenario_one_time_purchase(db) -> None:
    owner, user_id = await _seed_owner(db)
    cat = _catalog(
        f'buy_{_uid()}', access_type='purchase', price_amount=Decimal('99.00'),
        price_unit='cny', billing_cycle='once', trial_days=0,
    )
    db.add(cat)
    await db.flush()

    # 购买前：need_purchase + 带价；闸门③ 拦截。
    before = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert before['allowed'] is False and before['reason'] == 'need_purchase'
    assert before['price'] == {'amount': 99.0, 'unit': 'cny', 'cycle': 'once'}
    assert await _gate3_denial(db, cat.app_id, owner) is not None

    # 真实购买回调（apply_app_purchase）：写 source=purchase 权益，once → 永久（expires_at=None）。
    order = SimpleNamespace(
        extra_data={'app_id': cat.app_id, 'app_code': 'huanxing'}, user_id=user_id,
        order_no=f'HX{_uid()}', billing_cycle='once', pay_amount=9900,
    )
    ent = await apply_app_purchase(db, order=order)
    assert ent is not None and ent.source == 'purchase' and ent.expires_at is None

    # 购买后：entitled + 闸门③ 放行。
    after = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert after['allowed'] is True and after['reason'] == 'entitled'
    assert await _gate3_denial(db, cat.app_id, owner) is None


# ============================ 场景四：试用（开通 + 到期）============================


async def test_scenario_trial_open_then_expire(db) -> None:
    owner, _ = await _seed_owner(db)
    cat = _catalog(
        f'trial_{_uid()}', access_type='purchase', price_amount=Decimal('29.00'),
        billing_cycle='month', trial_days=7,
    )
    db.add(cat)
    await db.flush()

    # 试用前：need_purchase + trial_available。
    before = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert before['allowed'] is False and before['trial_available'] is True
    assert await _gate3_denial(db, cat.app_id, owner) is not None

    # 开通试用 → trialing + 闸门③ 放行。
    ent = await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner)
    assert ent.source == 'trial' and ent.status == 'active'
    trialing = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert trialing['allowed'] is True and trialing['reason'] == 'trialing'
    assert await _gate3_denial(db, cat.app_id, owner) is None

    # 到期：把 expires_at 拨到过去 + 过期兜底 sweep → 权益 expired。
    ent.expires_at = timezone.now() - timedelta(minutes=1)
    await db.flush()
    swept = await app_catalog_service.sweep_expired_entitlements(db)
    assert swept >= 1
    await db.refresh(ent)
    assert ent.status == 'expired'

    # 到期后：回到 need_purchase，且试用机会已用尽（不再 trial_available）；闸门③ 重新拦截。
    after = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert after['allowed'] is False and after['reason'] == 'need_purchase'
    assert after['trial_available'] is False
    assert await _gate3_denial(db, cat.app_id, owner) is not None


# ============================ 场景五：下架 ============================


async def test_scenario_disabled_app_blocked_everywhere(db) -> None:
    owner, user_id = await _seed_owner(db)
    # 即便 owner 顶级订阅，下架 app 对任何人都不可用。
    db.add(_activate_subscription(user_id, 'flagship'))
    cat = _catalog(f'off_{_uid()}', access_type='tier', min_tier='pro', status='disabled')
    db.add(cat)
    await db.flush()

    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is False and access['reason'] == 'disabled'
    # 闸门③ 同样拦截下架 app 的工具调用。
    assert await _gate3_denial(db, cat.app_id, owner) is not None
    deny = await _gate3_authorize_denies(db, cat.app_id, owner, skip_mode_gate=False)
    assert deny is not None and deny['error']['code'] == '15030'


# ============================ 收尾：管理员授予/撤销贯穿 ============================


async def test_scenario_admin_grant_revoke_round_trip(db) -> None:
    """运营兜底：admin 直接授予权益即时解锁，撤销后即时回到拦截（C5 §5.4）。"""
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'gr_{_uid()}', access_type='purchase', price_amount=Decimal('59.00'))
    db.add(cat)
    await db.flush()
    assert await _gate3_denial(db, cat.app_id, owner) is not None

    ent = await app_catalog_service.grant_entitlement(
        db, app_id=cat.app_id, subject_type='owner', subject_id=owner, source='admin_grant'
    )
    assert await _gate3_denial(db, cat.app_id, owner) is None

    assert await app_catalog_service.revoke_entitlement(db, entitlement_id=ent.id) is True
    assert await _gate3_denial(db, cat.app_id, owner) is not None
