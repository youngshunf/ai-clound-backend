"""公共技能清单 open 端点 + profile 出参 common_skill_ids（doc11 §6 B 支撑，真实 PG 零 mock）。

覆盖：
  1. GET /api/v1/marketplace/open/skills/common：统一信封（response_base.success），
     data = {revision, skills:[{skill_id, fingerprint}]}；seed 的 is_common 技能在列，
     指纹与版本行一致，revision 与 get_common_skill_snapshot 同源一致。
  2. 该路由不被 ``/{resource_id:path}`` 详情 catch-all 吞掉（注册顺序守卫）。
  3. AgentProfileResponse.common_skill_ids：新字段默认空列表（向后兼容，旧 runtime 忽略）。

最小 app 挂真实 open 技能路由 + 真实 PG（override get_db，seed 不 commit、末尾 rollback）。
需要 export DATABASE_PORT=15432（本地 PG）。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_agents import AgentProfileResponse
from backend.app.marketplace.api.v1.open.marketplace_skills import router as open_skills_router
from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.service.common_skills_service import get_common_skill_snapshot
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(open_skills_router, prefix='/api/v1/marketplace/open/skills')


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    async def _yield_session():
        yield session

    _APP.dependency_overrides[get_db] = _yield_session
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(http=http, session=session)
    finally:
        await http.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_open_common_skills_manifest(client) -> None:
    skill_id = f'huanxing/test/open-common-{uuid.uuid4().hex[:8]}'
    namespace, slug = skill_id.rsplit('/', 1)
    client.session.add(
        MarketplaceSkill(
            skill_id=skill_id,
            namespace=namespace,
            slug=slug,
            name=slug,
            status='published',
            visibility='public',
            is_common=True,
        )
    )
    client.session.add(
        MarketplaceSkillVersion(skill_id=skill_id, version='1.0.0', content_hash='fp-open-1', is_latest=True)
    )
    await client.session.flush()

    resp = await client.http.get('/api/v1/marketplace/open/skills/common')
    assert resp.status_code == 200
    body = resp.json()
    # 统一信封硬规则：{code, msg, data}，不许裸返回。
    assert body['code'] == 200
    data = body['data']
    assert set(data.keys()) == {'revision', 'skills'}

    by_id = {item['skill_id']: item['fingerprint'] for item in data['skills']}
    assert by_id[skill_id] == 'fp-open-1'

    # revision 与 snapshot 同源一致（daemon 据 revision 比对是否需要 reconcile）。
    snapshot_ids, snapshot_rev = await get_common_skill_snapshot(client.session)
    assert data['revision'] == snapshot_rev
    assert [item['skill_id'] for item in data['skills']] == snapshot_ids


async def test_agent_profile_response_common_skill_ids_defaults_empty() -> None:
    """新字段默认空列表：旧调用方不传不炸，旧 runtime 收到多余字段可忽略（向后兼容）。"""
    profile = AgentProfileResponse(hasn_id='a_test', display_name='测试分身')
    assert profile.common_skill_ids == []
