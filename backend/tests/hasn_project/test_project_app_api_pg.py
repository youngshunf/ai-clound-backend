"""平台项目（doc38 U5）用户端 API `/api/v1/project/app/*` 真实 HTTP E2E（真实 PostgreSQL，零 mock）。

经真实 ASGI HTTP（httpx.ASGITransport）打两个用户端 router，验证它们由真正 owner 隔离的
``ProjectService`` + 挂靠点注册表支撑（而非 codegen 样板 int pk / user_id）：
- **project CRUD**：list(items 信封) / create / get(含 milestones + artifact_flow) / update / archive / restore；
- **milestone**：create（POST /projects/{pk}/milestones）/ update（PUT /milestones/{id}）/ complete；
- **联邦挂靠**：link / unlink 经注册表落 ``hasn_artifacts.project_id``，artifact-flow 并集读随之增减；
- **owner 隔离 + 校验**：跨 owner get → 404、name 必填 → 400。

统一信封：每个端点都断言 ``{code:200,data:...}``（裸返回会让 daemon 解析炸，故守住）。
逐文件跑（asyncpg loop 限制）：``uv run pytest backend/tests/hasn_project/test_project_app_api_pg.py -x``。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_project.api.v1.app.hasn_project import router as app_project_router
from backend.app.hasn_project.api.v1.app.hasn_project_milestone import router as app_milestone_router
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
# 与 router.py 一致：app router → /api/v1/project/app/projects；milestone router → /api/v1/project/app/milestones。
_APP.include_router(app_project_router, prefix='/api/v1/project/app/projects')
_APP.include_router(app_milestone_router, prefix='/api/v1/project/app/milestones')

_PROJECTS = '/api/v1/project/app/projects'
_MILESTONES = '/api/v1/project/app/milestones'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 970_000_000 + int(uuid.uuid4().int % 20_000_000)


def _human(hasn_id: str, user_id: int) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'项目E2E_{hasn_id[-6:]}', status='active'
    )


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    user_id = _new_user_id()
    owner = f'h_prj_{_uid()}'
    agent = f'a_prj_{_uid()}'
    session.add(_human(owner, user_id))
    await session.flush()

    auth_state = {'user_id': user_id}

    async def _yield_session():
        # 用户端两类会话（读 get_db / 写 get_db_transaction）在测试里共用同一未提交 session，
        # 末尾统一 rollback 不污染库（与既有 HTTP E2E 范式一致）。
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, owner=owner, agent=agent, session=session, user_id=user_id, auth_state=auth_state
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    """断言 HTTP 200 + 信封 code=200，返回 data。"""
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


async def _seed_artifact(session, *, owner: str, agent: str, artifact_id: str) -> None:
    """插一条 owner 名下产物行（link/unlink/并集读用）。只填 NOT NULL 关键列。"""
    session.add(
        HasnArtifacts(
            artifact_id=artifact_id,
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            kind='resource',
            source_kind='app',
            status='active',
            title=f'产物 {artifact_id}',
        )
    )
    await session.flush()


# ── project CRUD ──────────────────────────────────────────────────────────────
async def test_create_list_get_roundtrip(env) -> None:
    """建 → 列(items 信封) → 详情(含空里程碑轨 + artifact_flow 键)。"""
    c = env.client
    created = _data(await c.post(_PROJECTS, json={'name': '换根重构', 'goal': '把项目当第三条轴'}))
    assert created['name'] == '换根重构'
    assert created['status'] == 'active'
    assert created['owner_id'] == env.owner
    # id 是云端权威 UUID（进 hasn://project/{id}），字符串形态。
    uuid.UUID(created['id'])

    listed = _data(await c.get(_PROJECTS))
    assert 'items' in listed
    assert any(r['id'] == created['id'] for r in listed['items'])

    detail = _data(await c.get(f'{_PROJECTS}/{created["id"]}'))
    assert detail['id'] == created['id']
    assert detail['milestones'] == []  # 新建无里程碑
    assert detail['artifact_flow'] == []  # 无产物挂靠


async def test_create_name_required_400(env) -> None:
    """name 为空 → 业务 400（name_required），不落库。"""
    resp = await env.client.post(_PROJECTS, json={'name': '   '})
    assert resp.status_code == 400, resp.text
    assert resp.json()['code'] == 400


async def test_update_archive_restore(env) -> None:
    """改目标 → 归档(status archived) → 恢复(active)。"""
    c = env.client
    proj = _data(await c.post(_PROJECTS, json={'name': 'P'}))
    pid = proj['id']

    upd = _data(await c.put(f'{_PROJECTS}/{pid}', json={'goal': '改后的目标'}))
    assert upd['goal'] == '改后的目标'
    assert upd['name'] == 'P'  # 未传字段不动

    arch = _data(await c.post(f'{_PROJECTS}/{pid}/archive'))
    assert arch['status'] == 'archived'

    restored = _data(await c.post(f'{_PROJECTS}/{pid}/restore'))
    assert restored['status'] == 'active'


# ── milestone（create 在 /projects 面，update/complete 在 /milestones 面）─────────
async def test_milestone_create_update_complete(env) -> None:
    """POST /projects/{pk}/milestones 建 → PUT /milestones/{id} 改 → complete → 详情随查出。"""
    c = env.client
    proj = _data(await c.post(_PROJECTS, json={'name': 'P'}))
    pid = proj['id']

    ms = _data(await c.post(f'{_PROJECTS}/{pid}/milestones', json={'name': '里程碑1'}))
    assert ms['status'] == 'pending'
    assert ms['project_id'] == pid
    mid = ms['id']

    upd = _data(await c.put(f'{_MILESTONES}/{mid}', json={'sort': 5}))
    assert upd['sort'] == 5

    done = _data(await c.post(f'{_MILESTONES}/{mid}/complete'))
    assert done['status'] == 'done'

    detail = _data(await c.get(f'{_PROJECTS}/{pid}'))
    assert len(detail['milestones']) == 1
    assert detail['milestones'][0]['status'] == 'done'


# ── 联邦挂靠 link/unlink + 产物流并集读 ─────────────────────────────────────────
async def test_link_unlink_and_artifact_flow(env) -> None:
    """seed 产物 → link 挂进项目 → artifact-flow 含它 → unlink 摘出 → artifact-flow 不再含。"""
    c = env.client
    art_in = f'art_{uuid.uuid4().hex[:16]}'
    art_out = f'art_{uuid.uuid4().hex[:16]}'
    await _seed_artifact(env.session, owner=env.owner, agent=env.agent, artifact_id=art_in)
    await _seed_artifact(env.session, owner=env.owner, agent=env.agent, artifact_id=art_out)

    proj = _data(await c.post(_PROJECTS, json={'name': 'P'}))
    pid = proj['id']

    linked = _data(await c.post(f'{_PROJECTS}/{pid}/link', json={'resource_uri': f'hasn://artifact/{art_in}'}))
    assert linked['linked'] is True

    flow = _data(await c.get(f'{_PROJECTS}/{pid}/artifact-flow'))
    ids = {r['artifact_id'] for r in flow['items']}
    assert art_in in ids
    assert art_out not in ids  # 未挂靠的不进流

    unlinked = _data(await c.post(f'{_PROJECTS}/{pid}/unlink', json={'resource_uri': f'hasn://artifact/{art_in}'}))
    assert unlinked['unlinked'] is True

    flow2 = _data(await c.get(f'{_PROJECTS}/{pid}/artifact-flow'))
    assert art_in not in {r['artifact_id'] for r in flow2['items']}


async def test_link_unsupported_domain_400(env) -> None:
    """未注册资源域挂靠 → 业务 400（unsupported_link_domain）。U3 只注册了 artifact。"""
    c = env.client
    proj = _data(await c.post(_PROJECTS, json={'name': 'P'}))
    resp = await c.post(f'{_PROJECTS}/{proj["id"]}/link', json={'resource_uri': 'hasn://deck/deck_x'})
    assert resp.status_code == 400, resp.text


# ── owner 隔离 ─────────────────────────────────────────────────────────────────
async def test_cross_owner_get_404(env) -> None:
    """A 建项目，切到 B（另一登录用户）GET A 的项目 → 404（不泄漏存在性）。"""
    c = env.client
    proj = _data(await c.post(_PROJECTS, json={'name': 'A 的项目'}))

    # 切换到 owner B（另一 human + user_id）。
    owner_b = f'h_prjB_{_uid()}'
    user_b = _new_user_id()
    env.session.add(_human(owner_b, user_b))
    await env.session.flush()
    env.auth_state['user_id'] = user_b

    resp = await c.get(f'{_PROJECTS}/{proj["id"]}')
    assert resp.status_code == 404, resp.text
    assert resp.json()['code'] == 404


async def test_cross_owner_milestone_complete_404(env) -> None:
    """A 的里程碑对 B 不可操作（经父项目归属校验）→ 404。"""
    c = env.client
    proj = _data(await c.post(_PROJECTS, json={'name': 'A 的项目'}))
    ms = _data(await c.post(f'{_PROJECTS}/{proj["id"]}/milestones', json={'name': 'M'}))

    owner_b = f'h_prjB_{_uid()}'
    user_b = _new_user_id()
    env.session.add(_human(owner_b, user_b))
    await env.session.flush()
    env.auth_state['user_id'] = user_b

    resp = await c.post(f'{_MILESTONES}/{ms["id"]}/complete')
    assert resp.status_code == 404, resp.text
