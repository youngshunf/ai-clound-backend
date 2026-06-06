"""P1 云端跨设备渠道脱敏摘要镜像 真实 HTTP E2E（真实 PostgreSQL，零 mock）。

模块级把 app 渠道镜像路由挂最小 app，fixture 用 dependency_overrides 把 DependsJwtAuth 换成
注入固定 user_id、get_db/get_db_transaction 指向真实 PG 会话。覆盖：
- upsert 新建 → GET 列表能看到（含脱敏字段）
- 三道隔离：另一 user 的 JWT 看不到本 owner 的行
- 第④层脱敏：upsert 带 app_secret/client_secret/token/xxx_secret 的 metadata 写库后被剔除
- 脱敏常量一致性：is_secret_key 对 Hermes SECRET_KEY_FRAGMENTS 全集为 True
- ON CONFLICT 时间择新：旧 updated_time 不覆盖新值
- 统一信封 {code:200,...}
- 真实 38 字符 hasn_id 测列宽（不绕过截断 bug）

设计事实源: docs/hasn-node设计文档/桌面端第三方IM渠道接入/01-桌面端第三方IM渠道接入总体设计.md §6 / §8.5 / §3.3。
"""
from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app.hasn_agent_channel_mirrors import router as app_mirrors_router
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.secret_keys import is_secret_key
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

# Hermes huanxing_hermes_runtime/security.py::SECRET_KEY_FRAGMENTS 全集（设计 §3.3 已核对 7 项）。
_HERMES_SECRET_KEY_FRAGMENTS = (
    'api_key',
    'apikey',
    'secret',
    'token',
    'password',
    'credential',
    'client_secret',
)

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_mirrors_router, prefix='/api/v1/hasn/app/agent-channel-mirrors')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _hasn40(prefix: str) -> str:
    """构造真实 38 字符 hasn_id（不用短 id 绕过 varchar(40) 截断 bug，记忆 hasn_id_column_width）。

    `<prefix>_` 前缀 + 用 32 位 hex 填满到正好 38 字符（接近 hasn_id 实际长度），
    既验证 varchar(40) 容纳、又不绕过截断 bug。
    """
    head = f'{prefix}_'
    fill = (uuid.uuid4().hex + uuid.uuid4().hex)[: 38 - len(head)]
    return f'{head}{fill}'


class _Ctx(SimpleNamespace):
    pass


