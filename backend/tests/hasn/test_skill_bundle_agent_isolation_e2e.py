"""技能包 agent scope owner 隔离 进程内 HTTP E2E（实施 B0.2，真实 PG，零 mock）。

覆盖 B0 安全急修后的 agent 端契约：
  1) GET 列表只回本 owner 的 bundle（不暴露其它 owner 私有任务域资源）。
  2) GET/PUT/DELETE 其它 owner 的 bundle pk → 403 ForbiddenError。
  3) POST create 入参伪造 owner_id → 落库仍是令牌身份 owner（不信入参，强制覆盖）。

最小 app 挂真实 agent skill_bundle 路由 + 真实 PG + override agent JWT（owner A）；经
ASGITransport 走完整 HTTP 栈（依赖注入 + 统一信封）。事务末尾回滚。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_pagination import add_pagination
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.api.v1.agent.skill_bundle import router as bundle_router
from backend.app.hasn_task.model.skill_bundle import HasnSkillBundle
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(bundle_router, prefix='/api/v1/hasn/agent/hasn/skill/bundles')
add_pagination(_APP)  # list 端点 paging_data 需要分页上下文


# 轻量异常映射：把自定义 ForbiddenError/NotFoundError 映射为对应 HTTP 状态（信封形）。
# 不引入完整 register_exception（其依赖 starlette_context 中间件，而 BaseHTTPMiddleware 会与
# 跨事件循环共享的异步会话冲突）。Starlette 按 MRO 查处理器，注册基类即可覆盖子类。
@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


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

    tag = _uid()
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    owner_a_uid = 970000 + int(uuid.uuid4().int % 9000)

    # A/B 两 owner 各一个私有 bundle
    bundle_a = HasnSkillBundle(
        owner_id=owner_a, name=f'bundle-a-{tag}', display_name='A 的包',
        description='A', skill_ids=['developer/code-review'], instruction='go',
    )
    bundle_b = HasnSkillBundle(
        owner_id=owner_b, name=f'bundle-b-{tag}', display_name='B 的包',
        description='B', skill_ids=['x/y'], instruction='nope',
    )
    session.add_all([bundle_a, bundle_b])
    await session.flush()

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> AgentTokenPayload:
        payload = AgentTokenPayload(
            agent_hasn_id=f'a_{tag}',
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner_a,  # 令牌身份恒为 owner A
            owner_user_id=owner_a_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1),
        )
        request.state.agent = payload  # 真实 agent_jwt_auth 同样落 request.state.agent
        return payload

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session  # 写端点同走 fixture 会话，末尾回滚清理
    _APP.dependency_overrides[agent_jwt_auth] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session,
            owner_a=owner_a, owner_b=owner_b,
            bundle_a_id=bundle_a.id, bundle_b_id=bundle_b.id, tag=tag,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_list_only_returns_own_owner_bundles(e2e) -> None:
    r = await e2e.client.get('/api/v1/hasn/agent/hasn/skill/bundles')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['code'] == 200, body
    ids = [item['id'] for item in body['data']['items']]
    assert e2e.bundle_a_id in ids, ids
    assert e2e.bundle_b_id not in ids, ids  # B 的私有包不暴露


async def test_get_other_owner_bundle_forbidden(e2e) -> None:
    r = await e2e.client.get(f'/api/v1/hasn/agent/hasn/skill/bundles/{e2e.bundle_b_id}')
    assert r.status_code == 403, r.text


async def test_update_other_owner_bundle_forbidden(e2e) -> None:
    r = await e2e.client.put(
        f'/api/v1/hasn/agent/hasn/skill/bundles/{e2e.bundle_b_id}',
        json={'owner_id': e2e.owner_b, 'name': 'hacked', 'skill_ids': []},
    )
    assert r.status_code == 403, r.text


async def test_delete_other_owner_bundle_forbidden(e2e) -> None:
    r = await e2e.client.delete(f'/api/v1/hasn/agent/hasn/skill/bundles/{e2e.bundle_b_id}')
    assert r.status_code == 403, r.text


async def test_create_overrides_forged_owner_id(e2e) -> None:
    # 入参伪造 owner_id='B'，落库应被强制覆盖为令牌身份 owner A
    r = await e2e.client.post(
        '/api/v1/hasn/agent/hasn/skill/bundles',
        json={
            'owner_id': e2e.owner_b, 'name': f'forged-{e2e.tag}', 'skill_ids': ['a/b'],
            'created_time': '2026-06-05T00:00:00+00:00',
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()['code'] == 200

    row = (
        await e2e.session.execute(
            select(HasnSkillBundle).where(HasnSkillBundle.name == f'forged-{e2e.tag}')
        )
    ).scalar_one()
    assert row.owner_id == e2e.owner_a, row.owner_id  # 伪造被覆盖
