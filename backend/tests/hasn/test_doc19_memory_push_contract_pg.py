"""doc19 S5-cloud · 记忆上行 push 端点的真实 HTTP + 真实 PG 契约验收（零 mock）。

设计事实源：``docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md`` §8.3
事故教训：``实施/98-记忆上推链路整体退役与本地镜像单向化实施方案.md``

**为什么必须走 HTTP 而不是只调 service**：实施/98 的故障链第 ④⑤ 环恰恰在 HTTP 边界上——
云端白名单不含某事件类型 → 200 + ``rejected:[{code:8040}]`` → 客户端契约把 int code 解析成
String 直接炸 → 整批不 clean → 5s 无限重推。service 层 E2E 绕过 HTTP，抓不到这类外壳漂移
（本仓 CLAUDE.md「加端点要跑真实 HTTP」）。

本文件钉死四件事：

1. 未登记事件仍被 8040 永久拒（白名单没被顺手放宽）；
2. 六个新事件被接受并真的落进 sync inbox（两侧白名单已对称，§8.3-5）；
3. 墓碑命中 / origin 不符在**入队前**就被逐事件拒回，且带 ``client_event_id`` 与
   ``purge_local`` 指令（§8.3-4 / §8.3-6）；
4. 私有运行时元数据守卫不再误伤事实内容——一条 object 里带 ``endpoint`` 键的记忆不会被永久
   拒绝后静默丢失。

需本地 PostgreSQL :15432（不可达则跳过）。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1 import sync as sync_api
from backend.app.hasn_memory.service.fact_uplink_service import (
    MEMORY_FACT_UPLINK_EVENTS,
    fact_uplink_service,
    reset_warn_once_cache,
)
from backend.common.exception.exception_handler import register_exception
from backend.database.db import (
    SQLALCHEMY_DATABASE_URL,
    async_engine,
    get_db,
    get_sync_db,
    get_sync_db_transaction,
)
from backend.database.schema_names import SCHEMA_NAMES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')
_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')

OWNER_ID = f'h_pc{uuid.uuid4().hex[:16]}'
AGENT_ID = f'a_pc{uuid.uuid4().hex[:16]}'
NODE_ID = f'node_{uuid.uuid4().hex[:12]}'
OTHER_NODE_ID = f'node_{uuid.uuid4().hex[:12]}'


def _fake_owner_jwt() -> Callable[[Request], Awaitable[None]]:
    """只替换认证：owner 身份来自 JWT（`require_owner_identity` 的权威来源）。

    其余全部走真实路由与真实 DB——本测试的价值就在于不绕开 HTTP 与 PG。
    """

    async def dependency(request: Request) -> None:  # noqa: RUF029 FastAPI 依赖必须是协程
        request.scope['user'] = SimpleNamespace(id=7, hasn_id=OWNER_ID)

    return dependency


@pytest_asyncio.fixture
async def client() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def real_db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    async def real_db_transaction() -> AsyncIterator[AsyncSession]:
        async with sessions.begin() as session:
            yield session

    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(app)
    app.include_router(sync_api.router, prefix='/api/v1/hasn')
    app.dependency_overrides[get_db] = real_db
    app.dependency_overrides[get_sync_db] = real_db
    app.dependency_overrides[get_sync_db_transaction] = real_db_transaction
    app.dependency_overrides[sync_api.DependsJwtAuth.dependency] = _fake_owner_jwt()

    reset_warn_once_cache()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        try:
            yield http, sessions
        finally:
            async with sessions.begin() as session:
                await session.execute(
                    sa.text(f'DELETE FROM {_INBOX} WHERE owner_id = :owner_id'),
                    {'owner_id': OWNER_ID},
                )
                await session.execute(
                    sa.text(f'DELETE FROM {_SYNC_EVENTS} WHERE owner_id = :owner_id'),
                    {'owner_id': OWNER_ID},
                )
                await session.execute(
                    sa.text('DELETE FROM hasn_memory.semantic_fact WHERE owner_id = :owner_id'),
                    {'owner_id': OWNER_ID},
                )
                await session.execute(
                    sa.text('DELETE FROM hasn_memory.fact_tombstone WHERE owner_id = :owner_id'),
                    {'owner_id': OWNER_ID},
                )
                await session.execute(
                    sa.text('DELETE FROM hasn_memory.merge_request WHERE owner_id = :owner_id'),
                    {'owner_id': OWNER_ID},
                )
                await session.execute(
                    sa.text(
                        'DELETE FROM hasn_memory.namespace_revision '
                        'WHERE sync_scope_id IN (:owner_id, :agent_id)'
                    ),
                    {'owner_id': OWNER_ID, 'agent_id': AGENT_ID},
                )
            await engine.dispose()
            await async_engine.dispose()


def _fact_payload(fact_id: str, *, node_id: str = NODE_ID, revision: int = 1, obj: Any = '喜欢冰美式') -> dict:
    """与本地 ``fact_snapshot_json``（hasn-memory/src/storage/facts.rs）同形的上行载荷。"""
    return {
        'fact_id': fact_id,
        'owner_id': OWNER_ID,
        'agent_id': AGENT_ID,
        'subject_kind': 'agent_self',
        'subject_id': AGENT_ID,
        'scope_kind': 'global',
        'scope_id': AGENT_ID,
        'predicate': '偏好',
        'object_json': obj,
        'confidence': 0.8,
        'status': 'active',
        'superseded_by': None,
        'source_turn_ids': [],
        'source_refs': [],
        'rationale': None,
        'valid_until': None,
        'created_at': 1_785_000_000_000,
        'updated_at': 1_785_000_000_000,
        'revision': revision,
        'origin_kind': 'node',
        'origin_node_id': node_id,
        'origin_agent_id': AGENT_ID,
        'merged_from': [],
    }


async def _push(http: AsyncClient, events: list[dict]) -> dict:
    response = await http.post(
        '/api/v1/hasn/sync/push',
        headers={'Authorization': 'Bearer jwt-doc19-push', 'X-Node-Id': NODE_ID},
        json={'owner_id': OWNER_ID, 'node_id': NODE_ID, 'events': events},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _inbox_event_types(sessions: async_sessionmaker) -> set[str]:
    async with sessions() as session:
        rows = (
            await session.execute(
                sa.text(f'SELECT event_type FROM {_INBOX} WHERE owner_id = :owner_id'),
                {'owner_id': OWNER_ID},
            )
        ).scalars()
        return set(rows)


async def test_unregistered_event_type_is_still_rejected_with_8040(client: tuple) -> None:
    """§8.3-5：白名单只放宽到六个语义事实事件，其它类型照旧 8040 永久拒。

    实施/98 的 ④ 环就是「本地能产 12 类、云端只认 3 类」；这条守住反向——放宽不许放成敞口。
    """
    http, sessions = client
    body = await _push(
        http,
        [
            {
                'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
                'event_type': 'memory.agent_self_portrait.upserted',
                'payload': {'agent_id': AGENT_ID},
            }
        ],
    )
    assert body['accepted'] == 0
    assert len(body['rejected']) == 1
    rejected = body['rejected'][0]
    assert rejected['code'] == 8040
    assert rejected['name'] == 'ERR_SYNC_EVENT_UNSUPPORTED'
    assert rejected['detail']['client_event_id'] is not None
    assert await _inbox_event_types(sessions) == set()


async def test_all_six_fact_events_are_accepted_into_inbox(client: tuple) -> None:
    """§8.3-5：六个新事件全部被接受并真的进 sync inbox（两侧白名单已对称）。"""
    http, sessions = client
    fact_id = uuid.uuid4().hex
    # saved 先落云端，后面五个整理 / 裁决 / 硬删事件才不会命中「尚未汇聚」冲突。
    async with sessions.begin() as session:
        await fact_uplink_service.apply_fact_event(
            session,
            owner_id=OWNER_ID,
            node_id=NODE_ID,
            event_type='memory.fact.saved',
            payload=_fact_payload(fact_id),
        )

    events = [
        {
            'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
            'event_type': 'memory.fact.saved',
            'payload': _fact_payload(fact_id),
        },
        {
            'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
            'event_type': 'memory.fact.updated',
            'payload': _fact_payload(fact_id, revision=2, obj='改喝手冲'),
        },
        {
            'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
            'event_type': 'memory.fact.superseded',
            'payload': _fact_payload(fact_id, revision=3),
        },
        {
            'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
            'event_type': 'memory.fact.withdrawn',
            'payload': _fact_payload(fact_id, revision=4),
        },
        {
            'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
            'event_type': 'memory.fact.merge_verdict',
            'payload': {
                'fact_id': fact_id,
                'merge_verdict': 'disputed',
                'merge_verdict_run': 'run_http',
                'merge_judged_revision': 1,
            },
        },
        {
            'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
            'event_type': 'memory.fact.purged',
            'payload': {'fact_id': fact_id, 'owner_id': OWNER_ID, 'purged_by': OWNER_ID},
        },
    ]
    body = await _push(http, events)
    assert body['rejected'] == [], body['rejected']
    assert body['accepted'] == len(events)
    assert await _inbox_event_types(sessions) == set(MEMORY_FACT_UPLINK_EVENTS)
    assert body['next_cursor'].startswith(f'owner:{OWNER_ID}:')


async def test_tombstone_hit_is_rejected_before_enqueue_with_purge_local(client: tuple) -> None:
    """§8.3-6：墓碑命中在**入队前**就被拒——毒丸不许进队列，并回令来源节点清本地。"""
    http, sessions = client
    fact_id = uuid.uuid4().hex
    async with sessions.begin() as session:
        await fact_uplink_service.apply_fact_event(
            session,
            owner_id=OWNER_ID,
            node_id=NODE_ID,
            event_type='memory.fact.saved',
            payload=_fact_payload(fact_id),
        )
    async with sessions.begin() as session:
        await fact_uplink_service.apply_fact_event(
            session,
            owner_id=OWNER_ID,
            node_id=NODE_ID,
            event_type='memory.fact.purged',
            payload={'fact_id': fact_id, 'owner_id': OWNER_ID, 'purged_by': OWNER_ID},
        )

    survivor = uuid.uuid4().hex
    client_event_id = f'ce_{uuid.uuid4().hex[:20]}'
    body = await _push(
        http,
        [
            {
                'client_event_id': client_event_id,
                'event_type': 'memory.fact.updated',
                'payload': _fact_payload(fact_id, revision=2),
            },
            {
                'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
                'event_type': 'memory.fact.saved',
                'payload': _fact_payload(survivor),
            },
        ],
    )
    assert body['accepted'] == 1, '同批其余事件必须照常入队——一条毒丸不许拖住整批'
    assert len(body['rejected']) == 1
    rejected = body['rejected'][0]
    assert rejected['code'] == 8045
    assert rejected['name'] == 'ERR_MEMORY_FACT_PURGED'
    assert rejected['detail'] == {
        'action': 'purge_local',
        'fact_id': fact_id,
        'client_event_id': client_event_id,
    }
    async with sessions() as session:
        queued = (
            await session.execute(
                sa.text(
                    f"SELECT payload->>'fact_id' FROM {_INBOX} WHERE owner_id = :owner_id"
                ),
                {'owner_id': OWNER_ID},
            )
        ).scalars().all()
    assert set(queued) == {survivor}, '已 purge 的事实绝不许再进队列'


async def test_origin_mismatch_is_rejected_before_enqueue(client: tuple) -> None:
    """§8.3-3：``origin_node_id`` 与推送节点不符 → 8044 永久拒，事件不入队。"""
    http, sessions = client
    fact_id = uuid.uuid4().hex
    body = await _push(
        http,
        [
            {
                'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
                'event_type': 'memory.fact.saved',
                'payload': _fact_payload(fact_id, node_id=OTHER_NODE_ID),
            }
        ],
    )
    assert body['accepted'] == 0
    assert body['rejected'][0]['code'] == 8044
    assert body['rejected'][0]['name'] == 'ERR_MEMORY_FACT_ORIGIN_MISMATCH'
    assert await _inbox_event_types(sessions) == set()


async def test_missing_fact_update_is_conflict_not_permanent(client: tuple) -> None:
    """§8.3-4：事实尚未汇聚 → 8041 冲突（daemon 退避重试），不是永久拒绝。"""
    http, _sessions = client
    body = await _push(
        http,
        [
            {
                'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
                'event_type': 'memory.fact.updated',
                'payload': _fact_payload(uuid.uuid4().hex, revision=2),
            }
        ],
    )
    assert body['accepted'] == 0
    assert body['rejected'][0]['code'] == 8041
    assert body['rejected'][0]['name'] == 'ERR_SYNC_EVENT_CONFLICT'


async def test_private_runtime_key_guard_does_not_swallow_fact_content(client: tuple) -> None:
    """事实内容里出现 ``endpoint``/``token`` 一类键不得被 8034 永久拒。

    该守卫是给 runtime report 设计的（拦本地私有元数据回传），但它递归全载荷。记忆事实的
    object 是主人和分身写下的**任意内容**——一条「网关 endpoint 配置是 X」的事实若被永久拒绝，
    daemon 会丢弃出队，那条记忆就此静默消失。doc19 D-11 明确本期云端明文存储，对内容扫这些
    键既保护不了隐私、又制造真实的记忆丢失，故内容子树豁免、信封层照扫。
    """
    http, sessions = client
    fact_id = uuid.uuid4().hex
    body = await _push(
        http,
        [
            {
                'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
                'event_type': 'memory.fact.saved',
                'payload': _fact_payload(fact_id, obj={'endpoint': 'https://gw.example', 'token': 'abc'}),
            }
        ],
    )
    assert body['rejected'] == [], body['rejected']
    assert body['accepted'] == 1
    assert await _inbox_event_types(sessions) == {'memory.fact.saved'}

    # 但信封层仍然照扫：私有运行时元数据混进事实信封依旧 8034 永久拒。
    leaky = _fact_payload(uuid.uuid4().hex)
    leaky['workspace_path'] = '/Users/someone/.hasn'
    leaked = await _push(
        http,
        [
            {
                'client_event_id': f'ce_{uuid.uuid4().hex[:20]}',
                'event_type': 'memory.fact.saved',
                'payload': leaky,
            }
        ],
    )
    assert leaked['rejected'][0]['code'] == 8034
