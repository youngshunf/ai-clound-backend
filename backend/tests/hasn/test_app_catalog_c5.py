"""C5 商业化管理面 真实 PostgreSQL 测试（零 mock）。

覆盖（设计 §5.1/§5.4，实施清单 §C5）：
- 权益写操作：``open_trial``（一次性 + 付费前置 + 占位互斥）、``grant_entitlement``（幂等）、
  ``revoke_entitlement``（软撤销）、``list_entitlements``
- 购买回调核心 ``apply_app_purchase``：订单 → owner 解析 → 算到期 → 写 source=purchase 权益（带 order_ref）
- ``purchase_expiry`` 周期映射（once=永久 / month≈30d / year≈365d）
- 写操作后 ``resolve_app_access`` 准入随之翻转（trialing / entitled / 撤销后回 need_purchase）
- app-scope HTTP：开通试用 + 我的权益
- admin HTTP：语义化授予 + 软撤销

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §5。
"""
from __future__ import annotations

import uuid

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.admin.hasn_app_entitlement import router as admin_entitlement_router
from backend.app.home.api.v1.app.home import router as workbench_router
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.app.hasn.service.app_purchase_callback import apply_app_purchase
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.rbac import rbac_verify
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio

# 真实前缀：app workbench → /api/v1/hasn/app；admin entitlement → /api/v1/hasn/app-entitlements（admin scope，无 /app/ 段）。
_APP_PREFIX = '/api/v1/hasn/app'
_ADMIN_PREFIX = '/api/v1/hasn/app-entitlements'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _catalog(app_id: str, **over) -> HasnAppCatalog:
    base = {
        'app_id': app_id,
        'name': 'C5 测试应用',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'C5',
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
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    # 当前登录身份（由 _seed_owner 写入真实 user_id 后回填）。
    state = SimpleNamespace(user_id=900_000_001)

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=state.user_id)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
    register_exception(app)
    app.include_router(workbench_router, prefix=_APP_PREFIX)
    app.include_router(admin_entitlement_router, prefix=_ADMIN_PREFIX)
    app.dependency_overrides[get_db] = _yield_session
    app.dependency_overrides[get_db_transaction] = _yield_session
    app.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject
    app.dependency_overrides[rbac_verify] = lambda: None  # 绕过 RBAC，聚焦业务逻辑

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, state=state)
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_owner(db) -> tuple[str, int]:
    """造 owner（HasnHumans），返回 (owner_hasn_id, user_id)。"""
    user_id = 920_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=owner_hasn_id, star_id=f's{user_id}', user_id=user_id, nickname='C5 owner'))
    await db.flush()
    return owner_hasn_id, user_id


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


# ============================ 试用 open_trial ============================


async def test_open_trial_writes_active_trial_and_flips_access(env) -> None:
    """付费 app 开通试用 → 写 source=trial active 权益；resolve_app_access 随之 trialing。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'tr_{_uid()}', access_type='purchase', price_amount='19.90', billing_cycle='month', trial_days=7)
    db.add(cat)
    await db.flush()

    before = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert before['allowed'] is False
    assert before['reason'] == 'need_purchase'
    assert before['trial_available'] is True

    ent = await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner)
    assert ent.source == 'trial'
    assert ent.status == 'active'
    assert ent.expires_at is not None and ent.expires_at > timezone.now()

    after = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert after['allowed'] is True
    assert after['reason'] == 'trialing'


async def test_open_trial_only_once(env) -> None:
    """同一 owner 对同一 app 仅能开一次试用（第二次抛 RequestError）。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'tr_{_uid()}', access_type='tier', min_tier='pro', trial_days=14)
    db.add(cat)
    await db.flush()

    await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner)
    with pytest.raises(errors.RequestError):
        await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner)


async def test_open_trial_rejects_free_app(env) -> None:
    """免费 app 无需试用 → RequestError。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'fr_{_uid()}', access_type='free')
    db.add(cat)
    await db.flush()
    with pytest.raises(errors.RequestError):
        await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner)


async def test_open_trial_blocked_when_already_entitled(env) -> None:
    """已有 active 权益 → 试用被拒（避免重复占位）。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'tr_{_uid()}', access_type='purchase', price_amount='9.90', billing_cycle='once', trial_days=7)
    db.add(cat)
    await db.flush()
    await app_catalog_service.grant_entitlement(
        db, app_id=cat.app_id, subject_type='owner', subject_id=owner, source='purchase'
    )
    with pytest.raises(errors.RequestError):
        await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=owner)


# ============================ 授予 / 撤销 ============================


async def test_grant_entitlement_is_idempotent(env) -> None:
    """grant 两次同主体同 app → 返回同一行（active 唯一约束不撞）。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    app_id = f'gr_{_uid()}'
    first = await app_catalog_service.grant_entitlement(
        db, app_id=app_id, subject_type='owner', subject_id=owner, source='admin_grant'
    )
    second = await app_catalog_service.grant_entitlement(
        db, app_id=app_id, subject_type='owner', subject_id=owner, source='admin_grant'
    )
    assert first.id == second.id


async def test_revoke_entitlement_flips_status_and_access(env) -> None:
    """撤销后 status=revoked，且付费 app 准入回到 need_purchase。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    cat = _catalog(f'rv_{_uid()}', access_type='purchase', price_amount='29.90', billing_cycle='month')
    db.add(cat)
    await db.flush()
    ent = await app_catalog_service.grant_entitlement(
        db, app_id=cat.app_id, subject_type='owner', subject_id=owner, source='purchase'
    )
    granted = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert granted['allowed'] is True

    ok = await app_catalog_service.revoke_entitlement(db, entitlement_id=ent.id)
    assert ok is True
    await db.refresh(ent)
    assert ent.status == 'revoked'

    revoked = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert revoked['allowed'] is False
    assert revoked['reason'] == 'need_purchase'

    # 二次撤销幂等无效（非 active）。
    assert await app_catalog_service.revoke_entitlement(db, entitlement_id=ent.id) is False


