"""技能市场 Agent 权威查询 Interface（DOC15-95 M1-1，真实 PostgreSQL）。"""

from __future__ import annotations

import hashlib
import uuid

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.api.router import agent as agent_marketplace_router
from backend.app.marketplace.model import (
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

_OWNER_USER_ID = 9_901_551
_OWNER_HASN_ID = 'h_marketplace_agent_interface_owner'
_AGENT_HASN_ID = 'a_marketplace_agent_interface_agent'

_APP = FastAPI()
_APP.include_router(agent_marketplace_router)


@_APP.exception_handler(BaseExceptionError)
async def _error_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.code,
        content={'code': exc.code, 'msg': str(exc.msg), 'data': exc.data},
    )


def _agent_payload() -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=_AGENT_HASN_ID,
        agent_name='marketplace-interface-test',
        owner_hasn_id=_OWNER_HASN_ID,
        owner_user_id=_OWNER_USER_ID,
        session_uuid='agent-marketplace-interface-test',
        expire_time=timezone.now() + timedelta(minutes=5),
    )


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
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
    namespace: str,
    slug: str,
    owner_user_id: int | None = None,
    status: str = 'published',
    visibility: str = 'public',
    source_type: str = 'github',
    category: str = 'engineering',
    tags: str = '["agent-interface", "alpha"]',
) -> str:
    skill_id = f'{namespace}/{slug}'
    digest = hashlib.sha256(f'{skill_id}@1.0.0'.encode()).hexdigest()
    session.add(
        MarketplaceSkill(
            skill_id=skill_id,
            namespace=namespace,
            slug=slug,
            user_id=owner_user_id,
            hasn_id=_OWNER_HASN_ID if owner_user_id == _OWNER_USER_ID else None,
            status=status,
            visibility=visibility,
            name=f'{namespace}-{slug}',
            name_zh=f'{namespace}-{slug}',
            description_zh='Agent Interface 查询测试技能',
            source_type=source_type,
            category=category,
            tags=tags,
            tags_zh=tags,
            is_private=visibility != 'public',
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


async def _seed_template(
    session,
    *,
    namespace: str,
    slug: str,
    template_type: str,
) -> str:
    template_id = f'{namespace}/{slug}'
    digest = f"sha256:{hashlib.sha256(f'{template_id}@1.0.0'.encode()).hexdigest()}"
    session.add(
        MarketplaceTemplate(
            template_id=template_id,
            namespace=namespace,
            slug=slug,
            status='published',
            visibility='public',
            template_type=template_type,
            name=f'{template_type}-{slug}',
            description='Agent Interface 查询测试模板',
            source_type='huanxing',
            category='engineering',
            tags='agent-interface,alpha',
            price=Decimal(0),
            is_private=False,
            is_official=True,
        )
    )
    session.add(
        MarketplaceTemplateVersion(
            template_id=template_id,
            version='1.0.0',
            bundle_slug=slug if template_type == 'skill_pack' else None,
            command_key=f'/{slug}' if template_type == 'skill_pack' else None,
            hermes_yaml=(
                f'name: {slug}\ndescription: 测试技能包\nskills:\n  - huanxing/example\n'
                if template_type == 'skill_pack'
                else None
            ),
            content_hash=digest,
            file_hash=digest.removeprefix('sha256:'),
            is_latest=True,
        )
    )
    await session.flush()
    return template_id


async def test_agent_query_routes_exist_without_caller_supplied_identity(client) -> None:
    openapi = _APP.openapi()
    expected = {
        '/api/v1/marketplace/agent/skills',
        '/api/v1/marketplace/agent/skills/{resource_id}',
        '/api/v1/marketplace/agent/templates',
        '/api/v1/marketplace/agent/templates/{resource_id}',
        '/api/v1/marketplace/agent/skill-packs',
        '/api/v1/marketplace/agent/skill-packs/{resource_id}',
    }
    assert expected <= set(openapi['paths'])
    for path in expected:
        parameters = openapi['paths'][path]['get'].get('parameters', [])
        names = {parameter['name'] for parameter in parameters}
        assert not names & {'agent_hasn_id', 'owner_hasn_id', 'owner_id', 'user_id'}


async def test_skill_search_filters_before_cursor_pagination_and_isolates_private_acl(client) -> None:
    tag = _tag()
    shared_slug = f'shared-{tag}'
    first_id = await _seed_skill(
        client.session,
        namespace=f'alpha-{tag}',
        slug=shared_slug,
        source_type='github',
    )
    second_id = await _seed_skill(
        client.session,
        namespace=f'beta-{tag}',
        slug=shared_slug,
        source_type='clawhub',
    )
    own_private_id = await _seed_skill(
        client.session,
        namespace=f'user/{_OWNER_HASN_ID}',
        slug=f'own-{tag}',
        owner_user_id=_OWNER_USER_ID,
        status='draft',
        visibility='private',
        source_type='user',
    )
    other_private_id = await _seed_skill(
        client.session,
        namespace=f'user/h_other_{tag}',
        slug=f'other-{tag}',
        owner_user_id=_OWNER_USER_ID + 1,
        status='draft',
        visibility='private',
        source_type='user',
    )

    first = await client.http.get(
        '/api/v1/marketplace/agent/skills',
        params={
            'query': tag,
            'tags': 'alpha',
            'language': 'zh-CN',
            'sort': 'updated',
            'limit': 2,
        },
    )
    assert first.status_code == 200, first.text
    page = first.json()['data']
    assert page['total'] == 3
    assert len(page['items']) == 2
    assert page['next_cursor'] is not None
    assert other_private_id not in {item['skill_id'] for item in page['items']}

    second = await client.http.get(
        '/api/v1/marketplace/agent/skills',
        params={'query': tag, 'tags': 'alpha', 'limit': 2, 'cursor': page['next_cursor']},
    )
    assert second.status_code == 200, second.text
    all_ids = {item['skill_id'] for item in page['items']} | {
        item['skill_id'] for item in second.json()['data']['items']
    }
    assert all_ids == {first_id, second_id, own_private_id}


async def test_skill_detail_requires_authoritative_identity_and_reports_ambiguity(client) -> None:
    tag = _tag()
    slug = f'ambiguous-{tag}'
    skill_id = await _seed_skill(client.session, namespace=f'one-{tag}', slug=slug)
    await _seed_skill(client.session, namespace=f'two-{tag}', slug=slug)

    detail = await client.http.get(f'/api/v1/marketplace/agent/skills/{skill_id}')
    assert detail.status_code == 200, detail.text
    assert detail.json()['data']['skill_id'] == skill_id
    assert detail.json()['data']['resource_uri'] == f'hasn://marketplace/skills/{skill_id}'

    ambiguous = await client.http.get(f'/api/v1/marketplace/agent/skills/{slug}')
    assert ambiguous.status_code == 409, ambiguous.text

    missing = await client.http.get(f'/api/v1/marketplace/agent/skills/missing/{tag}')
    assert missing.status_code == 404, missing.text


async def test_templates_and_skill_packs_are_independent_first_class_queries(client) -> None:
    tag = _tag()
    template_id = await _seed_template(
        client.session,
        namespace=f'huanxing-{tag}',
        slug=f'agent-{tag}',
        template_type='agent_template',
    )
    package_id = await _seed_template(
        client.session,
        namespace=f'huanxing-{tag}',
        slug=f'pack-{tag}',
        template_type='skill_pack',
    )

    templates = await client.http.get(
        '/api/v1/marketplace/agent/templates',
        params={'query': tag, 'limit': 20},
    )
    assert templates.status_code == 200, templates.text
    assert {item['template_id'] for item in templates.json()['data']['items']} == {template_id}

    packages = await client.http.get(
        '/api/v1/marketplace/agent/skill-packs',
        params={'query': tag, 'limit': 20},
    )
    assert packages.status_code == 200, packages.text
    assert {item['package_id'] for item in packages.json()['data']['items']} == {package_id}

    package = await client.http.get(
        f'/api/v1/marketplace/agent/skill-packs/{package_id}',
        params={'version': '1.0.0'},
    )
    assert package.status_code == 200, package.text
    assert package.json()['data']['package_id'] == package_id
    assert package.json()['data']['version'] == '1.0.0'
