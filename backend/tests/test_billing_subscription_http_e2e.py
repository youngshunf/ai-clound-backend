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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.llm.crud.crud_llm_newapi_user_mapping import newapi_direct_dao
from backend.app.llm.model import LlmNewapiUserMapping
from backend.app.billing.api.v1.app.subscription import router as app_subscription_router
from backend.app.billing.model import CreditTransaction, UserSubscription
from backend.app.billing.service.billing_usage_service import quota_to_credits
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.core.conf import settings
from backend.database.db import NEWAPI_DATABASE_URL, SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction
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


async def test_transactions_daily_grants_internal_usage_not_counted(env) -> None:
    """按日合并新口径：入账取内部账本；内部 usage 行**不再**计为消耗（消耗权威在 new-api）。

    本测试用户无 new-api 映射 → 消耗/请求/token 全 0；验证入账侧 + 本地日归并 + 隔离，
    且内部历史 usage 行不被当作消耗。new-api 真实消耗的合并见
    `test_newapi_authoritative_info_and_daily`（按 new-api 可达性 gated）。
    """
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    utc = dt_tz.utc
    # 06-07 本地：入账 +1000（purchase）+ 一条内部 usage（旧账本遗留，应被忽略）
    s.add(_txn(uid, ttype='purchase', credits=Decimal('1000'), before=Decimal('0'), after=Decimal('1000'),
               ref_type='pay_order', desc='充值', created=datetime(2026, 6, 7, 12, 0, tzinfo=utc)))
    s.add(_txn(uid, ttype='usage', credits=Decimal('-30'), before=Decimal('1000'), after=Decimal('970'),
               ref_type='llm_usage', desc='内部usage(应忽略)', created=datetime(2026, 6, 7, 13, 0, tzinfo=utc),
               extra={'input_tokens': 1200, 'output_tokens': 300}))
    # 隔离：别的用户 + 别 app 不计入
    s.add(_txn(_new_user_id(), ttype='purchase', credits=Decimal('999'), before=Decimal('0'), after=Decimal('999'),
               ref_type='pay_order', desc='别人', created=datetime(2026, 6, 7, 12, 0, tzinfo=utc)))
    s.add(_txn(uid, ttype='purchase', credits=Decimal('888'), before=Decimal('0'), after=Decimal('888'),
               ref_type='pay_order', desc='别app', created=datetime(2026, 6, 7, 12, 0, tzinfo=utc), app_code='zhixiaoya'))
    await s.flush()

    data = _data(await c.get(_DAILY, params={'page': 1, 'size': 20}))
    items = data['items']
    assert data['total'] == 1 and len(items) == 1, f'仅 06-07 一天（含我的 huanxing 入账），实际 {items}'
    d7 = items[0]
    assert d7['date'] == '2026-06-07'
    assert Decimal(str(d7['granted'])) == Decimal('1000')
    # 内部 usage 不再计为消耗（消耗权威在 new-api，本用户无映射）
    assert Decimal(str(d7['consumed'])) == Decimal('0'), '内部 usage 不应再计为消耗'
    assert Decimal(str(d7['net'])) == Decimal('1000')
    assert d7['request_count'] == 0 and d7['token_count'] == 0, '消耗/请求/token 权威在 new-api'
    # count 仍含当日全部内部流水（purchase + usage = 2 笔）
    assert d7['count'] == 2


