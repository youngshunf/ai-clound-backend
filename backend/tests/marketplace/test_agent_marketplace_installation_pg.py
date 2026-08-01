"""技能市场 Agent 安装来源语义（DOC15-95 MA-3、MA-4、M1-2，真实 PostgreSQL）。"""

from __future__ import annotations

import hashlib
import uuid

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.router import agent as agent_hasn_router
from backend.app.hasn.model import HasnAgents
from backend.app.marketplace.api.router import agent as agent_marketplace_router
from backend.app.marketplace.model import (
    MarketplacePersonalSkill,
    MarketplaceSkill,
    MarketplaceSkillVersion,
    MarketplaceTemplate,
    MarketplaceTemplateVersion,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio

_OWNER_USER_ID = 9_901_552
_OWNER_HASN_ID = 'h_marketplace_installation_owner'
_AGENT_HASN_ID = 'a_marketplace_installation_agent'
_MIGRATION = (
    Path(__file__).parents[2]
    / 'sql/hasn/migrations/2026-08-02-marketplace-profile-origins-and-frozen-bundles.sql'
)

_APP = FastAPI()
_APP.include_router(agent_marketplace_router)
_APP.include_router(agent_hasn_router)


@_APP.exception_handler(BaseExceptionError)
async def _error_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.code,
        content={'code': exc.code, 'msg': str(exc.msg), 'data': exc.data},
    )


def _agent_payload() -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=_AGENT_HASN_ID,
        agent_name='marketplace-installation-test',
        owner_hasn_id=_OWNER_HASN_ID,
        owner_user_id=_OWNER_USER_ID,
        session_uuid='agent-marketplace-installation-test',
        expire_time=timezone.now() + timedelta(minutes=5),
    )


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    async def _yield_session():
        yield session

    async def _auth() -> AgentTokenPayload:
        return _agent_payload()

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _auth
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(http=http, session=session)
    finally:
        await http.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _tag() -> str:
    return uuid.uuid4().hex[:10]


async def _seed_skill(
    session,
    *,
    skill_id: str,
    is_common: bool = False,
) -> str:
    namespace, slug = skill_id.rsplit('/', 1)
    digest = hashlib.sha256(f'{skill_id}@1.0.0'.encode()).hexdigest()
    session.add(
        MarketplaceSkill(
            skill_id=skill_id,
            namespace=namespace,
            slug=slug,
            status='published',
            visibility='public',
            name=slug,
            source_type='huanxing',
            is_private=False,
            is_common=is_common,
        )
    )
    session.add(
        MarketplaceSkillVersion(
            skill_id=skill_id,
            version='1.0.0',
            content_hash=digest,
            file_hash=digest,
            is_latest=True,
        )
    )
    await session.flush()
    return skill_id


