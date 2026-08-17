"""C1 应用目录与权益数据层 真实 PostgreSQL 测试 + 进程内 HTTP E2E（零 mock）。

覆盖：
- ``ensure_catalog_seeded`` 幂等播种（knowledge/community/deck 三行、全 free、像素等价默认值）
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
from pydantic import ValidationError
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
    GetHasnAppCatalogDetail,
    UpdateHasnAppCatalogParam,
)
from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded, sweep_expired_entitlements
from backend.app.hasn.service.hasn_app_catalog_service import hasn_app_catalog_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.rbac import rbac_verify
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
# admin v1 前缀 /api/v1/hasn + include 前缀 /app-catalogs（admin scope，无 /app/ 段避免与 app-scope 混淆）。
_APP.include_router(admin_catalog_router, prefix='/api/v1/hasn/app-catalogs')

_CATALOG = '/api/v1/hasn/app-catalogs'
_SEED_APP_IDS = {'knowledge', 'community', 'deck'}


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

    async def _rbac_bypass() -> None:
        """写端点（POST/PUT）挂着 DependsRBAC，测试库里没有角色菜单数据会被它先拦掉。

        只旁路 RBAC 这一层，body 校验照常跑——否则拿到的会是 403 而不是要验的 422。
        同挂的 ``RequestPermission`` 不需要旁路：它只往 ctx 里写权限标识，自己不拒绝。
        """

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject
    _APP.dependency_overrides[rbac_verify] = _rbac_bypass

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
    """播种后内置行齐备（全 free），二次播种零插入；display 像素等价默认值。

    注：dev DB 由运行中云端 register_init reconcile 已 seed（C2），故首次 inserted 可能为 0；
    幂等语义只保证「调用后内置行齐备 + display 正确」，不假设调用前为空。
    内置集随注册新增（knowledge/community/deck），见 _SEED_APP_IDS。
    """
    db = env.session
    # 删 knowledge/community 行后重播种 → 走「新插入」路径，断言出厂 brand-* 图标
    # （ensure_catalog_seeded 是 INSERT-only，不回写存量 dev 行；存量行图标由迁移 SQL 刷新）。
    await db.execute(sa.delete(HasnAppCatalog).where(HasnAppCatalog.app_id.in_({'knowledge', 'community'})))
    await db.flush()
    await ensure_catalog_seeded(db)

    rows = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id.in_(_SEED_APP_IDS)))).scalars().all()
    by_id = {r.app_id: r for r in rows}
    assert set(by_id) >= _SEED_APP_IDS, f'内置 app 应全部存在: {set(by_id)}'
    # 迁移 M2 不变量：保持现状全免费。
    assert all(r.access_type == 'free' for r in by_id.values())
    # display 与 AppCatalogRegistry 一致。
    assert by_id['knowledge'].name == '知识库'
    # 应用中心改版：icon 出厂即 brand-* 彩色品牌 token（webui 按 token 渲染渐变方块）。
    assert by_id['knowledge'].icon == 'brand-knowledge'
    assert by_id['community'].icon == 'brand-community'
    assert by_id['knowledge'].status == 'published'
    # deck（自研演示文稿，模块 17）：local_tool + 自动挂载（default_mount=True，唯一默认演示文稿应用）。
    assert by_id['deck'].execution_mode == 'local_tool'
    assert by_id['deck'].source == 'builtin'
    assert by_id['deck'].default_mount is True
    assert by_id['deck'].sort_order == 35

    # 二次播种幂等：不再插入。
    again = await ensure_catalog_seeded(db)
    assert again == 0, '二次播种应零插入（幂等）'


async def test_seed_does_not_overwrite_existing_display(env) -> None:
    """已存在行的 display/价格不被代码回写（代码不覆盖运营改动，设计 §6.1）。"""
    db = env.session
    await ensure_catalog_seeded(db)  # 确保 knowledge 行存在
    # 把已存在的 knowledge 行改成被「运营改过」的样子（改名 + 付费），会话内变更，结束回滚还原。
    row = (await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'knowledge'))).scalars().one()
    row.name = '运营改名'
    row.access_type = 'tier'
    row.min_tier = 'pro'
    await db.flush()

    await ensure_catalog_seeded(db)

    await db.refresh(row)
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


# ============================ 上架状态写入面枚举校验 ============================


async def test_catalog_status_write_params_reject_foreign_dict_values() -> None:
    """写入面 status 只收 published/disabled/draft，别的模块的状态值一律 422（不落库）。

    背景：管理端的「上架状态」下拉曾绑到共享字典 ``hasn_status``——``fba codegen`` 把每张
    ``hasn_*`` 表的 ``status`` 列都往同一个 type_code 里塞值，累计约 50 个取值（联系人的
    ``connected``、绑定的 ``bound``、消息的 ``accepted``…）。管理端已改本地三值常量，但写入面
    当时是裸 ``str``，绕过 UI 直接调 API 仍能把 ``accepted`` 写进 ``status``；而
    ``list_published_catalog`` 只选 ``published``，于是应用从应用中心、工作台、分身工具面同时
    静默消失且不报错。本测试钉死那道校验。
    """
    app_id = f'enum_{_uid()}'

    # 三个合法值都必须通过（收窄不能误伤正常上下架）。
    for ok in ('published', 'disabled', 'draft'):
        assert CreateHasnAppCatalogParam(**_catalog_kwargs(app_id, status=ok)).status == ok
        assert UpdateHasnAppCatalogParam(**_catalog_kwargs(app_id, status=ok)).status == ok

    # 其它模块塞进 hasn_status 的取值一律拒收——含最危险的 accepted（「已接收」，来自联系人/
    # 内测申请域），它在下拉里紧挨着「已上架」。
    for foreign in ('accepted', 'connected', 'bound', 'active', 'pending', '1', '0', ''):
        with pytest.raises(ValidationError):
            CreateHasnAppCatalogParam(**_catalog_kwargs(app_id, status=foreign))
        with pytest.raises(ValidationError):
            UpdateHasnAppCatalogParam(**_catalog_kwargs(app_id, status=foreign))

    # 读取面**不得**跟着收窄：详情模型仍是裸 str，否则存量脏行会在 GET 时 500
    # （校验的位置本身就是这条测试要守的东西）。
    detail_status = GetHasnAppCatalogDetail.model_fields['status'].annotation
    assert detail_status is str, '详情模型的 status 必须保持裸 str，收窄只允许发生在写入面'


async def test_admin_catalog_create_rejects_foreign_status_over_http(env) -> None:
    """同一道收窄要在**真实 HTTP** 上兑现：外来状态值 422，合法值照常落库。

    只验 schema 类不够——校验挂在端点入参上，走 ASGI 才能证明它没被中间件/依赖顺序绕过。
    """
    app_id = f'httpenum_{_uid()}'

    bad = await env.client.post(_CATALOG, json=_catalog_kwargs(app_id, status='accepted'))
    assert bad.status_code == 422, f'外来状态值应被入参校验拦下，实际 {bad.status_code}: {bad.text}'
    assert 'status' in bad.text, f'422 应指名 status 字段，便于管理员定位：{bad.text}'

    # 收窄不能误伤正常下架：同一条链路上 disabled 必须通到底并真的落库。
    _data(await env.client.post(_CATALOG, json=_catalog_kwargs(app_id, status='disabled')))
    row = (await env.session.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == app_id))).scalars().one()
    assert row.status == 'disabled'