async def test_info_status_expired_recomputed(env) -> None:
    """/info 状态按订阅结束日重算：付费且已过期 → expired（修复「过期却显示生效中」）。

    只依赖唤星 PG；new-api 不可达时 get_available_credits 优雅降级回退内部，不影响状态判定。
    """
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    now = datetime.now(dt_tz.utc)
    s.add(UserSubscription(
        app_code='huanxing', user_id=uid, tier='pro', subscription_type='monthly',
        monthly_credits=Decimal('1000'), current_credits=Decimal('1000'),
        used_credits=Decimal('0'), purchased_credits=Decimal('0'),
        billing_cycle_start=now - timedelta(days=95), billing_cycle_end=now - timedelta(days=65),
        subscription_start_date=now - timedelta(days=95), subscription_end_date=now - timedelta(days=65),
        next_grant_date=None, status='active', auto_renew=False, max_agents=3,
    ))
    await s.flush()

    data = _data(await c.get('/api/v1/user_tier/app/subscription/info'))
    assert data['status'] == 'expired', f"已过期付费订阅应判 expired，实际 {data['status']}"
    assert data['tier'] == 'pro'
    # cycle_consumed_credits 字段存在（无 new-api 映射 → 0），不再用「额度−剩余」假象
    assert Decimal(str(data['cycle_consumed_credits'])) == Decimal('0')


async def test_newapi_authoritative_info_and_daily(env) -> None:
    """**真实联调**：new-api 为权威源 —— 可用积分=(quota−used)/RATE、本期消耗/流水取自 logs。

    seed 真实 new-api `users`(quota/used_quota) + `logs`(type=2 跨两本地日) + 唤星映射/订阅，
    断言 /info current_credits/cycle_consumed_credits 与 /daily 合并消耗。
    new-api 库不可达或 logs 列缺失 → skip（infra-gated，CI/staging 跑真值）。
    """
    s, c, uid = env.session, env.client, env.auth_state['user_id']
    rate = settings.NEWAPI_CREDITS_TO_QUOTA_RATE

    newapi_engine = create_async_engine(NEWAPI_DATABASE_URL, poolclass=NullPool)
    try:
        async with newapi_engine.connect() as probe:
            await probe.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await newapi_engine.dispose()
        pytest.skip(f'new-api 库不可达，跳过真实联调: {exc!r}')

    newapi_session = async_sessionmaker(newapi_engine, expire_on_commit=False)()
    newapi_user_id = None
    try:
        # 1) new-api 用户：总额度 20000 积分、已用 1000 积分（quota 单位）
        try:
            newapi_user_id = await newapi_direct_dao.create_newapi_user(
                newapi_session, username=f'hx_e2e_{uid}', display_name='e2e', quota=20_000 * rate,
            )
            await newapi_session.execute(
                text('UPDATE users SET used_quota = :u WHERE id = :id'),
                {'u': 1_000 * rate, 'id': newapi_user_id},
            )
            # 2) 两本地日的消耗日志（type=2）：06-07 用 600 积分 / 06-08 用 400 积分
            for created_at, q, pt, ct in [
                (int(datetime(2026, 6, 7, 5, 0, tzinfo=dt_tz.utc).timestamp()), 600 * rate, 1200, 300),
                (int(datetime(2026, 6, 7, 20, 0, tzinfo=dt_tz.utc).timestamp()), 400 * rate, 400, 100),
            ]:
                await newapi_session.execute(
                    text(
                        'INSERT INTO logs (user_id, created_at, type, quota, prompt_tokens, completion_tokens, '
                        'model_name, content, username, token_name) '
                        "VALUES (:uid, :ts, 2, :q, :pt, :ct, 'qwen3-max', '', '', '')"
                    ),
                    {'uid': newapi_user_id, 'ts': created_at, 'q': q, 'pt': pt, 'ct': ct},
                )
            await newapi_session.commit()
        except Exception as exc:  # noqa: BLE001
            await newapi_session.rollback()
            pytest.skip(f'new-api 表结构与本测试 seed 不符（schema-gated）: {exc!r}')

        # 3) 唤星侧：映射 + 付费订阅（周期覆盖两条日志）
        s.add(LlmNewapiUserMapping(
            huanxing_user_id=uid, newapi_user_id=newapi_user_id,
            newapi_token_key='e2e', newapi_token_id=0, app_code='huanxing', status='active',
        ))
        now = datetime.now(dt_tz.utc)
        s.add(UserSubscription(
            app_code='huanxing', user_id=uid, tier='flagship', subscription_type='monthly',
            monthly_credits=Decimal('20000'), current_credits=Decimal('0'),
            used_credits=Decimal('0'), purchased_credits=Decimal('0'),
            billing_cycle_start=datetime(2026, 6, 1, tzinfo=dt_tz.utc), billing_cycle_end=now + timedelta(days=10),
            subscription_start_date=datetime(2026, 6, 1, tzinfo=dt_tz.utc), subscription_end_date=now + timedelta(days=10),
            next_grant_date=None, status='active', auto_renew=False, max_agents=10,
        ))
        await s.flush()

        # /info：可用积分 = (20000 − 1000) = 19000；本期消耗 = 600+400 = 1000（quota_to_credits）
        info = _data(await c.get('/api/v1/user_tier/app/subscription/info'))
        assert Decimal(str(info['current_credits'])) == quota_to_credits(19_000 * rate)
        assert Decimal(str(info['cycle_consumed_credits'])) == quota_to_credits(1_000 * rate)
        assert info['status'] == 'active'

        # /daily：消耗按 new-api logs 归本地日 —— 06-07 -600 / 06-08 -400（跨 UTC 日界）
        daily = _data(await c.get(_DAILY, params={'page': 1, 'size': 20}))
        by_date = {it['date']: it for it in daily['items']}
        assert Decimal(str(by_date['2026-06-07']['consumed'])) == -quota_to_credits(600 * rate)
        assert by_date['2026-06-07']['request_count'] == 1 and by_date['2026-06-07']['token_count'] == 1500
        assert Decimal(str(by_date['2026-06-08']['consumed'])) == -quota_to_credits(400 * rate)
        assert by_date['2026-06-08']['request_count'] == 1 and by_date['2026-06-08']['token_count'] == 500
    finally:
        # 清理 new-api seed（避免污染共享库）
        try:
            if newapi_user_id is not None:
                import sqlalchemy as _sa
                await newapi_session.execute(_sa.text('DELETE FROM logs WHERE user_id = :id'), {'id': newapi_user_id})
                await newapi_session.execute(_sa.text('DELETE FROM users WHERE id = :id'), {'id': newapi_user_id})
                await newapi_session.commit()
        except Exception:  # noqa: BLE001
            await newapi_session.rollback()
        await newapi_session.close()
        await newapi_engine.dispose()


