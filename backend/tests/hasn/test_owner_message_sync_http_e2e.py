"""MEMCLOUD-A1：owner↔自有分身消息上行（messages:sync）进程内 HTTP E2E（真实 PG，零 mock）。

最小 app 挂真实 app 会话路由，dependency_overrides 注入 owner + 真实 PG 会话；经
ASGITransport 走完整 FastAPI HTTP 栈（依赖注入 + 统一信封），覆盖 service 测不到的
路由依赖/外壳漂移层。事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。

覆盖：
- outbound（主人→分身）落库 + 返回云端权威 id + 写 owner 同步事件（message.sent）。
- inbound（分身→主人）复用同一 loopback 会话（conversation_id 一致）。
- 幂等：同 local_id 重放 → deduped=True、同 message_id、不重复落库/不重复写事件。
- 安全：同步他人分身 → 403；不存在分身 → 404；缺 local_id / 非法 direction → 400。
"""
from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app.hasn_conversations import router as conversations_router
from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_sync_events import HasnSyncEvents
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(conversations_router, prefix='/api/v1/hasn/app/conversations')
register_exception(_APP)  # errors.* → HTTP 400/403/404 信封
# 同步事件写入路径会写 starlette_context.context（request id 等），需此中间件提供存储。
_APP.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=False)])

_SYNC_URL = '/api/v1/hasn/app/conversations/messages:sync'


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@pytest_asyncio.fixture
async def e2e():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    uid_owner = 970000 + int(uuid.uuid4().int % 9000)
    uid_stranger = uid_owner + 1
    owner = f'h_own_{_uid()}'
    stranger = f'h_str_{_uid()}'
    my_agent = f'a_mine_{_uid()}'
    others_agent = f'a_other_{_uid()}'
    session.add_all([
        HasnHumans(hasn_id=owner, star_id=f's_{uid_owner}', user_id=uid_owner, nickname='Owner', status='active'),
        HasnHumans(
            hasn_id=stranger, star_id=f's_{uid_stranger}', user_id=uid_stranger, nickname='Stranger', status='active'
        ),
        HasnAgents(
            hasn_id=my_agent, star_id=f'sa_{_uid()}', owner_id=owner,
            display_name='我的分身', agent_name='mine', status='active',
        ),
        HasnAgents(
            hasn_id=others_agent, star_id=f'sa_{_uid()}', owner_id=stranger,
            display_name='别人的分身', agent_name='other', status='active',
        ),
    ])
    await session.flush()

    current = {'uid': uid_owner, 'hasn_id': owner}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        # 真实运行时 JWT 携带 hasn_id；直接给定，避免 user_id（非唯一）回落库时与 dev 库真实
        # 人撞号导致解析漂移。
        request.scope['user'] = SimpleNamespace(id=current['uid'], hasn_id=current['hasn_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            current=current,
            owner=owner,
            stranger=stranger,
            my_agent=my_agent,
            others_agent=others_agent,
            uid_owner=uid_owner,
            uid_stranger=uid_stranger,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _count_messages_by_local_id(session, local_id: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.local_id == local_id)
        )
    ).scalar_one()


async def test_outbound_inbound_idempotent_and_security(e2e) -> None:
    c = e2e.client

    # 1) outbound（主人→分身）首次上行 → 落库 + 云端权威 id + 非去重
    out_local = f'lid_out_{_uid()}'
    r1 = await c.post(
        _SYNC_URL,
        json={
            'agent_hasn_id': e2e.my_agent,
            'direction': 'outbound',
            'content': {'text': '帮我把这周的复盘整理一下'},
            'local_id': out_local,
            'created_at': 1735660800,
        },
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1['code'] == 200, b1  # 统一信封
    msg_id = b1['data']['message_id']
    conv_id = b1['data']['conversation_id']
    assert msg_id and conv_id
    assert b1['data']['deduped'] is False

    # 落库核实：from=owner→to=agent，内容/时序保真
    await e2e.session.flush()
    row = (
        await e2e.session.execute(select(HasnMessages).where(HasnMessages.local_id == out_local))
    ).scalar_one()
    assert row.from_id == e2e.owner and row.to_id == e2e.my_agent
    assert row.content == {'text': '帮我把这周的复盘整理一下'}
    assert int(row.created_time.timestamp()) == 1735660800  # 客户端发送时序保真
    assert str(row.id) == msg_id

    # 会话原子去重：参与者排序后唯一
    conv = await e2e.session.get(HasnConversations, conv_id)
    assert conv is not None
    assert {conv.participant_a_id, conv.participant_b_id} == {e2e.owner, e2e.my_agent}

    # owner 同步事件（message.sent）已写，供其它设备 sync/pull 恢复
    sent_events = (
        await e2e.session.execute(
            select(HasnSyncEvents).where(
                HasnSyncEvents.owner_id == e2e.owner,
                HasnSyncEvents.event_type == 'message.sent',
                HasnSyncEvents.aggregate_id == msg_id,
            )
        )
    ).scalars().all()
    assert len(sent_events) == 1

    # 2) inbound（分身→主人）→ 复用同一 loopback 会话，方向相反
    in_local = f'lid_in_{_uid()}'
    r2 = await c.post(
        _SYNC_URL,
        json={
            'agent_hasn_id': e2e.my_agent,
            'direction': 'inbound',
            'content': {'text': '好的，已整理完毕'},
            'local_id': in_local,
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()['data']['conversation_id'] == conv_id  # 同一 loopback 会话
    assert r2.json()['data']['deduped'] is False
    await e2e.session.flush()
    in_row = (
        await e2e.session.execute(select(HasnMessages).where(HasnMessages.local_id == in_local))
    ).scalar_one()
    assert in_row.from_id == e2e.my_agent and in_row.to_id == e2e.owner

    # 3) 幂等：同 local_id 重放 → deduped=True、同 message_id、不重复落库
    r3 = await c.post(
        _SYNC_URL,
        json={
            'agent_hasn_id': e2e.my_agent,
            'direction': 'outbound',
            'content': {'text': '帮我把这周的复盘整理一下'},
            'local_id': out_local,
        },
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()['data']['deduped'] is True
    assert r3.json()['data']['message_id'] == msg_id
    await e2e.session.flush()
    assert await _count_messages_by_local_id(e2e.session, out_local) == 1  # 未重复落库

    # 4) 安全：同步他人分身 → 403
    r403 = await c.post(
        _SYNC_URL,
        json={
            'agent_hasn_id': e2e.others_agent,
            'direction': 'outbound',
            'content': {'text': 'x'},
            'local_id': f'lid_403_{_uid()}',
        },
    )
    assert r403.status_code == 403, r403.text

    # 5) 不存在分身 → 404
    r404 = await c.post(
        _SYNC_URL,
        json={
            'agent_hasn_id': f'a_ghost_{_uid()}',
            'direction': 'outbound',
            'content': {'text': 'x'},
            'local_id': f'lid_404_{_uid()}',
        },
    )
    assert r404.status_code == 404, r404.text

    # 6) 校验：缺 local_id → 422（pydantic 必填）；非法 direction → 400（业务校验）
    r_missing = await c.post(
        _SYNC_URL,
        json={'agent_hasn_id': e2e.my_agent, 'direction': 'outbound', 'content': {'text': 'x'}},
    )
    assert r_missing.status_code == 422, r_missing.text

    r_baddir = await c.post(
        _SYNC_URL,
        json={
            'agent_hasn_id': e2e.my_agent,
            'direction': 'sideways',
            'content': {'text': 'x'},
            'local_id': f'lid_bad_{_uid()}',
        },
    )
    assert r_baddir.status_code == 400, r_baddir.text
