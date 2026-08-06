"""doc19 S6-cloud · 合并闸三个端点的真实 HTTP + 真实 JWT + 真实 PG 验收（零 mock）。

设计事实源：``docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md``
  覆盖主脑单点可见、云端合并闸和失败语义

**为什么必须走 HTTP 而不是只调 service**（本仓 CLAUDE.md「加端点要跑真实 HTTP」）：

1. **scope 边界只在 HTTP 层存在**——service 根本不知道调用者拿的是 Owner JWT 还是 Agent JWT。
   铁律「agent scope 由 Agent JWT / Agent MCP Key 自识别，禁止 X-User-Id」只有在真实认证依赖
   跑起来时才被验证：本文件用**真签发的两种 token** 交叉打三个端点，两个方向都必须被拒。
2. **响应信封**——合并闸返回体经 daemon `decode_ok_envelope` 解析，裸返回会让 daemon 直接报
   `error decoding response body`（2026-06-02 权限 tab 事故）。service 层 E2E 抓不到这类外壳漂移。
3. **拒绝的 409 状态码与 `data.rejected_reason`** 是主脑重跑逻辑的唯一依据，只在 HTTP 边界成形。

需本地 PostgreSQL :15432 与 Redis（JWT 会话存 Redis）；不可达则跳过。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.admin.model.user import User
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_memory.api.v1.agent.merge_gate import router as agent_merge_gate_router
from backend.app.hasn_memory.api.v1.app.merge_status import router as app_merge_status_router
from backend.app.hasn_memory.api.v1.app.owner_memory import router as app_owner_memory_router
from backend.app.hasn_memory.api.v1.app.owner_profile_coverage import router as app_owner_profile_coverage_router
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt import create_agent_access_token, revoke_agent_token
from backend.common.security.jwt import create_access_token, revoke_token
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.redis import redis_client
from backend.middleware.jwt_auth_middleware import JwtAuthMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio(loop_scope='session')


def _build_app() -> FastAPI:
    """只挂本切片相关 router，其余中间件与异常处理全用生产件（认证真跑）。

    prefix 与 `app/hasn/api/router.py` 的真实挂载点逐字一致，否则这里绿了线上仍是错路径。
    """
    app = FastAPI()
    app.include_router(agent_merge_gate_router, prefix='/api/v1/hasn/memory/agent')
    app.include_router(app_merge_status_router, prefix='/api/v1/hasn/app/memory')
    # 另外两个 app scope 面同样要被 Agent JWT 打一遍（它们与 merge/status 是同一类隐患）
    app.include_router(app_owner_memory_router, prefix='/api/v1/hasn/app/owner')
    app.include_router(app_owner_profile_coverage_router, prefix='/api/v1/hasn/app/owner')
    register_exception(app)
    app.add_middleware(
        AuthenticationMiddleware,
        backend=JwtAuthMiddleware(),
        on_error=JwtAuthMiddleware.auth_exception_handler,
    )
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=False)])
    return app


_APP = _build_app()


@pytest_asyncio.fixture(scope='module', loop_scope='session')
async def gate() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    try:
        await redis_client.ping()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 Redis 不可达（JWT 会话依赖），跳过: {exc!r}')

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    owner_id = f'h_mgh{marker[:20]}'
    master_agent_id = f'a_mgh1{marker[:18]}'
    other_agent_id = f'a_mgh2{marker[:18]}'
    node_a = f'node_{marker[:12]}'
    node_b = f'node_{marker[12:24]}'

    async with sessions.begin() as db:
        user = User(username=f'mg_owner_{marker[:16]}', nickname=f'合并闸主人{marker[:6]}', password=None, salt=None)
        db.add(user)
        await db.flush()
        db.add_all([
            HasnHumans(
                hasn_id=owner_id,
                star_id=f'h{marker[:24]}',
                user_id=user.id,
                nickname=user.nickname,
                status='active',
            ),
            HasnAgents(
                hasn_id=master_agent_id,
                star_id=f'a{marker[:24]}',
                owner_id=owner_id,
                display_name='主脑分身',
                agent_name=f'master{marker[:10]}',
                role='primary',
                status='active',
                binding_node_id=node_a,
            ),
            HasnAgents(
                hasn_id=other_agent_id,
                star_id=f'b{marker[:24]}',
                owner_id=owner_id,
                display_name='普通分身',
                agent_name=f'other{marker[:10]}',
                role='specialist',
                status='active',
                binding_node_id=node_b,
            ),
        ])

    owner_token = await create_access_token(user.id, multi_login=True)
    master_token = await create_agent_access_token(
        agent_hasn_id=master_agent_id,
        agent_name='主脑分身',
        owner_hasn_id=owner_id,
        owner_user_id=user.id,
    )
    other_token = await create_agent_access_token(
        agent_hasn_id=other_agent_id,
        agent_name='普通分身',
        owner_hasn_id=owner_id,
        owner_user_id=user.id,
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://merge-gate-e2e')
    ctx = SimpleNamespace(
        client=client,
        sessions=sessions,
        owner_id=owner_id,
        master_agent_id=master_agent_id,
        other_agent_id=other_agent_id,
        node_a=node_a,
        node_b=node_b,
        owner_token=owner_token,
        master_token=master_token,
        other_token=other_token,
    )
    try:
        yield ctx
    finally:
        await client.aclose()
        await revoke_token(user.id, owner_token.session_uuid)
        await revoke_agent_token(master_agent_id, master_token.session_uuid)
        await revoke_agent_token(other_agent_id, other_token.session_uuid)
        await redis_client.delete(f'{settings.JWT_USER_REDIS_PREFIX}:{user.id}')
        async with sessions.begin() as db:
            for stmt in (
                sa.text('DELETE FROM hasn_memory.merge_run WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.merge_request WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.owner_memory WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_agents WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_humans WHERE hasn_id = :o'),
            ):
                await db.execute(stmt, {'o': owner_id})
            await db.execute(sa.delete(User).where(User.id == user.id))
        await engine.dispose()


def _bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def _apply_body(node_id: str, *, base_version: int = 0, run_id: str | None = None) -> dict:
    return {
        'run_id': run_id or f'mrun_{uuid.uuid4().hex[:24]}',
        'node_id': node_id,
        'base_owner_memory_version': base_version,
        'verdicts': [],
        'derived_facts': [],
        'owner_memory': {'content': '工作: 主人主攻 Rust 与分布式系统'},
        'peer_portraits': [],
        'summary': '本轮没有新事实，只刷新了档案',
        'stats': {'facts_judged': 0, 'facts_merged': 0, 'facts_disputed': 0},
    }


# --------------------------------------------------------------------------------------
# 1 · 正路：主脑 Agent JWT 打 apply，走统一信封
# --------------------------------------------------------------------------------------


async def test_master_brain_apply_over_http_returns_envelope(gate: SimpleNamespace) -> None:
    """主脑用真实 Agent JWT 提交整轮，200 + `{code,msg,data}` 信封（daemon 靠它解析）。"""
    response = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply',
        headers=_bearer(gate.master_token.access_token),
        json=_apply_body(gate.node_a),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {'code', 'msg', 'data'}
    data = body['data']
    assert data['applied'] is True
    assert data['new_owner_memory_version'] == 1
    assert data['replayed'] is False


# --------------------------------------------------------------------------------------
# 2 · 拒绝：409 + rejected_reason（主脑重跑的唯一依据）
# --------------------------------------------------------------------------------------


async def test_non_master_brain_apply_over_http_is_409(gate: SimpleNamespace) -> None:
    """D-18：非主脑分身的 Agent JWT 提交 → 409 + `data.rejected_reason='not_master_brain'`。"""
    response = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply',
        headers=_bearer(gate.other_token.access_token),
        json=_apply_body(gate.node_b, base_version=1),
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body['data']['rejected_reason'] == 'not_master_brain'
    assert body['data']['applied'] is False


async def test_version_conflict_over_http_is_409(gate: SimpleNamespace) -> None:
    """§5.6 CAS：基线过期 → 409 + `version_conflict`（前一个用例已把 version 推到 1）。"""
    response = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply',
        headers=_bearer(gate.master_token.access_token),
        json=_apply_body(gate.node_a, base_version=0),
    )
    assert response.status_code == 409, response.text
    assert response.json()['data']['rejected_reason'] == 'version_conflict'


# --------------------------------------------------------------------------------------
# 3 · scope 边界：两种 token 交叉打，两个方向都必须被拒
# --------------------------------------------------------------------------------------


async def test_owner_jwt_cannot_call_agent_scope_endpoints(gate: SimpleNamespace) -> None:
    """Owner JWT 打 agent scope 端点 → 401。

    身份必须由 Agent JWT 自识别；若 Owner JWT 能打进来，「哪个分身提交的合并」就成了请求体里
    可伪造的字段——主脑校验（D-18）连同整个合并闸一起失效。
    """
    headers = _bearer(gate.owner_token.access_token)
    apply_resp = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply', headers=headers, json=_apply_body(gate.node_a, base_version=1)
    )
    request_resp = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/request',
        headers=headers,
        json={'node_id': gate.node_a, 'reason': 'owner_manual'},
    )
    assert apply_resp.status_code == 401, apply_resp.text
    assert request_resp.status_code == 401, request_resp.text


#: 本模块全部 app scope（Owner JWT）端点。Agent JWT 打进来必须**逐个**是 401，不是 500。
#:
#: 中间件对 Agent JWT 是放行的（`is_agent_token` 分流），到了 handler 里 `request.user` 是
#: `UnauthenticatedUser`——直接取 `.id` 会抛 AttributeError 变成 500。500 会被 daemon 当成
#: 「服务器故障」按可重试处置反复重打，而这条请求无论重试多少次都不会成功；主人那边看到的
#: 也是一个查不出原因的服务器错误，而不是「你用错了凭据」。
_APP_SCOPE_ENDPOINTS = (
    ('GET', '/api/v1/hasn/app/memory/merge/status'),
    ('GET', '/api/v1/hasn/app/owner/memory'),
    ('GET', '/api/v1/hasn/app/owner/profile-coverage'),
    ('POST', '/api/v1/hasn/app/owner/proactive-planning/claim'),
)


@pytest.mark.parametrize(['method', 'path'], [list(item) for item in _APP_SCOPE_ENDPOINTS])
async def test_agent_jwt_on_app_scope_endpoint_is_401_not_500(
    gate: SimpleNamespace, method: str, path: str
) -> None:
    """Agent JWT 打任一 app scope（Owner JWT）端点 → **401**，且绝不是 500。

    主人可见性面只对主人开；scope 越界必须 fail-closed 成明确的 401。
    """
    response = await gate.client.request(method, path, headers=_bearer(gate.master_token.access_token))
    assert response.status_code == 401, response.text


async def test_owner_jwt_still_reads_app_scope_owner_memory(gate: SimpleNamespace) -> None:
    """反向钉子：fail-closed 不能把主人自己一起挡在门外（正路必须仍是 200 + 信封）。"""
    response = await gate.client.get(
        '/api/v1/hasn/app/owner/memory', headers=_bearer(gate.owner_token.access_token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {'code', 'msg', 'data'}
    assert 'version' in body['data']


async def test_agent_scope_requires_bearer(gate: SimpleNamespace) -> None:
    """无凭证直接 401，不许匿名摸到合并闸。"""
    response = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply', json=_apply_body(gate.node_a, base_version=1)
    )
    assert response.status_code == 401, response.text


# --------------------------------------------------------------------------------------
# 4 · 合并待办 + 主人可见性
# --------------------------------------------------------------------------------------


async def test_request_then_owner_sees_pending_and_last_merge(gate: SimpleNamespace) -> None:
    """非主脑 request → 主人 status 端点看到待办、上次整理设备与拒绝原因（§5.5）。"""
    requested = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/request',
        headers=_bearer(gate.other_token.access_token),
        json={'node_id': gate.node_b, 'reason': 'local_review_done'},
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()['data']['is_master_brain'] is False

    status = await gate.client.get(
        '/api/v1/hasn/app/memory/merge/status', headers=_bearer(gate.owner_token.access_token)
    )
    assert status.status_code == 200, status.text
    data = status.json()['data']
    assert data['has_pending_request'] is True
    assert data['pending_request']['requested_by_node'] == gate.node_b
    assert data['master_brain_agent_id'] == gate.master_agent_id
    assert data['master_brain_node_id'] == gate.node_a
    # 上一轮 apply 成功过，可见性面必须给出「上次整理于 X、在哪台设备」
    assert data['last_merge_node_id'] == gate.node_a
    assert data['last_merge_agent_id'] == gate.master_agent_id
    assert data['days_since_last_merge'] is not None
    assert data['stale_over_threshold'] is False
    # 前一个用例的 version_conflict 必须留痕可见（§5.6 拒绝可解释）
    assert data['last_rejected_reason'] == 'version_conflict'
    # 在线判定要么是明确的 True/False，要么是 None（判不了），绝不伪装成确定结论
    assert data['master_brain_online'] in (True, False, None)


async def test_master_brain_online_follows_real_node_presence(gate: SimpleNamespace) -> None:
    """§5.5「主脑在 <设备> 上，当前离线」必须接**真实** presence 源，不是常量占位。

    在线判据复用 IM 侧的心跳 TTL 键（`hasn:node_alive:{node_id}`），这里直接写/删那个键验证
    两态都真的翻转——若只断言「in (True, False, None)」，一个恒返回 None 的实现也能过关，
    而主人看到的就永远是「判不了」。
    """
    alive_key = f'hasn:node_alive:{gate.node_a}'
    headers = _bearer(gate.owner_token.access_token)
    try:
        await redis_client.set(alive_key, '1', ex=60)
        online = await gate.client.get('/api/v1/hasn/app/memory/merge/status', headers=headers)
        assert online.status_code == 200, online.text
        assert online.json()['data']['master_brain_online'] is True

        await redis_client.delete(alive_key)
        offline = await gate.client.get('/api/v1/hasn/app/memory/merge/status', headers=headers)
        assert offline.status_code == 200, offline.text
        assert offline.json()['data']['master_brain_online'] is False
    finally:
        await redis_client.delete(alive_key)


async def test_duplicate_run_id_over_http_replays(gate: SimpleNamespace) -> None:
    """§5.6 幂等：同一 run_id 重复 POST → 200 + `replayed=True`，version 不再推进。"""
    run_id = f'mrun_{uuid.uuid4().hex[:24]}'
    first = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply',
        headers=_bearer(gate.master_token.access_token),
        json=_apply_body(gate.node_a, base_version=1, run_id=run_id),
    )
    assert first.status_code == 200, first.text
    assert first.json()['data']['new_owner_memory_version'] == 2

    second = await gate.client.post(
        '/api/v1/hasn/memory/agent/merge/apply',
        headers=_bearer(gate.master_token.access_token),
        json=_apply_body(gate.node_a, base_version=1, run_id=run_id),
    )
    assert second.status_code == 200, second.text
    data = second.json()['data']
    assert data['replayed'] is True
    assert data['new_owner_memory_version'] == 2