async def _seed_pack(
    session,
    *,
    package_id: str,
    version: str,
    member_skill_ids: list[str],
    is_latest: bool,
    stored_content_hash: str | None = None,
) -> tuple[str, str]:
    namespace, slug = package_id.rsplit('/', 1)
    hermes_yaml = '\n'.join([
        f'name: {slug}',
        'description: 安装来源测试技能包',
        'skills:',
        *[f'  - {skill_id}' for skill_id in member_skill_ids],
        '',
    ])
    digest = f'sha256:{hashlib.sha256(hermes_yaml.encode()).hexdigest()}'
    existing = (
        await session.execute(sa.select(MarketplaceTemplate).where(MarketplaceTemplate.template_id == package_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            MarketplaceTemplate(
                template_id=package_id,
                namespace=namespace,
                slug=slug,
                status='published',
                visibility='public',
                template_type='skill_pack',
                name=slug,
                description='安装来源测试技能包',
                source_type='huanxing',
                price=Decimal(0),
                is_private=False,
            )
        )
    session.add(
        MarketplaceTemplateVersion(
            template_id=package_id,
            version=version,
            bundle_slug=slug,
            command_key=f'/{slug}',
            hermes_yaml=hermes_yaml,
            content_hash=stored_content_hash or digest,
            file_hash=digest.removeprefix('sha256:'),
            is_latest=is_latest,
        )
    )
    await session.flush()
    return digest, hermes_yaml


async def _seed_agent(session, *, direct_skill_ids: list[str], skill_bundles: list[dict] | None = None) -> HasnAgents:
    row = HasnAgents(
        hasn_id=_AGENT_HASN_ID,
        star_id=f'{_tag()}#star',
        owner_id=_OWNER_HASN_ID,
        display_name='安装来源测试分身',
        agent_name='marketplace-installation-test',
        runtime_location='local',
        skills=direct_skill_ids,
        skill_bundles=skill_bundles or [],
        profile_revision=7,
        status='active',
        created_via='client',
    )
    session.add(row)
    await session.flush()
    return row


async def _seed_personal_skill(session, *, personal_skill_id: str, slug: str) -> None:
    content_hash = hashlib.sha256(personal_skill_id.encode()).hexdigest()
    session.add(
        MarketplacePersonalSkill(
            personal_skill_id=personal_skill_id,
            user_id=_OWNER_USER_ID,
            hasn_id=_OWNER_HASN_ID,
            slug=slug,
            name='个人技能',
            origin='user-upload',
            visibility='private',
            content_hash=content_hash,
            file_hash=content_hash,
            version=3,
        )
    )
    await session.flush()


async def _run_profile_migration(session) -> None:
    sql = _MIGRATION.read_text(encoding='utf-8')
    for statement in sql.split('-- statement-breakpoint'):
        if statement.strip():
            await session.execute(sa.text(statement))
    await session.flush()


async def test_profile_migration_canonicalizes_personal_and_freezes_legacy_bundle_without_losing_effective_set(
    client,
) -> None:
    tag = _tag()
    direct_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-direct')
    member_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-member')
    personal_id = f'01PERSONAL{tag.upper()}'
    personal_slug = f'personal-{tag}'
    await _seed_personal_skill(client.session, personal_skill_id=personal_id, slug=personal_slug)
    package_id = f'huanxing/{tag}-pack'
    package_hash, _ = await _seed_pack(
        client.session,
        package_id=package_id,
        version='1.0.0',
        member_skill_ids=[member_id],
        is_latest=True,
    )
    row = await _seed_agent(
        client.session,
        direct_skill_ids=[direct_id, personal_slug, member_id],
        skill_bundles=[{'template_id': package_id, 'version': '1.0.0'}],
    )
    before = {direct_id, personal_id, member_id}

    await _run_profile_migration(client.session)
    await client.session.refresh(row)

    assert row.skills == [direct_id, personal_id, member_id]
    assert set(row.skills) == before
    assert row.skill_bundles == [
        {
            'package_id': package_id,
            'version': '1.0.0',
            'content_hash': package_hash,
            'bundle_slug': f'{tag}-pack',
        }
    ]


async def test_profile_migration_keeps_unresolvable_bundle_marked_for_refreeze(client) -> None:
    row = await _seed_agent(
        client.session,
        direct_skill_ids=[],
        skill_bundles=[{'template_id': f'missing/{_tag()}', 'version': '0.9.0'}],
    )

    await _run_profile_migration(client.session)
    await client.session.refresh(row)

    assert row.skill_bundles is not None
    bundle = row.skill_bundles[0]
    assert bundle['template_id'].startswith('missing/')
    assert bundle['version'] == '0.9.0'
    assert bundle['needs_refreeze'] is True
    assert 'content_hash' not in bundle


async def test_profile_returns_direct_personal_common_and_cross_pack_origins(client) -> None:
    tag = _tag()
    common_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-common', is_common=True)
    direct_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-direct')
    shared_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-shared')
    personal_id = f'01PERSONAL{tag.upper()}'
    await _seed_personal_skill(client.session, personal_skill_id=personal_id, slug=f'personal-{tag}')
    first_package = f'huanxing/{tag}-pack-a'
    second_package = f'huanxing/{tag}-pack-b'
    first_hash, _ = await _seed_pack(
        client.session,
        package_id=first_package,
        version='1.0.0',
        member_skill_ids=[shared_id],
        is_latest=True,
    )
    second_hash, _ = await _seed_pack(
        client.session,
        package_id=second_package,
        version='2.0.0',
        member_skill_ids=[shared_id],
        is_latest=True,
    )
    await _seed_agent(
        client.session,
        direct_skill_ids=[direct_id, personal_id],
        skill_bundles=[
            {
                'package_id': first_package,
                'version': '1.0.0',
                'content_hash': first_hash,
                'bundle_slug': f'{tag}-pack-a',
            },
            {
                'package_id': second_package,
                'version': '2.0.0',
                'content_hash': second_hash,
                'bundle_slug': f'{tag}-pack-b',
            },
        ],
    )

    response = await client.http.get('/api/v1/hasn/agent/profile')

    assert response.status_code == 200, response.text
    profile = response.json()['data']
    assert direct_id in profile['direct_skill_ids']
    assert personal_id in profile['personal_skill_ids']
    assert common_id in profile['common_skill_ids']
    assert {common_id, direct_id, personal_id, shared_id} <= set(profile['skills'])
    assert profile['origins'][common_id] == ['common']
    assert profile['origins'][direct_id] == ['direct']
    assert profile['origins'][personal_id] == ['personal']
    assert profile['skill_content_hashes'][personal_id] == hashlib.sha256(personal_id.encode()).hexdigest()
    assert profile['skill_versions'][personal_id] == {
        'version': '3.0.0',
        'content_hash': hashlib.sha256(personal_id.encode()).hexdigest(),
    }
    assert profile['origins'][shared_id] == [
        f'skill_pack:{first_package}@1.0.0',
        f'skill_pack:{second_package}@2.0.0',
    ]

    installed = await client.http.get('/api/v1/marketplace/agent/installed')
    assert installed.status_code == 200, installed.text
    desired = installed.json()['data']
    assert desired['profile_revision'] == 7
    assert desired['direct_skill_ids'] == [direct_id]
    assert desired['personal_skill_ids'] == [personal_id]
    assert desired['skill_versions'][personal_id] == profile['skill_versions'][personal_id]
    assert {common_id, direct_id, personal_id, shared_id} <= set(desired['effective_skill_ids'])
    assert desired['origins'][shared_id] == [
        f'skill_pack:{first_package}@1.0.0',
        f'skill_pack:{second_package}@2.0.0',
    ]


async def test_skill_and_pack_mutations_are_idempotent_and_preserve_cross_sources(client) -> None:
    tag = _tag()
    direct_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-direct')
    shared_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-shared')
    package_id = f'huanxing/{tag}-pack'
    package_hash, _ = await _seed_pack(
        client.session,
        package_id=package_id,
        version='1.0.0',
        member_skill_ids=[shared_id],
        is_latest=True,
    )
    row = await _seed_agent(client.session, direct_skill_ids=[])

    first_skill = await client.http.put(f'/api/v1/marketplace/agent/installed/skills/{direct_id}')
    assert first_skill.status_code == 200, first_skill.text
    assert first_skill.json()['data']['changed'] is True
    first_revision = first_skill.json()['data']['profile_revision']

    repeated_skill = await client.http.put(f'/api/v1/marketplace/agent/installed/skills/{direct_id}')
    assert repeated_skill.status_code == 200, repeated_skill.text
    assert repeated_skill.json()['data']['changed'] is False
    assert repeated_skill.json()['data']['profile_revision'] == first_revision

    first_pack = await client.http.put(
        f'/api/v1/marketplace/agent/installed/skill-packs/{package_id}',
        params={'version': '1.0.0'},
    )
    assert first_pack.status_code == 200, first_pack.text
    assert first_pack.json()['data']['changed'] is True
    assert first_pack.json()['data']['bundle']['content_hash'] == package_hash
    assert first_pack.json()['data']['bundle']['hermes_yaml']
    assert first_pack.json()['data']['bundle']['member_skill_ids'] == [shared_id]
    assert first_pack.json()['data']['bundle']['member_skills'] == [
        {
            'skill_id': shared_id,
            'version': '1.0.0',
            'content_hash': hashlib.sha256(f'{shared_id}@1.0.0'.encode()).hexdigest(),
        }
    ]
    await client.session.refresh(row)
    assert row.skills == [direct_id]
    assert row.skill_bundles == [
        {
            'package_id': package_id,
            'version': '1.0.0',
            'content_hash': package_hash,
            'bundle_slug': f'{tag}-pack',
        }
    ]

    repeated_pack = await client.http.put(
        f'/api/v1/marketplace/agent/installed/skill-packs/{package_id}',
        params={'version': '1.0.0'},
    )
    assert repeated_pack.status_code == 200, repeated_pack.text
    assert repeated_pack.json()['data']['changed'] is False

    removed = await client.http.delete(
        f'/api/v1/marketplace/agent/installed/skill-packs/{package_id}',
        params={'version': '1.0.0'},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()['data']['changed'] is True
    assert shared_id not in removed.json()['data']['effective_skill_ids']
    await client.session.refresh(row)
    assert row.skills == [direct_id]
    assert row.skill_bundles == []

    repeated_remove = await client.http.delete(
        f'/api/v1/marketplace/agent/installed/skill-packs/{package_id}',
        params={'version': '1.0.0'},
    )
    assert repeated_remove.status_code == 200, repeated_remove.text
    assert repeated_remove.json()['data']['changed'] is False


async def test_skill_pack_install_freezes_definition_hash_instead_of_archive_manifest_hash(client) -> None:
    tag = _tag()
    member_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-member')
    package_id = f'huanxing/{tag}-pack'
    archive_manifest_hash = f'sha256:{hashlib.sha256(b"archive-manifest").hexdigest()}'
    definition_hash, _ = await _seed_pack(
        client.session,
        package_id=package_id,
        version='1.0.0',
        member_skill_ids=[member_id],
        is_latest=True,
        stored_content_hash=archive_manifest_hash,
    )
    row = await _seed_agent(client.session, direct_skill_ids=[])

    response = await client.http.put(
        f'/api/v1/marketplace/agent/installed/skill-packs/{package_id}',
        params={'version': '1.0.0'},
    )

    assert response.status_code == 200, response.text
    assert response.json()['data']['bundle']['content_hash'] == definition_hash
    await client.session.refresh(row)
    assert row.skill_bundles == [
        {
            'package_id': package_id,
            'version': '1.0.0',
            'content_hash': definition_hash,
            'bundle_slug': f'{tag}-pack',
        }
    ]


async def test_agent_cannot_uninstall_common_or_personal_skill(client) -> None:
    tag = _tag()
    common_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-common', is_common=True)
    personal_id = f'01PERSONAL{tag.upper()}'
    await _seed_personal_skill(client.session, personal_skill_id=personal_id, slug=f'personal-{tag}')
    row = await _seed_agent(client.session, direct_skill_ids=[personal_id])

    common = await client.http.delete(f'/api/v1/marketplace/agent/installed/skills/{common_id}')
    personal = await client.http.delete(f'/api/v1/marketplace/agent/installed/skills/{personal_id}')

    assert common.status_code == 409, common.text
    assert 'common' in common.text
    assert personal.status_code == 409, personal.text
    assert 'Owner Interface' in personal.text
    await client.session.refresh(row)
    assert row.profile_revision == 7
    assert row.skills == [personal_id]


async def test_pack_uninstall_without_version_reports_candidates_when_multiple_versions_are_installed(client) -> None:
    tag = _tag()
    member_id = await _seed_skill(client.session, skill_id=f'huanxing/{tag}-member')
    package_id = f'huanxing/{tag}-pack'
    first_hash, _ = await _seed_pack(
        client.session,
        package_id=package_id,
        version='1.0.0',
        member_skill_ids=[member_id],
        is_latest=False,
    )
    second_hash, _ = await _seed_pack(
        client.session,
        package_id=package_id,
        version='2.0.0',
        member_skill_ids=[member_id],
        is_latest=True,
    )
    await _seed_agent(
        client.session,
        direct_skill_ids=[],
        skill_bundles=[
            {
                'package_id': package_id,
                'version': '1.0.0',
                'content_hash': first_hash,
                'bundle_slug': f'{tag}-pack',
            },
            {
                'package_id': package_id,
                'version': '2.0.0',
                'content_hash': second_hash,
                'bundle_slug': f'{tag}-pack',
            },
        ],
    )

    response = await client.http.delete(f'/api/v1/marketplace/agent/installed/skill-packs/{package_id}')

    assert response.status_code == 409, response.text
    assert response.json()['data']['candidates'] == ['1.0.0', '2.0.0']