async def test_quota_to_credits_conversion() -> None:
    """quota → 积分换算（÷ NEWAPI_CREDITS_TO_QUOTA_RATE），与 credits_to_quota 互逆（纯函数，无 DB）。"""
    rate = settings.NEWAPI_CREDITS_TO_QUOTA_RATE
    assert quota_to_credits(rate) == Decimal('1.00')
    assert quota_to_credits(rate * 1000) == Decimal('1000.00')
    assert quota_to_credits(rate * 19000) == Decimal('19000.00')
    assert quota_to_credits(0) == Decimal('0.00')
    assert quota_to_credits(None) == Decimal('0.00')


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

    from backend.app.billing.schema.pay_order import CreatePayOrderParam

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
    from backend.app.billing.service.channel.alipay_pc import AlipayPcClient
    from backend.app.billing.service.channel.alipay_qr import AlipayQrClient
    from backend.app.billing.service.pay_order_service import _build_client

    cfg = {'appId': 'x', 'serverUrl': 'https://openapi-sandbox.dl.alipaydev.com/gateway.do', 'privateKey': 'k', 'alipayPublicKey': 'p'}
    qr_channel = SimpleNamespace(code='alipay_qr', id=999, config=cfg, extra_config=None)
    pc_channel = SimpleNamespace(code='alipay_pc', id=998, config=cfg, extra_config=None)
    assert isinstance(_build_client(qr_channel, cfg), AlipayQrClient)
    pc = _build_client(pc_channel, cfg)
    assert isinstance(pc, AlipayPcClient) and not isinstance(pc, AlipayQrClient)
