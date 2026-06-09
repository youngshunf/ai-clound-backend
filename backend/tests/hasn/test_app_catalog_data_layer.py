"""C1 应用目录与权益数据层 真实 PostgreSQL 测试 + 进程内 HTTP E2E（零 mock）。

覆盖：
- ``ensure_catalog_seeded`` 幂等播种（knowledge/community/presentation 三行、全 free、像素等价默认值）
- ``hasn_app_catalog_service`` CRUD（create/get/update/delete）
- ``hasn_app_entitlement`` 行级 CRUD + ``sweep_expired_entitlements`` 过期兜底
- Admin catalog 读端点 HTTP E2E（分页列表 + 详情，统一信封）

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §4/§5.4；实施清单 §C1。
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

from backend.app.hasn.api.v1.admin.hasn_app_catalog import router as admin_catalog_router
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.schema.hasn_app_catalog import (
    CreateHasnAppCatalogParam,
    DeleteHasnAppCatalogParam,
    UpdateHasnAppCatalogParam,
)
from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded, sweep_expired_entitlements
from backend.app.hasn.service.hasn_app_catalog_service import hasn_app_catalog_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
# admin v1 前缀 /api/v1/hasn + codegen include 前缀 /hasn/app/catalogs（既有双 hasn 约定）。
_APP.include_router(admin_catalog_router, prefix='/api/v1/hasn/hasn/app/catalogs')

_CATALOG = '/api/v1/hasn/hasn/app/catalogs'
_SEED_APP_IDS = {'knowledge', 'community', 'presentation'}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _catalog_kwargs(app_id: str, **over) -> dict:
    base = {
        'app_id': app_id,
        'name': '测试应用',
        'icon': 'app-window',
        'icon_asset_uri': None,
        'description': 'C1 测试',
        'source': 'builtin',
        'status': 'published',
        'execution_mode': 'cloud',
        'scope': ['personal'],
        'collaboration_mode': 'none',
        'entry_route': '/x',
        'sort_order': 100,
        'default_mount': True,
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
    return base


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

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=900_000_001)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


# ============================ seed ============================


async def test_ensure_catalog_seeded_is_idempotent(env) -> None:
    """首次播种插入三内置行（全 free），二次播种零插入；display 像素等价默认值。"""
    db = env.session
    inserted = await ensure_catalog_seeded(db)
    assert inserted >= 1, '首次播种应至少插入缺失的内置行'

    rows = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id.in_(_SEED_APP_IDS)))).scalars().all()
    by_id = {r.app_id: r for r in rows}
    assert set(by_id) >= _SEED_APP_IDS, f'三内置 app 应全部存在: {set(by_id)}'
    # 迁移 M2 不变量：保持现状全免费。
    assert all(r.access_type == 'free' for r in by_id.values())
    # display 与 WorkbenchAppRegistry 一致。
    assert by_id['knowledge'].name == '知识库'
    assert by_id['knowledge'].icon == 'book-open'
    assert by_id['community'].icon == 'users-round'
    assert by_id['presentation'].execution_mode == 'embedded_desktop'
    assert by_id['knowledge'].status == 'published'

    # 二次播种幂等：不再插入。
    again = await ensure_catalog_seeded(db)
    assert again == 0, '二次播种应零插入（幂等）'


async def test_seed_does_not_overwrite_existing_display(env) -> None:
    """已存在行的 display/价格不被代码回写（代码不覆盖运营改动，设计 §6.1）。"""
    db = env.session
    # 先插入一个被「运营改过」的 knowledge 行（改名 + 付费）。
    db.add(HasnAppCatalog(**_catalog_kwargs('knowledge', name='运营改名', access_type='tier', min_tier='pro')))
    await db.flush()

    await ensure_catalog_seeded(db)

    row = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'knowledge'))).scalars().one()
    assert row.name == '运营改名', '已存在行不应被 seed 覆盖'
    assert row.access_type == 'tier', '已存在行定价不应被 seed 覆盖'


# ============================ catalog CRUD（service）============================


async def test_catalog_crud_via_service(env) -> None:
    """catalog create/get/update/delete 全链路（真实 PG，付费 app 字段往返）。"""
    db = env.session
    app_id = f'paid_{_uid()}'
    await hasn_app_catalog_service.create(
        db=db,
        obj=CreateHasnAppCatalogParam(
            **_catalog_kwargs(
                app_id,
                name='高级分析',
                access_type='purchase',
                price_amount='39.90',
                billing_cycle='month',
                trial_days=7,
            )
        ),
    )
    await db.flush()

    row = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == app_id))).scalars().one()
    assert row.access_type == 'purchase'
    assert str(row.price_amount) == '39.90'
    assert row.trial_days == 7

    fetched = await hasn_app_catalog_service.get(db=db, pk=row.id)
    assert fetched.app_id == app_id

    update_param = UpdateHasnAppCatalogParam(**_catalog_kwargs(app_id, name='高级分析Pro', status='disabled'))
    count = await hasn_app_catalog_service.update(db=db, pk=row.id, obj=update_param)
    assert count == 1
    await db.flush()
    await db.refresh(row)
    assert row.name == '高级分析Pro'
    assert row.status == 'disabled'

    deleted = await hasn_app_catalog_service.delete(db=db, obj=DeleteHasnAppCatalogParam(pks=[row.id]))
    assert deleted == 1
    await db.flush()
    gone = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == app_id))).scalars().first()
    assert gone is None


# ============================ entitlement + 过期兜底 ============================


async def test_entitlement_rows_and_expiry_sweep(env) -> None:
    """权益行 CRUD + sweep 只把已过期 active 行置 expired，未到期/永久不动。"""
    from backend.utils.timezone import timezone

    db = env.session
    now = timezone.now()
    app_id = f'ent_{_uid()}'
    sub = f'h_{_uid()}{_uid()}'[:38]  # 真实 hasn_id 长度（验证 varchar(40) 不截断）

    past = HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=sub, source='trial',
        status='active', expires_at=now - timedelta(days=1),
    )
    future = HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=f'{sub}b'[:38], source='purchase',
        status='active', expires_at=now + timedelta(days=30),
    )
    forever = HasnAppEntitlement(
        app_id=app_id, subject_type='owner', subject_id=f'{sub}c'[:38], source='purchase',
        status='active', expires_at=None,
    )
    db.add_all([past, future, forever])
    await db.flush()

    affected = await sweep_expired_entitlements(db)
    assert affected == 1, '只应有 1 条已过期 active 行被置 expired'
    await db.refresh(past)
    await db.refresh(future)
    await db.refresh(forever)
    assert past.status == 'expired'
    assert future.status == 'active', '未到期不应被动'
    assert forever.status == 'active', '永久买断（expires_at=NULL）不应被动'

    # 二次 sweep 幂等。
    assert await sweep_expired_entitlements(db) == 0


# ============================ Admin 读端点 HTTP E2E ============================


async def test_admin_catalog_list_and_detail_http(env) -> None:
    """Admin 分页列表 + 详情走真实 HTTP + 统一信封（真实 PG）。"""
    db = env.session
    app_id = f'http_{_uid()}'
    db.add(HasnAppCatalog(**_catalog_kwargs(app_id, name='HTTP 列表测试')))
    await db.flush()
    row = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == app_id))).scalars().one()

    page = _data(await env.client.get(_CATALOG))
    assert 'items' in page
    assert any(item['app_id'] == app_id for item in page['items']), '新建行应出现在分页列表'

    detail = _data(await env.client.get(f'{_CATALOG}/{row.id}'))
    assert detail['app_id'] == app_id
    assert detail['name'] == 'HTTP 列表测试'
    assert detail['access_type'] == 'free'
    assert detail['scope'] == ['personal']
