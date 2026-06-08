"""演示文稿（模块 17）云端 Agent scope API 进程内 HTTP E2E。

走真实 HTTP 栈（ASGITransport → 路由 → DependsAgentJwtAuth → service → 真实 PG），
覆盖：统一信封 {code,msg,data} + owner 隔离（分身 owner=agent.owner_hasn_id）+ scope 闸（deck:read/deck:write）
+ deck/page CRUD 往返。savepoint 事务隔离结束回滚不留痕（零 Mock 零 Fake：连真库不污染）。

鉴权用 dependency_override 注入构造的 AgentTokenPayload（隔离掉 JWT 签名/Redis 验证这类通用 infra，
聚焦验证 deck 端点自身的隔离/闸门/契约）；check_scopes 仍真实跑在被注入的 payload 上。
"""

from collections.abc import AsyncGenerator, Generator
from datetime import timedelta

import pytest
import pytest_asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.deck.api.router import agent as deck_agent_router
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import get_db, get_db_transaction, uuid4_str
from backend.utils.timezone import timezone

# 本地开发数据库（刻意不依赖 .env，避免 worktree 落到 5432）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'

_ALL_SCOPES = ['deck:read', 'deck:write']


def _payload(owner_id: str, scopes: list[str]) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=f'a_{uuid4_str()}',
        agent_name='测试分身',
        owner_hasn_id=owner_id,
        owner_user_id=1,
        scopes=scopes,
        session_uuid=uuid4_str(),
        expire_time=timezone.now() + timedelta(hours=1),
    )


@pytest_asyncio.fixture
async def harness() -> AsyncGenerator[tuple[AsyncClient, dict], None]:
    """返回 (client, holder)：holder['payload'] 决定当前请求的分身身份/scope；共享同一隔离会话。"""
    engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode='create_savepoint')

    holder: dict = {'payload': None}

    def _override_db() -> Generator[AsyncSession, None, None]:
        yield session

    def _override_agent() -> AgentTokenPayload | None:
        return holder['payload']

    app = FastAPI()
    # 异常处理器会读 starlette_context（trace_id 等），最小 app 需补上 ContextMiddleware，否则 403/404 路径 LookupError
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(app)
    app.include_router(deck_agent_router)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_db_transaction] = _override_db
    app.dependency_overrides[agent_jwt_auth] = _override_agent

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url='http://t')
    try:
        yield client, holder
    finally:
        await client.aclose()
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


_BASE = '/api/v1/deck/agent'


@pytest.mark.asyncio
async def test_envelope_and_crud_roundtrip(harness: tuple[AsyncClient, dict]) -> None:
    client, holder = harness
    holder['payload'] = _payload(f'h_{uuid4_str()}', _ALL_SCOPES)

    # 创建 → 统一信封 + data.id
    resp = await client.post(f'{_BASE}/decks', json={'title': '季度汇报', 'topic': 'Q2 复盘'})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {'code', 'msg', 'data'}  # 统一信封
    assert body['code'] == 200
    deck = body['data']
    assert deck['title'] == '季度汇报'
    assert deck['source'] == 'agent'  # 分身建的来源标记
    assert deck['status'] == 'draft'
    deck_id = deck['id']
    assert isinstance(deck_id, int) and deck_id > 0

    # 列表 → 1 条
    resp = await client.get(f'{_BASE}/decks')
    assert resp.json()['data']['total'] == 1

    # 详情
    resp = await client.get(f'{_BASE}/decks/{deck_id}')
    assert resp.json()['data']['title'] == '季度汇报'

    # 更新 → 标题改、rev 自增、owner_id 不被请求体污染
    resp = await client.put(f'{_BASE}/decks/{deck_id}', json={'title': '季度汇报 v2', 'status': 'ready'})
    updated = resp.json()['data']
    assert updated['title'] == '季度汇报 v2'
    assert updated['status'] == 'ready'
    assert updated['rev'] == deck['rev'] + 1

    # 新增两页 → page_count 推进
    r0 = await client.post(f'{_BASE}/decks/{deck_id}/pages', json={'position': 0, 'title': '封面', 'html': '<i>0</i>'})
    assert r0.status_code == 200
    r1 = await client.post(f'{_BASE}/decks/{deck_id}/pages', json={'position': 1, 'title': '正文', 'html': '<i>1</i>'})
    page1_id = r1.json()['data']['id']
    pages = await client.get(f'{_BASE}/decks/{deck_id}/pages')
    assert pages.json()['data']['total'] == 2

    # 更新页
    rp = await client.put(f'{_BASE}/pages/{page1_id}', json={'title': '正文改', 'status': 'edited'})
    assert rp.json()['data']['title'] == '正文改'

    # 软删一页 → 列表剩 1
    rd = await client.delete(f'{_BASE}/pages/{page1_id}')
    assert rd.status_code == 200
    pages = await client.get(f'{_BASE}/decks/{deck_id}/pages')
    assert pages.json()['data']['total'] == 1

    # 软删 deck → 列表清空
    rdd = await client.delete(f'{_BASE}/decks/{deck_id}')
    assert rdd.status_code == 200
    resp = await client.get(f'{_BASE}/decks')
    assert resp.json()['data']['total'] == 0


@pytest.mark.asyncio
async def test_owner_isolation(harness: tuple[AsyncClient, dict]) -> None:
    client, holder = harness
    owner_a = f'h_{uuid4_str()}'
    owner_b = f'h_{uuid4_str()}'

    # A 建一个 deck
    holder['payload'] = _payload(owner_a, _ALL_SCOPES)
    deck_id = (await client.post(f'{_BASE}/decks', json={'title': 'A 的稿'})).json()['data']['id']
    assert (await client.get(f'{_BASE}/decks')).json()['data']['total'] == 1

    # 切到 B（另一主人的分身）→ 看不到 A 的 deck
    holder['payload'] = _payload(owner_b, _ALL_SCOPES)
    assert (await client.get(f'{_BASE}/decks')).json()['data']['total'] == 0
    # 直取 A 的 deck → 404（不泄露存在性）
    assert (await client.get(f'{_BASE}/decks/{deck_id}')).status_code == 404
    # 跨户更新/删除也 404
    assert (await client.put(f'{_BASE}/decks/{deck_id}', json={'title': 'hack'})).status_code == 404
    assert (await client.delete(f'{_BASE}/decks/{deck_id}')).status_code == 404


@pytest.mark.asyncio
async def test_scope_gate(harness: tuple[AsyncClient, dict]) -> None:
    client, holder = harness
    owner = f'h_{uuid4_str()}'

    # 只读 scope → 写操作 403
    holder['payload'] = _payload(owner, ['deck:read'])
    assert (await client.post(f'{_BASE}/decks', json={'title': '应被拒'})).status_code == 403

    # 空 scope → 读操作也 403
    holder['payload'] = _payload(owner, [])
    assert (await client.get(f'{_BASE}/decks')).status_code == 403

    # 写 scope 齐 → 放行
    holder['payload'] = _payload(owner, _ALL_SCOPES)
    assert (await client.post(f'{_BASE}/decks', json={'title': '放行'})).status_code == 200