@pytest_asyncio.fixture
async def env():
    """两个真实 owner（owner_a / owner_b，不同 user_id）+ 可切换注入 user_id 的 ASGI 客户端。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    user_a = 970000 + int(uuid.uuid4().int % 20000)
    user_b = user_a + 1
    owner_a = _hasn40('h')
    owner_b = _hasn40('h')
    sa_uid = _uid()
    session.add_all(
        [
            HasnHumans(hasn_id=owner_a, star_id=f'sa{sa_uid}', user_id=user_a, nickname='渠道E2E_A', status='active'),
            HasnHumans(hasn_id=owner_b, star_id=f'sb{sa_uid}', user_id=user_b, nickname='渠道E2E_B', status='active'),
        ]
    )
    await session.flush()

    async def _yield_session():
        yield session

    # 当前注入哪个 user_id（测试用例切换以模拟 owner_a / owner_b 视角）。
    current = {'user_id': user_a}

    async def _auth_inject(request: Request):
        request.scope['user'] = SimpleNamespace(id=current['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')

    def _as(user_id: int) -> None:
        current['user_id'] = user_id

    try:
        yield _Ctx(
            client=client,
            session=session,
            user_a=user_a,
            user_b=user_b,
            owner_a=owner_a,
            owner_b=owner_b,
            as_user=_as,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _envelope(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


def _upsert_payload(**overrides) -> dict:
    payload = {
        'agent_hasn_id': _hasn40('a'),
        'channel': 'feishu',
        'origin_node_id': _hasn40('n'),
        'runtime_location': 'local',
        'status': 'bound',
        'bound_account_display': '运营小助手@feishu.cn',
        'metadata_json': {'app_id': 'cli_xxx', 'domain': 'feishu.cn'},
        'last_error': None,
    }
    payload.update(overrides)
    return payload


async def test_upsert_then_list_visible_with_redacted_fields(env) -> None:
    """upsert 新建 → GET 列表能看到该行（含脱敏 bound_account_display + 状态）。"""
    c = env.client
    env.as_user(env.user_a)
    p = _upsert_payload()

    created = _envelope(await c.put('/api/v1/hasn/app/agent-channel-mirrors/upsert', json=p))
    assert created['owner_id'] == env.owner_a, 'owner_id 必须由 JWT 解析覆盖（非 body）'
    assert created['mirror_id'].startswith('chm_'), 'mirror_id 应为 chm_ 文本主键'
    assert created['channel'] == 'feishu'
    assert created['status'] == 'bound'
    assert created['bound_account_display'] == '运营小助手@feishu.cn'

    items = _envelope(await c.get('/api/v1/hasn/app/agent-channel-mirrors'))['items']
    row = next((r for r in items if r['agent_hasn_id'] == p['agent_hasn_id']), None)
    assert row is not None, 'upsert 后列表应能看到该行'
    assert row['origin_node_id'] == p['origin_node_id']
    assert row['metadata_json'] == {'app_id': 'cli_xxx', 'domain': 'feishu.cn'}, '非秘密字段应保留'


async def test_owner_isolation_cross_user_cannot_see(env) -> None:
    """三道隔离：owner_a upsert 的行，owner_b 的 JWT 在列表里看不到。"""
    c = env.client
    env.as_user(env.user_a)
    p = _upsert_payload()
    _envelope(await c.put('/api/v1/hasn/app/agent-channel-mirrors/upsert', json=p))

    # 切到 owner_b 视角
    env.as_user(env.user_b)
    items_b = _envelope(await c.get('/api/v1/hasn/app/agent-channel-mirrors'))['items']
    assert all(r['agent_hasn_id'] != p['agent_hasn_id'] for r in items_b), 'owner_b 不应看到 owner_a 的镜像行'
    assert all(r['owner_id'] == env.owner_b for r in items_b), 'owner_b 列表只应含自己的 owner_id'


async def test_fourth_layer_redaction_strips_secrets(env) -> None:
    """第④层脱敏：metadata 带 app_secret/client_secret/token/xxx_secret 写库后被剔除，非秘密保留。"""
    c = env.client
    env.as_user(env.user_a)
    p = _upsert_payload(
        metadata_json={
            'app_id': 'cli_keep',
            'app_secret': 'SHOULD_BE_STRIPPED',
            'client_secret': 'SHOULD_BE_STRIPPED',
            'token': 'SHOULD_BE_STRIPPED',
            'weixin_secret': 'SHOULD_BE_STRIPPED',
            'refresh_token': 'SHOULD_BE_STRIPPED',
            'nested': {'inner_secret': 'SHOULD_BE_STRIPPED', 'inner_keep': 'ok'},
        }
    )
    created = _envelope(await c.put('/api/v1/hasn/app/agent-channel-mirrors/upsert', json=p))

    meta = created['metadata_json']
    for forbidden in ('app_secret', 'client_secret', 'token', 'weixin_secret', 'refresh_token'):
        assert forbidden not in meta, f'秘密键 {forbidden} 应被第④层脱敏剔除'
    assert meta.get('app_id') == 'cli_keep', '非秘密字段应保留'
    assert meta['nested'] == {'inner_keep': 'ok'}, '嵌套秘密键也应被递归剔除'

    # 直接查 DB 双重确认库内无明文秘密（不靠响应自证）。
    raw = (
        await env.session.execute(
            text(
                'SELECT metadata_json FROM hasn_agent_channel_mirrors '
                'WHERE mirror_id = :mid'
            ),
            {'mid': created['mirror_id']},
        )
    ).scalar_one()
    db_meta = raw if isinstance(raw, dict) else __import__('json').loads(raw)
    assert 'SHOULD_BE_STRIPPED' not in str(db_meta), '库内 metadata 不得含任何秘密明文'
    assert 'app_secret' not in db_meta and 'token' not in db_meta


def test_redaction_constant_consistency_with_hermes() -> None:
    """脱敏常量一致性：is_secret_key 对 Hermes SECRET_KEY_FRAGMENTS 全集判 True（§0.6）。"""
    for frag in _HERMES_SECRET_KEY_FRAGMENTS:
        assert is_secret_key(frag) is True, f'Hermes 片段 {frag} 应被 is_secret_key 判为秘密'
    # 后缀规则
    assert is_secret_key('app_secret') is True
    assert is_secret_key('refresh_token') is True
    # 非秘密键应判 False
    for keep in ('app_id', 'domain', 'channel', 'account_id', 'open_id'):
        assert is_secret_key(keep) is False, f'非秘密键 {keep} 不应被判为秘密'


async def test_on_conflict_keeps_newest_updated_time(env) -> None:
    """ON CONFLICT 时间择新：先 upsert 较新 updated_time，再 upsert 较旧的 → 不覆盖新值。

    同一唯一键 (owner, agent, channel, origin_node) 复用同一行。两次 upsert 之间在 DB 把
    updated_time 人为推到未来，模拟「库内已有更新的上报」，再发一次（其 updated_time=now() 更旧）
    → WHERE 旧<新 不命中，status/display 不应被覆盖。
    """
    c = env.client
    env.as_user(env.user_a)
    agent = _hasn40('a')
    node = _hasn40('n')
    first = _upsert_payload(
        agent_hasn_id=agent, origin_node_id=node, status='bound', bound_account_display='新值@feishu.cn'
    )
    created = _envelope(await c.put('/api/v1/hasn/app/agent-channel-mirrors/upsert', json=first))
    mid = created['mirror_id']

    # 把库内该行 updated_time 推到未来（模拟更新的上报已落库），并改成可识别的新值。
    await env.session.execute(
        text(
            "UPDATE hasn_agent_channel_mirrors "
            "SET updated_time = now() + interval '1 hour', status = 'expired', "
            "    bound_account_display = '更新后的新值' WHERE mirror_id = :mid"
        ),
        {'mid': mid},
    )
    await env.session.flush()

    # 再 upsert（updated_time=now() 比库内未来值旧）→ 不应覆盖。
    stale = _upsert_payload(
        agent_hasn_id=agent, origin_node_id=node, status='failed', bound_account_display='陈旧值不该覆盖'
    )
    res = _envelope(await c.put('/api/v1/hasn/app/agent-channel-mirrors/upsert', json=stale))
    assert res['status'] == 'expired', 'WHERE 旧<新 未命中，status 应保持库内更新的值'
    assert res['bound_account_display'] == '更新后的新值', '旧上报不得覆盖库内更新值（时间择新）'

    # 同一唯一键仍只一行（upsert 未新增行）。
    cnt = (
        await env.session.execute(
            text(
                'SELECT count(*) FROM hasn_agent_channel_mirrors '
                'WHERE owner_id=:o AND agent_hasn_id=:a AND channel=:c AND origin_node_id=:n'
            ),
            {'o': env.owner_a, 'a': agent, 'c': 'feishu', 'n': node},
        )
    ).scalar_one()
    assert cnt == 1, '唯一键应保证同 (owner,agent,channel,node) 只一行'


async def test_envelope_shape_is_unified(env) -> None:
    """统一信封：两端点返回 {code:200, msg, data}。"""
    c = env.client
    env.as_user(env.user_a)
    r1 = await c.get('/api/v1/hasn/app/agent-channel-mirrors')
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1.get('code') == 200 and 'data' in b1 and 'items' in b1['data']

    r2 = await c.put('/api/v1/hasn/app/agent-channel-mirrors/upsert', json=_upsert_payload())
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2.get('code') == 200 and isinstance(b2.get('data'), dict)


async def test_long_hasn_id_column_width(env) -> None:
    """真实 38 字符 hasn_id 入库不截断（varchar(40) 容纳；不用短 id 绕过 bug）。"""
    c = env.client
    env.as_user(env.user_a)
    agent = _hasn40('a')
    node = _hasn40('n')
    assert len(agent) == 38 and len(node) == 38, '测试用真实 38 字符 id'
    created = _envelope(
        await c.put(
            '/api/v1/hasn/app/agent-channel-mirrors/upsert',
            json=_upsert_payload(agent_hasn_id=agent, origin_node_id=node),
        )
    )
    assert created['agent_hasn_id'] == agent, '38 字符 agent_hasn_id 应完整入库'
    assert created['origin_node_id'] == node, '38 字符 origin_node_id 应完整入库'
    assert len(created['owner_id']) == 38, 'owner hasn_id 也为 38 字符且完整'
