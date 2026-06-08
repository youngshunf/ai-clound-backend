"""桌面端订阅与积分计费 · 云端真实 HTTP E2E（真实 PostgreSQL，零 mock）。

覆盖（B0/B0'/B0''/B0''' 中可在进程内真实验证的部分）：
  - B0''' 用户端积分流水端点 `GET /api/v1/user_tier/app/subscription/transactions`：
    时间倒序、user_id+app_code 严格隔离、含 LLM 消耗(usage/llm_usage)、按类型筛选、统一信封、分页。
  - B0  `CreatePayOrderParam` 可辨识联合跨字段校验（subscribe 必 tier / credit_pack 必 package_id）。
  - B0' 渠道工厂 `_build_client` 把 `alipay_qr` 路由到 `AlipayQrClient`（当面付 precreate）。

真实支付下单返回 `qr_code_url`（依赖真实渠道商户凭据 + 网络）属 E1 真实 E2E（打运行中 8020），
本进程内测试不 mock 支付 SDK（零 fake），只覆盖数据层端点 + 纯构造逻辑。

模块级把 app subscription 路由挂最小 app，AppContextMiddleware 注入 app_code，
dependency_overrides 把 DependsJwtAuth 换成注入 user_id、get_db 指向真实 PG。
每测试用唯一 user_id 隔离。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as dt_tz
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi_pagination import add_pagination
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.user_tier.api.v1.app.subscription import router as app_subscription_router
from backend.app.user_tier.model import CreditTransaction
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction
from backend.middleware.app_context_middleware import AppContextMiddleware

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(AppContextMiddleware)
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_subscription_router, prefix='/api/v1/user_tier/app/subscription')
add_pagination(_APP)

_TXNS = '/api/v1/user_tier/app/subscription/transactions'
_DAILY = '/api/v1/user_tier/app/subscription/transactions/daily'


def _new_user_id() -> int:
    return 960_000_000 + int(uuid.uuid4().int % 20_000_000)


def _txn(
    user_id: int,
    *,
    ttype: str,
    credits: Decimal,
    before: Decimal,
    after: Decimal,
    ref_type: str | None,
    desc: str,
    created: datetime,
    app_code: str = 'huanxing',
    extra: dict | None = None,
) -> CreditTransaction:
    txn = CreditTransaction(
        app_code=app_code,
        user_id=user_id,
        transaction_type=ttype,
        credits=credits,
        balance_before=before,
        balance_after=after,
        reference_id=None,
        reference_type=ref_type,
        description=desc,
        extra_data=extra,
    )
    # created_time 是 init=False 自动时间戳，构造后显式赋值以控制排序断言。
    txn.created_time = created
    return txn


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    auth_state = {'user_id': _new_user_id()}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request):
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'], is_superuser=False)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, auth_state=auth_state)
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


async def test_transactions_ordered_isolated_with_llm_usage(env) -> None:
    """流水时间倒序 + user/app 隔离 + 含 LLM 消耗(usage/llm_usage) + 统一信封。"""
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    base = datetime(2026, 6, 1, 12, 0, 0)
    # 我的三条：购买(+) / 月度发放(+) / LLM 消耗(-)
    s.add(_txn(uid, ttype='purchase', credits=Decimal('1100'), before=Decimal('0'), after=Decimal('1100'),
               ref_type='pay_order', desc='购买积分包', created=base))
    s.add(_txn(uid, ttype='monthly_grant', credits=Decimal('1000'), before=Decimal('1100'), after=Decimal('2100'),
               ref_type='pay_order', desc='月度赠送', created=base + timedelta(minutes=1)))
    s.add(_txn(uid, ttype='usage', credits=Decimal('-12.5'), before=Decimal('2100'), after=Decimal('2087.5'),
               ref_type='llm_usage', desc='LLM 调用消耗', created=base + timedelta(minutes=2)))
    # 别的用户一条（隔离）
    other = _new_user_id()
    s.add(_txn(other, ttype='purchase', credits=Decimal('999'), before=Decimal('0'), after=Decimal('999'),
               ref_type='pay_order', desc='别人的', created=base + timedelta(minutes=3)))
    # 另一 app_code 一条（隔离）
    s.add(_txn(uid, ttype='purchase', credits=Decimal('777'), before=Decimal('0'), after=Decimal('777'),
               ref_type='pay_order', desc='知小鸦的', created=base + timedelta(minutes=4), app_code='zhixiaoya'))
    await s.flush()

    data = _data(await c.get(_TXNS, params={'page': 1, 'size': 50}))
    items = data['items']
    assert len(items) == 3, f'仅我的 huanxing 三条（隔离别人/别 app），实际 {len(items)}'
    # 时间倒序：usage(最新) → monthly_grant → purchase
    assert [it['transaction_type'] for it in items] == ['usage', 'monthly_grant', 'purchase']
    # LLM 消耗条目：负积分 + reference_type=llm_usage
    usage = items[0]
    assert usage['reference_type'] == 'llm_usage'
    assert Decimal(str(usage['credits'])) < 0, 'usage 为消耗（负积分）'
    # 不泄漏别人/别 app 的描述
    descs = {it['description'] for it in items}
    assert '别人的' not in descs and '知小鸦的' not in descs


async def test_transactions_filter_by_type(env) -> None:
    """按 transaction_type 筛选只返回该类。"""
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    base = datetime(2026, 6, 2, 9, 0, 0)
    s.add(_txn(uid, ttype='purchase', credits=Decimal('100'), before=Decimal('0'), after=Decimal('100'),
               ref_type='pay_order', desc='买', created=base))
    s.add(_txn(uid, ttype='usage', credits=Decimal('-5'), before=Decimal('100'), after=Decimal('95'),
               ref_type='llm_usage', desc='耗', created=base + timedelta(minutes=1)))
    await s.flush()

    data = _data(await c.get(_TXNS, params={'page': 1, 'size': 50, 'transaction_type': 'usage'}))
    assert len(data['items']) == 1 and data['items'][0]['transaction_type'] == 'usage'


async def test_transactions_empty_returns_envelope(env) -> None:
    """无流水也返回统一信封 + 空 items（零 fake）。"""
    c = env.client
    data = _data(await c.get(_TXNS, params={'page': 1, 'size': 20}))
    assert data['items'] == [] and data['total'] == 0


async def test_transactions_daily_aggregates_by_local_day(env) -> None:
    """按日聚合：按 Asia/Shanghai 本地日分组（跨 UTC 日界正确归日）+ 消耗/入账/净额/笔数 + 隔离。"""
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    utc = dt_tz.utc
    # 06-07 12:00Z = 06-07 20:00+08 → 本地 06-07
    s.add(_txn(uid, ttype='purchase', credits=Decimal('1000'), before=Decimal('0'), after=Decimal('1000'),
               ref_type='pay_order', desc='充值', created=datetime(2026, 6, 7, 12, 0, tzinfo=utc)))
    # 06-07 13:00Z = 06-07 21:00+08 → 本地 06-07（带 token 用量）
    s.add(_txn(uid, ttype='usage', credits=Decimal('-30'), before=Decimal('1000'), after=Decimal('970'),
               ref_type='llm_usage', desc='耗1', created=datetime(2026, 6, 7, 13, 0, tzinfo=utc),
               extra={'model_name': 'qwen3-max', 'input_tokens': 1200, 'output_tokens': 300}))
    # 06-07 20:00Z = 06-08 04:00+08 → 本地 06-08（跨日界）
    s.add(_txn(uid, ttype='usage', credits=Decimal('-12'), before=Decimal('970'), after=Decimal('958'),
               ref_type='llm_usage', desc='耗2', created=datetime(2026, 6, 7, 20, 0, tzinfo=utc),
               extra={'model_name': 'qwen3-max', 'input_tokens': 400, 'output_tokens': 100}))
    # 隔离：别的用户 + 别的 app_code 不计入
    s.add(_txn(_new_user_id(), ttype='usage', credits=Decimal('-999'), before=Decimal('0'), after=Decimal('-999'),
               ref_type='llm_usage', desc='别人', created=datetime(2026, 6, 7, 12, 0, tzinfo=utc)))
    s.add(_txn(uid, ttype='usage', credits=Decimal('-888'), before=Decimal('0'), after=Decimal('-888'),
               ref_type='llm_usage', desc='别app', created=datetime(2026, 6, 7, 12, 0, tzinfo=utc), app_code='zhixiaoya'))
    await s.flush()

    data = _data(await c.get(_DAILY, params={'page': 1, 'size': 20}))
    items = data['items']
    assert data['total'] == 2 and len(items) == 2, f'应聚合为两天，实际 {items}'
    # 倒序：06-08 在前
    assert items[0]['date'] == '2026-06-08' and items[1]['date'] == '2026-06-07'
    d8, d7 = items[0], items[1]
    # 06-07：入账 +1000 / 消耗 -30 / 净 970 / 2 笔；usage 仅 1 笔 → 请求 1 次 / token 1500（1200+300）
    assert Decimal(str(d7['granted'])) == Decimal('1000')
    assert Decimal(str(d7['consumed'])) == Decimal('-30')
    assert Decimal(str(d7['net'])) == Decimal('970')
    assert d7['count'] == 2
    assert d7['request_count'] == 1 and d7['token_count'] == 1500
    # 06-08：仅消耗 -12 / 净 -12 / 1 笔 → 请求 1 次 / token 500（400+100）
    assert Decimal(str(d8['consumed'])) == Decimal('-12')
    assert Decimal(str(d8['net'])) == Decimal('-12')
    assert d8['count'] == 1
    assert d8['request_count'] == 1 and d8['token_count'] == 500


async def test_transactions_daily_pagination(env) -> None:
    """按日聚合分页：3 天 size=2 → 第一页两天（倒序）、第二页一天。"""
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    utc = dt_tz.utc
    for day in (5, 6, 7):
        # 03:00Z = 11:00+08，稳落当地同一天
        s.add(_txn(uid, ttype='usage', credits=Decimal('-10'), before=Decimal('100'), after=Decimal('90'),
                   ref_type='llm_usage', desc=f'd{day}', created=datetime(2026, 6, day, 3, 0, tzinfo=utc)))
    await s.flush()

    p1 = _data(await c.get(_DAILY, params={'page': 1, 'size': 2}))
    assert p1['total'] == 3 and p1['total_pages'] == 2 and len(p1['items']) == 2
    assert [it['date'] for it in p1['items']] == ['2026-06-07', '2026-06-06']
    p2 = _data(await c.get(_DAILY, params={'page': 2, 'size': 2}))
    assert len(p2['items']) == 1 and p2['items'][0]['date'] == '2026-06-05'


async def test_transactions_daily_empty_returns_envelope(env) -> None:
    """无流水也返回统一信封 + 空 items（零 fake）。"""
    data = _data(await env.client.get(_DAILY, params={'page': 1, 'size': 20}))
    assert data['items'] == [] and data['total'] == 0


def test_create_pay_order_param_cross_field_validation() -> None:
    """B0：可辨识联合跨字段校验。"""
    import pydantic

    from backend.app.pay.schema.pay_order import CreatePayOrderParam

    # 合法
    assert CreatePayOrderParam(order_type='subscribe', tier='pro', channel_code='wx_native').tier == 'pro'
    assert CreatePayOrderParam(order_type='credit_pack', package_id=10, channel_code='alipay_qr').package_id == 10
    # subscribe 缺 tier → 422
    with pytest.raises(pydantic.ValidationError):
        CreatePayOrderParam(order_type='subscribe', channel_code='wx_native')
    # credit_pack 缺 package_id → 422
    with pytest.raises(pydantic.ValidationError):
        CreatePayOrderParam(order_type='credit_pack', channel_code='wx_native')
    # 非法 order_type → 422
    with pytest.raises(pydantic.ValidationError):
        CreatePayOrderParam(order_type='bogus', channel_code='wx_native')


def test_build_client_routes_alipay_qr() -> None:
    """B0'：渠道工厂把 alipay_qr 路由到 AlipayQrClient（当面付），alipay_pc → AlipayPcClient。"""
    from backend.app.pay.service.channel.alipay_pc import AlipayPcClient
    from backend.app.pay.service.channel.alipay_qr import AlipayQrClient
    from backend.app.pay.service.pay_order_service import _build_client

    cfg = {'appId': 'x', 'serverUrl': 'https://openapi-sandbox.dl.alipaydev.com/gateway.do', 'privateKey': 'k', 'alipayPublicKey': 'p'}
    qr_channel = SimpleNamespace(code='alipay_qr', id=999, config=cfg, extra_config=None)
    pc_channel = SimpleNamespace(code='alipay_pc', id=998, config=cfg, extra_config=None)
    assert isinstance(_build_client(qr_channel, cfg), AlipayQrClient)
    pc = _build_client(pc_channel, cfg)
    assert isinstance(pc, AlipayPcClient) and not isinstance(pc, AlipayQrClient)
