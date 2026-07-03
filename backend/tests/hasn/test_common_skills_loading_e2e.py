"""公共技能加载机制 进程内 HTTP E2E（doc12，真实 PG，零 mock 业务）。

覆盖 doc12 的云端契约（断点 B/A 的修复）：
  1) GET /api/v1/hasn/agent/profile 把 is_common 公共技能叠加进 skills（COMMON ∪ agent.skills），
     非公共技能不进；并返回 common_skills_revision != '0'。
  2) 公共技能内容版本变化（新版本行）→ common_skills_revision 变（驱动 Runtime 重拉最新）。
  3) 公共技能成员增加 → common_skills_revision 变。
  4) GET /api/v1/hasn/agent/profile/revision 轻量端点同样带最新 common_skills_revision。

最小 app 挂真实 agent profile 路由 + 真实 PG 会话 + override agent JWT；经 ASGITransport 走完整
FastAPI HTTP 栈（依赖注入 + 统一信封）。事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_agent_profile import router as profile_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion
from backend.common.dataclasses import AgentTokenPayload
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(profile_router, prefix='/api/v1/hasn/agent')


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
    owner = f'h_owner_{tag}'
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_{tag}'
    own_skill = f'user/{owner}/own-skill-{tag}'
    common_skill = f'huanxing/search/common-{tag}'
    extra_common = f'huanxing/productivity/common2-{tag}'
    non_common = f'huanxing/x/non-common-{tag}'

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
            skills=[own_skill],
            profile_revision=3,
        )
    )
    # 一个公共技能（is_common，带最新版本行）+ 一个非公共技能（对照）。
    session.add_all([
        MarketplaceSkill(
            skill_id=common_skill, namespace='huanxing/search', slug=f'common-{tag}',
            name='公共技能', status='published', visibility='public', is_common=True,
        ),
        MarketplaceSkill(
            skill_id=non_common, namespace='huanxing/x', slug=f'non-common-{tag}',
            name='非公共技能', status='published', visibility='public', is_common=False,
        ),
        MarketplaceSkillVersion(skill_id=common_skill, version='1.0.0', is_latest=True),
        MarketplaceSkillVersion(skill_id=non_common, version='1.0.0', is_latest=True),
    ])
    await session.flush()

    async def _yield_session():
        yield session

    async def _auth_inject() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner,
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session,
            owner=owner, agent_hasn=agent_hasn, own_skill=own_skill,
            common_skill=common_skill, extra_common=extra_common, non_common=non_common, tag=tag,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _get_profile(client) -> dict:
    r = await client.get('/api/v1/hasn/agent/profile')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['code'] == 200, body  # 统一信封
    return body['data']


async def test_profile_overlays_common_skills_and_revision_tracks_changes(e2e) -> None:
    c = e2e.client

    # 1) profile 叠加公共技能：含公共 + 自装，不含非公共；revision != '0'
    data = await _get_profile(c)
    skills = data['skills']
    assert e2e.common_skill in skills, skills        # 公共技能被叠加
    assert e2e.own_skill in skills, skills            # Agent 自装保留
    assert e2e.non_common not in skills, skills       # 非公共不进
    rev1 = data['common_skills_revision']
    assert rev1 and rev1 != '0', rev1

    # 2) 公共技能内容版本升级（新 latest 版本行）→ revision 变
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.common_skill)
        .values(is_latest=False)
    )
    e2e.session.add(MarketplaceSkillVersion(skill_id=e2e.common_skill, version='2.0.0', is_latest=True))
    await e2e.session.flush()
    data2 = await _get_profile(c)
    rev2 = data2['common_skills_revision']
    assert rev2 != rev1, (rev1, rev2)                 # 内容版本变 → 自动重拉信标变
    assert e2e.common_skill in data2['skills']

    # 3) 公共技能成员增加 → revision 再变
    e2e.session.add_all([
        MarketplaceSkill(
            skill_id=e2e.extra_common, namespace='huanxing/productivity', slug=f'common2-{e2e.tag}',
            name='公共技能2', status='published', visibility='public', is_common=True,
        ),
        MarketplaceSkillVersion(skill_id=e2e.extra_common, version='1.0.0', is_latest=True),
    ])
    await e2e.session.flush()
    data3 = await _get_profile(c)
    rev3 = data3['common_skills_revision']
    assert rev3 not in (rev1, rev2), (rev1, rev2, rev3)
    assert e2e.extra_common in data3['skills'] and e2e.common_skill in data3['skills']

    # 4) 轻量 revision 端点同样带最新 common_skills_revision
    r = await c.get('/api/v1/hasn/agent/profile/revision')
    assert r.status_code == 200, r.text
    rbody = r.json()
    assert rbody['code'] == 200
    assert rbody['data']['common_skills_revision'] == rev3
    assert rbody['data']['profile_revision'] == 3


async def test_revision_stable_when_nothing_changes(e2e) -> None:
    """同状态多次读取 → revision 稳定（确定性，非随机/时间）。"""
    c = e2e.client
    a = await _get_profile(c)
    b = await _get_profile(c)
    assert a['common_skills_revision'] == b['common_skills_revision']


async def test_content_hash_change_bumps_revision_without_version_change(e2e) -> None:
    """doc14 §B3 核心修复：**版本不变（恒 1.0.0）、仅 content_hash 变** → revision 变。

    这正是 doc12 饿死的场景——官方技能 frontmatter 无 version、同步器恒赋 1.0.0，旧实现用
    version 当指纹时改正文 revision 纹丝不动、桌面端永不重拉。改用 content_hash 后此用例必须红→绿。
    """
    c = e2e.client
    # 给公共技能的 latest 版本行写一个初始 content_hash（version 保持 1.0.0）。
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.common_skill, MarketplaceSkillVersion.is_latest.is_(True))
        .values(content_hash='hash_v1')
    )
    await e2e.session.flush()
    rev1 = (await _get_profile(c))['common_skills_revision']

    # 只改 content_hash（模拟"改了 SKILL.md 正文"），version 仍是 1.0.0、不新增版本行。
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.common_skill, MarketplaceSkillVersion.is_latest.is_(True))
        .values(content_hash='hash_v2')
    )
    await e2e.session.flush()
    rev2 = (await _get_profile(c))['common_skills_revision']

    assert rev2 != rev1, (rev1, rev2)  # content_hash 变 → 修订号变 → 桌面端自动重拉（bug 修复字面验收）

    # content_hash 缺失时回落 file_hash/version（COALESCE）——clawhub 等无 content_hash 的技能仍可驱动。
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.common_skill, MarketplaceSkillVersion.is_latest.is_(True))
        .values(content_hash=None, file_hash='zip_abc')
    )
    await e2e.session.flush()
    rev3 = (await _get_profile(c))['common_skills_revision']
    assert rev3 not in (rev1, rev2), (rev1, rev2, rev3)


async def test_installed_skills_revision_tracks_self_installed_content(e2e) -> None:
    """doc14 §B4：Agent **自装技能内容**升级 → installed_skills_revision 变（独立于公共技能）。"""
    c = e2e.client
    data0 = await _get_profile(c)
    inst0 = data0['installed_skills_revision']
    common0 = data0['common_skills_revision']

    # 自装技能（own_skill，profile.skills 里非公共那部分）落一个带 content_hash 的版本行。
    e2e.session.add(
        MarketplaceSkillVersion(skill_id=e2e.own_skill, version='1.0.0', is_latest=True, content_hash='ih1')
    )
    await e2e.session.flush()
    data1 = await _get_profile(c)
    inst1 = data1['installed_skills_revision']
    assert inst1 != inst0, (inst0, inst1)                  # 自装技能加版本指纹 → installed 变
    assert data1['common_skills_revision'] == common0      # 公共技能修订号不受影响（解耦）

    # 仅改自装技能 content_hash（版本仍 1.0.0）→ installed 再变。
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.own_skill, MarketplaceSkillVersion.is_latest.is_(True))
        .values(content_hash='ih2')
    )
    await e2e.session.flush()
    inst2 = (await _get_profile(c))['installed_skills_revision']
    assert inst2 not in (inst0, inst1), (inst0, inst1, inst2)

    # 轻量 revision 端点同样带 installed_skills_revision。
    r = await c.get('/api/v1/hasn/agent/profile/revision')
    assert r.json()['data']['installed_skills_revision'] == inst2


async def test_profile_exposes_per_skill_content_fingerprints(e2e) -> None:
    """doc14 §C4：profile 出 skill_content_hashes 映射，供 hermes 只重下指纹变化的技能。

    指纹与 common/installed revision 同源（COALESCE content_hash→file_hash→version），
    缺版本行的技能不出现（hermes 据此回落总是重下）。
    """
    c = e2e.client
    # 初始：common_skill 版本无 content_hash/file_hash → 指纹回落 version '1.0.0'；
    # own_skill 还没版本行 → 不出现在映射里。
    data0 = await _get_profile(c)
    fp0 = data0['skill_content_hashes']
    assert fp0.get(e2e.common_skill) == '1.0.0', fp0
    assert e2e.own_skill not in fp0, fp0

    # 给 common_skill 写 content_hash → 映射改为该指纹（content_hash 优先于 version）。
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.common_skill, MarketplaceSkillVersion.is_latest.is_(True))
        .values(content_hash='cf_v1')
    )
    await e2e.session.flush()
    fp1 = (await _get_profile(c))['skill_content_hashes']
    assert fp1.get(e2e.common_skill) == 'cf_v1', fp1

    # 改 content_hash → 映射指纹随之变（hermes 据此判定该技能需重下）。
    await e2e.session.execute(
        update(MarketplaceSkillVersion)
        .where(MarketplaceSkillVersion.skill_id == e2e.common_skill, MarketplaceSkillVersion.is_latest.is_(True))
        .values(content_hash='cf_v2')
    )
    await e2e.session.flush()
    data2 = await _get_profile(c)
    assert data2['skill_content_hashes'].get(e2e.common_skill) == 'cf_v2'
    # 指纹与公共修订号同源：指纹变了，common_skills_revision 也必然变（不会悖论）。
    assert data2['common_skills_revision'] != data0['common_skills_revision']
