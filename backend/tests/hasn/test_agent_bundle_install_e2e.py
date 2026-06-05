"""技能包安装语义 进程内 HTTP E2E（实施/91 B2.5，真实 PG，零 mock）。

覆盖 B2.5 收编后的契约（install bundle 展开成员挂 Agent + profile 出参 skill_bundles）：
  1) POST /by-hasn-id/{hasn_id}/bundles/install（owner JWT）→ 云端展开 skill_pack 成员技能
     并入 hasn_agents.skills、记录引用进 hasn_agents.skill_bundles、bump profile_revision，
     返回 bundle 快照（hermes_yaml / 成员 skill_ids）。
  2) GET /api/v1/hasn/agent/profile（agent JWT）→ 出参 skill_bundles=[{bundle_slug,
     command_key, hermes_yaml}]，且成员技能已进 skills。
  3) 幂等：重复 install 同包 → skills/skill_bundles/profile_revision 不变。

最小 app 同时挂 app-scope 安装路由 + agent-scope profile 路由 + 真实 PG + override 两套
鉴权（owner user.id / agent JWT）；经 ASGITransport 走完整 HTTP。事务末尾回滚不污染库。
需要 export DATABASE_PORT=15432。
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_agent_profile import router as profile_router
from backend.app.hasn.api.v1.app.hasn_agents import router as app_agents_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(app_agents_router, prefix='/api/v1/hasn/app/agents')
_APP.include_router(profile_router, prefix='/api/v1/hasn/agent')


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
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    tag = _uid()
    owner = f'h_owner_{tag}'
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_{tag}'
    package_id = f'huanxing/backend-dev-{tag}'
    bundle_slug = f'backend-dev-{tag}'
    command_key = f'/{bundle_slug}'
    hermes_yaml = (
        f'name: {bundle_slug}\n'
        'description: 后端开发\n'
        'skills:\n'
        '  - developer/code-review\n'
        '  - productivity/tdd\n'
    )
    members = ['developer/code-review', 'productivity/tdd']
    preexisting = 'user/legacy/own-skill'

    session.add(
        HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='Owner', status='active')
    )
    session.add(
        HasnAgents(
            hasn_id=agent_hasn,
            star_id=f'{owner_uid}#{tag}',
            owner_id=owner,
            display_name='E2E Agent',
            agent_name=f'agent_{tag}',
            type='desktop',
            role='specialist',
            api_key_hash='hash',
            status='active',
            created_via='client',
            skills=[preexisting],
            skill_bundles=[],
            profile_revision=3,
        )
    )
    # 直接落一个 skill_pack 模板 + 最新版本（含 bundle_slug/command_key/hermes_yaml）。
    await session.execute(
        text(
            '''
            INSERT INTO marketplace_template (template_id, namespace, slug, template_type, name,
                author_id, pricing_type, price, is_private, is_official, status, download_count,
                source_type, created_time, updated_time)
            VALUES (:tid, 'huanxing', :slug, 'skill_pack', :slug, :author, 'free', 0, false, true,
                'published', 0, 'local', now(), now())
            '''
        ),
        {'tid': package_id, 'slug': bundle_slug, 'author': owner_uid},
    )
    await session.execute(
        text(
            '''
            INSERT INTO marketplace_template_version (template_id, version, bundle_slug, command_key,
                hermes_yaml, content_hash, file_hash, is_latest, published_at, created_time, updated_time)
            VALUES (:tid, '1.0.0', :slug, :cmd, :yaml, 'sha256:deadbeef', 'deadbeef', true, now(), now(), now())
            '''
        ),
        {'tid': package_id, 'slug': bundle_slug, 'cmd': command_key, 'yaml': hermes_yaml},
    )
    await session.flush()

    async def _yield_session():
        yield session

    async def _owner_auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=owner_uid)

    async def _agent_auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner,
            owner_user_id=owner_uid,
            scopes=['agent'],
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner, agent_hasn=agent_hasn,
            package_id=package_id, bundle_slug=bundle_slug, command_key=command_key,
            hermes_yaml=hermes_yaml, members=members, preexisting=preexisting,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _install(client, agent_hasn: str, package_id: str) -> dict:
    r = await client.post(
        f'/api/v1/hasn/app/agents/by-hasn-id/{agent_hasn}/bundles/install',
        json={'package_id': package_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['code'] == 200, body
    return body['data']


async def _get_profile(client) -> dict:
    r = await client.get('/api/v1/hasn/agent/profile')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['code'] == 200, body
    return body['data']


async def test_install_bundle_expands_members_and_profile_outputs_skill_bundles(e2e):
    c = e2e.client

    # 1) 安装技能包：成员并入 skills、记录引用、bump revision、返回 bundle 快照
    data = await _install(c, e2e.agent_hasn, e2e.package_id)
    bundle = data['bundle']
    assert bundle['template_id'] == e2e.package_id
    assert bundle['version'] == '1.0.0'
    assert bundle['bundle_slug'] == e2e.bundle_slug
    assert bundle['command_key'] == e2e.command_key
    assert set(bundle['skill_ids']) == set(e2e.members)
    assert data['profile_revision'] == 4  # 3 → 4

    agent_skills = data['agent']['skills']
    assert e2e.preexisting in agent_skills          # 原有技能保留
    for m in e2e.members:
        assert m in agent_skills                    # 成员技能并入

    # 2) agent profile 出参 skill_bundles + 成员进 skills
    profile = await _get_profile(c)
    assert profile['profile_revision'] == 4
    for m in e2e.members:
        assert m in profile['skills']
    bundles = profile['skill_bundles']
    assert len(bundles) == 1, bundles
    assert bundles[0]['bundle_slug'] == e2e.bundle_slug
    assert bundles[0]['command_key'] == e2e.command_key
    assert 'developer/code-review' in bundles[0]['hermes_yaml']
    assert 'productivity/tdd' in bundles[0]['hermes_yaml']


async def test_install_bundle_is_idempotent(e2e):
    c = e2e.client

    first = await _install(c, e2e.agent_hasn, e2e.package_id)
    assert first['profile_revision'] == 4
    first_skills = first['agent']['skills']

    # 重复安装同包 → 不再 bump、不再追加
    second = await _install(c, e2e.agent_hasn, e2e.package_id)
    assert second['profile_revision'] == 4
    assert second['agent']['skills'] == first_skills

    # skill_bundles 仍只有一条
    profile = await _get_profile(c)
    assert len(profile['skill_bundles']) == 1