# ============================ 购买回调 apply_app_purchase ============================


async def test_apply_app_purchase_writes_purchase_entitlement(env) -> None:
    """订单支付成功核心：写 source=purchase + order_ref + 按 billing_cycle 算到期；准入翻转 entitled。"""
    db = env.session
    owner, user_id = await _seed_owner(db)
    cat = _catalog(f'pc_{_uid()}', access_type='purchase', price_amount='39.90', billing_cycle='month')
    db.add(cat)
    await db.flush()

    order = SimpleNamespace(
        extra_data={'app_id': cat.app_id, 'app_code': 'huanxing'},
        user_id=user_id,
        order_no=f'HX{_uid()}',
        billing_cycle='month',
        pay_amount=3990,
    )
    ent = await apply_app_purchase(db, order=order)
    assert ent is not None
    assert ent.source == 'purchase'
    assert ent.order_ref == order.order_no
    assert ent.expires_at is not None  # month → 非永久
    # 30 天到期窗口（容差 1 天）。
    assert abs((ent.expires_at - timezone.now()).days - 30) <= 1

    access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner)
    assert access['allowed'] is True
    assert access['reason'] == 'entitled'


async def test_apply_app_purchase_missing_owner_returns_none(env) -> None:
    """无 owner 映射 → 不写权益（返回 None，不抛）。"""
    db = env.session
    order = SimpleNamespace(
        extra_data={'app_id': 'whatever'},
        user_id=999_999_999,
        order_no=f'HX{_uid()}',
        billing_cycle='month',
        pay_amount=100,
    )
    assert await apply_app_purchase(db, order=order) is None


async def test_purchase_expiry_cycles() -> None:
    """周期 → 到期映射：once=永久(None)，month≈30d，year≈365d。"""
    now = timezone.now()
    assert app_catalog_service.purchase_expiry('once') is None
    assert app_catalog_service.purchase_expiry(None) is None
    month = app_catalog_service.purchase_expiry('month')
    assert month is not None and abs((month - now).days - 30) <= 1
    year = app_catalog_service.purchase_expiry('year')
    assert year is not None and abs((year - now).days - 365) <= 1


# ============================ app-scope HTTP ============================


async def test_http_open_trial_and_list_entitlements(env) -> None:
    """POST /apps/{id}/trial → 写试用；GET /apps/entitlements → 我的权益含该行。"""
    db = env.session
    owner, user_id = await _seed_owner(db)
    env.state.user_id = user_id  # 让 _resolve_owner_id 命中这个 owner
    cat = _catalog(f'ht_{_uid()}', access_type='purchase', price_amount='12.00', billing_cycle='once', trial_days=5)
    db.add(cat)
    await db.flush()

    created = _data(await env.client.post(f'{_APP_PREFIX}/apps/{cat.app_id}/trial'))
    assert created['app_id'] == cat.app_id
    assert created['source'] == 'trial'
    assert created['status'] == 'active'

    listed = _data(await env.client.get(f'{_APP_PREFIX}/apps/entitlements'))
    assert any(e['app_id'] == cat.app_id and e['source'] == 'trial' for e in listed)

    # active_only 过滤同样命中（试用未过期）。
    listed_active = _data(await env.client.get(f'{_APP_PREFIX}/apps/entitlements?active_only=true'))
    assert any(e['app_id'] == cat.app_id for e in listed_active)


async def test_http_trial_unknown_app_404(env) -> None:
    """对不存在的 app 开试用 → 404。"""
    db = env.session
    _, user_id = await _seed_owner(db)
    env.state.user_id = user_id
    resp = await env.client.post(f'{_APP_PREFIX}/apps/nope_{_uid()}/trial')
    assert resp.status_code == 404, resp.text


# ============================ admin HTTP grant / revoke ============================


async def test_http_admin_grant_then_revoke(env) -> None:
    """admin POST /grant 幂等授予 → POST /{id}/revoke 软撤销。"""
    db = env.session
    owner, _ = await _seed_owner(db)
    app_id = f'ag_{_uid()}'

    granted = _data(
        await env.client.post(
            f'{_ADMIN_PREFIX}/grant',
            json={'app_id': app_id, 'subject_type': 'owner', 'subject_id': owner},
        )
    )
    assert granted['app_id'] == app_id
    assert granted['source'] == 'admin_grant'
    assert granted['status'] == 'active'
    ent_id = granted['id']

    # 幂等：再 grant 返回同一行。
    again = _data(
        await env.client.post(
            f'{_ADMIN_PREFIX}/grant',
            json={'app_id': app_id, 'subject_type': 'owner', 'subject_id': owner},
        )
    )
    assert again['id'] == ent_id

    revoke_resp = await env.client.post(f'{_ADMIN_PREFIX}/{ent_id}/revoke')
    assert revoke_resp.status_code == 200, revoke_resp.text
    assert revoke_resp.json()['code'] == 200

    row = (
        await db.execute(sa.select(HasnAppEntitlement).where(HasnAppEntitlement.id == ent_id))
    ).scalars().one()
    assert row.status == 'revoked'

    # 再撤销 → 4xx（非 active）。
    second = await env.client.post(f'{_ADMIN_PREFIX}/{ent_id}/revoke')
    assert second.status_code >= 400
