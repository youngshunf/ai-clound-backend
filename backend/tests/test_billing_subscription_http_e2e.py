"""桌面端订阅与流水 · 云端真实 HTTP E2E（真实 PostgreSQL，零 mock 于数据层）。

**doc94 D1 之后这套用例换了断言对象**：云端不再有 `credit_transaction` 流水表，
消费流水与日聚合的唯一来源是 NewAPI。所以这里锁的不再是「云端账本记对了没」，
而是**云端有没有老老实实转述 NewAPI**：

- `GET .../transactions`：金额字符串原样透传，云端不做任何换算；
- NewAPI 读不到时 `usage_status='unavailable'` 且列表为空——**不能**用空列表
  把「读不到」伪装成「这段时间没花钱」；
- `GET .../transactions/daily`：消费取 NewAPI、入账取云端履约事件，按本地日合并、倒序、分页；
- `GET .../info`：状态按订阅结束日重算（修复「过期却显示生效中」）。

NewAPI 内部通道用一个**桩客户端**替身（只替网络边界，不 mock 业务逻辑）：真实 NewAPI
不可能在单测里造出指定日期的历史日志，那属于 staging 真值验证。数据库、路由、信封、
分页全部走真实实现。

另外保留两个纯构造用例（下单参数跨字段校验、支付渠道工厂路由），它们与积分权威无关。
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta
from datetime import timezone as dt_tz
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

from backend.app.billing.api.v1.app.subscription import router as app_subscription_router
from backend.app.billing.model import UserSubscription
from backend.app.newapi.credit_client import NewApiCreditError
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
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
_INFO = '/api/v1/user_tier/app/subscription/info'


def _new_user_id() -> int:
    return 960_000_000 + int(uuid.uuid4().int % 20_000_000)


class _StubCreditClient:
    """NewAPI 内部通道的网络边界替身。

    只替 HTTP 边界：路由、信封、分页、合并、时区口径全部走真实实现。
    `fail=True` 时抛可重试错误，用来验证「读不到就说读不到」。
    """

    def __init__(self) -> None:
        self.usage_page: dict = {'items': [], 'total': 0, 'page': 1, 'size': 20, 'measured_at': None}
        self.usage_daily: dict = {'items': [], 'measured_at': None}
        self.fail = False

    async def get_usage_page(self, newapi_user_id: int, **kwargs) -> dict:
        if self.fail:
            raise NewApiCreditError('模拟不可达', code='newapi_credit_unreachable', retryable=True)
        return self.usage_page

    async def get_usage_daily(self, newapi_user_id: int, **kwargs) -> dict:
        if self.fail:
            raise NewApiCreditError('模拟不可达', code='newapi_credit_unreachable', retryable=True)
        return self.usage_daily

    async def get_credit_account(self, newapi_user_id: int) -> dict:
        raise NewApiCreditError('本用例不覆盖账户读', code='newapi_credit_unreachable', retryable=True)


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    auth_state = {'user_id': _new_user_id()}
    stub = _StubCreditClient()

    import backend.app.billing.service.credit_account_service as account_module
    import backend.app.billing.service.credit_usage_service as usage_module

    monkeypatch.setattr(usage_module, 'newapi_credit_client', stub)
    monkeypatch.setattr(account_module, 'newapi_credit_client', stub)

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'], is_superuser=False)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, auth_state=auth_state, stub=stub)
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


async def _seed_mapping(session, user_id: int) -> None:
    session.add(
        LlmNewapiUserMapping(
            huanxing_user_id=user_id,
            newapi_user_id=user_id,
            newapi_token_key=f'e2e{user_id}',
            newapi_token_id=0,
            app_code='huanxing',
            status='active',
        )
    )
    await session.flush()


async def test_transactions_passes_through_newapi_amounts(env) -> None:
    """流水金额原样透传：云端不做任何单位换算，也不改字段口径。"""
    await _seed_mapping(env.session, env.auth_state['user_id'])
    env.stub.usage_page = {
        'items': [
            {
                'id': 2,
                'created_at': 1_784_997_000,
                'model_name': 'gpt-x',
                'token_name': 'default',
                'credits': '0.5',
                'prompt_tokens': 10,
                'completion_tokens': 20,
                'use_time': 3,
                'is_stream': False,
                'funding_source': 'subscription',
            }
        ],
        'total': 1,
        'page': 1,
        'size': 20,
        'measured_at': '2026-07-25T10:00:00Z',
    }

    data = _data(await env.client.get(_TXNS, params={'page': 1, 'size': 20}))

    assert data['usage_status'] == 'ok'
    assert data['total'] == 1
    # 字符串原样：不能变成 0.5 浮点，也不能被二次量化成 '0.50'
    assert data['items'][0]['credits'] == '0.5'
    assert data['items'][0]['funding_source'] == 'subscription'
    assert data['measured_at'] == '2026-07-25T10:00:00Z'


async def test_transactions_unavailable_is_not_disguised_as_no_usage(env) -> None:
    """NewAPI 读不到 → usage_status=unavailable；绝不用空列表伪装成「没有消费」。"""
    await _seed_mapping(env.session, env.auth_state['user_id'])
    env.stub.fail = True

    data = _data(await env.client.get(_TXNS))

    assert data['usage_status'] == 'unavailable'
    assert data['items'] == []
    assert data['unavailable_reason']


async def test_transactions_unmapped_user_is_distinguished(env) -> None:
    """尚未开通 NewAPI 账户是「没有账户」，与「没有消费」必须能区分。"""
    data = _data(await env.client.get(_TXNS))

    assert data['usage_status'] == 'unmapped'
    assert data['items'] == []


async def test_daily_merges_newapi_consumption_desc_and_paginates(env) -> None:
    """日聚合：消费取 NewAPI，按日倒序，在合并结果上分页（日边界不被切断）。"""
    await _seed_mapping(env.session, env.auth_state['user_id'])
    env.stub.usage_daily = {
        'items': [
            {'day': '2026-07-25', 'consumed_credits': '3', 'request_count': 3, 'token_count': 300},
            {'day': '2026-07-24', 'consumed_credits': '2', 'request_count': 2, 'token_count': 200},
            {'day': '2026-07-23', 'consumed_credits': '1', 'request_count': 1, 'token_count': 100},
        ],
        'measured_at': '2026-07-25T10:00:00Z',
    }

    first = _data(await env.client.get(_DAILY, params={'page': 1, 'size': 2}))
    second = _data(await env.client.get(_DAILY, params={'page': 2, 'size': 2}))

    assert first['total'] == 3
    assert [item['date'] for item in first['items']] == ['2026-07-25', '2026-07-24']
    assert [item['date'] for item in second['items']] == ['2026-07-23']
    # 展示口径：消耗为负数
    assert Decimal(str(first['items'][0]['consumed'])) == Decimal('-3')
    assert first['items'][0]['request_count'] == 3
    assert first['usage_status'] == 'ok'
    assert first['measured_at'] == '2026-07-25T10:00:00Z'


async def test_daily_unavailable_is_reported_not_swallowed(env) -> None:
    """日聚合读不到时同样如实标注，而不是回一串「当天消耗 0」。"""
    await _seed_mapping(env.session, env.auth_state['user_id'])
    env.stub.fail = True

    data = _data(await env.client.get(_DAILY))

    assert data['usage_status'] == 'unavailable'
    assert data['items'] == []


async def test_daily_empty_returns_envelope(env) -> None:
    """无数据也必须回统一信封与稳定形状，不能空 body。"""
    await _seed_mapping(env.session, env.auth_state['user_id'])

    data = _data(await env.client.get(_DAILY))

    assert data['items'] == []
    assert data['total'] == 0
    assert data['total_pages'] == 0


async def test_info_status_expired_recomputed(env) -> None:
    """/info 状态按订阅结束日重算：付费且已过期 → expired（修复「过期却显示生效中」）。

    该用户没有 NewAPI 映射，余额字段为 None，但**不影响状态判定**——
    状态来自合同，余额来自权威快照，两者互不兜底。
    """
    session, client, uid = env.session, env.client, env.auth_state['user_id']
    now = datetime.now(dt_tz.utc)
    session.add(
        UserSubscription(
            app_code='huanxing',
            user_id=uid,
            tier='pro',
            subscription_type='monthly',
            monthly_credits=Decimal(1000),
            current_credits=Decimal(1000),
            used_credits=Decimal(0),
            purchased_credits=Decimal(0),
            billing_cycle_start=now - timedelta(days=95),
            billing_cycle_end=now - timedelta(days=65),
            subscription_start_date=now - timedelta(days=95),
            subscription_end_date=now - timedelta(days=65),
            next_grant_date=None,
            status='active',
            auto_renew=False,
            max_agents=3,
        )
    )
    await session.flush()

    data = _data(await client.get(_INFO))

    assert data['status'] == 'expired', f"已过期付费订阅应判 expired，实际 {data['status']}"
    assert data['tier'] == 'pro'
    # 拿不到权威余额时如实为 None，绝不回落云端旧值，也不伪造 0
    assert data['current_credits'] is None
    assert data['credit_status'] == 'unmapped'


def test_create_pay_order_param_cross_field_validation() -> None:
    """下单参数可辨识联合跨字段校验。"""
    import pydantic

    from backend.app.billing.schema.pay_order import CreatePayOrderParam

    assert CreatePayOrderParam(order_type='subscribe', tier='pro', channel_code='wx_native').tier == 'pro'
    assert CreatePayOrderParam(order_type='credit_pack', package_id=10, channel_code='alipay_qr').package_id == 10
    with pytest.raises(pydantic.ValidationError):
        CreatePayOrderParam(order_type='subscribe', channel_code='wx_native')
    with pytest.raises(pydantic.ValidationError):
        CreatePayOrderParam(order_type='credit_pack', channel_code='wx_native')
    with pytest.raises(pydantic.ValidationError):
        CreatePayOrderParam(order_type='bogus', channel_code='wx_native')


def test_build_client_routes_alipay_qr() -> None:
    """渠道工厂把 alipay_qr 路由到 AlipayQrClient（当面付），alipay_pc → AlipayPcClient。"""
    from backend.app.billing.service.channel.alipay_pc import AlipayPcClient
    from backend.app.billing.service.channel.alipay_qr import AlipayQrClient
    from backend.app.billing.service.pay_order_service import _build_client

    cfg = {
        'appId': 'x',
        'serverUrl': 'https://openapi-sandbox.dl.alipaydev.com/gateway.do',
        'privateKey': 'k',
        'alipayPublicKey': 'p',
    }
    qr_channel = SimpleNamespace(code='alipay_qr', id=999, config=cfg, extra_config=None)
    pc_channel = SimpleNamespace(code='alipay_pc', id=998, config=cfg, extra_config=None)
    assert isinstance(_build_client(qr_channel, cfg), AlipayQrClient)
    pc = _build_client(pc_channel, cfg)
    assert isinstance(pc, AlipayPcClient) and not isinstance(pc, AlipayQrClient)
