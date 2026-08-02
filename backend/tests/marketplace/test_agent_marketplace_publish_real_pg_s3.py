"""技能市场 Agent 资产发布（DOC15-95 M1-3，真实 PostgreSQL + S3）。"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.app.marketplace.api.router import agent as agent_marketplace_router
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import async_db_session, get_db
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(agent_marketplace_router)


@_APP.exception_handler(BaseExceptionError)
async def _error_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.code,
        content={'code': exc.code, 'msg': str(exc.msg), 'data': exc.data},
    )


def _zip(files: dict[str, str]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(2026, 8, 2, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode())
    return target.getvalue()


def _skill_zip(slug: str, *, body: str = '技能正文') -> bytes:
    return _zip({
        'SKILL.md': (
            '---\n'
            f'name: {slug}\n'
            f'description: {slug} 发布测试\n'
            f'slug: {slug}\n'
            'version: 1.0.0\n'
            'tags: [test]\n'
            '---\n'
            f'{body}\n'
        ),
        'references/readme.md': '参考资料\n',
    })


def _template_zip(slug: str) -> bytes:
    return _zip({
        'template.yaml': (
            f'name: {slug}\n'
            f'display_name: {slug}\n'
            f'description: {slug} 模板发布测试\n'
            f'slug: {slug}\n'
            'version: 1.0.0\n'
            'template_type: agent_template\n'
        ),
        'SOUL.md': '# 人格\n保持严谨。\n',
    })


def _pack_zip(slug: str, member_skill_id: str) -> bytes:
    return _zip({
        'bundle.yaml': (
            f'name: {slug}\n'
            f'description: {slug} 技能包发布测试\n'
            f'command_key: /{slug}\n'
            'version: 1.0.0\n'
            'skills:\n'
            f'  - {member_skill_id}\n'
        ),
    })


async def _seed_identity_and_quota() -> SimpleNamespace:
    suffix = uuid.uuid4().hex[:10]
    owner = f'h_market_publish_{suffix}'
    agent = f'a_market_publish_{suffix}'
    user_id = 970_000_000 + int(suffix[:6], 16) % 20_000_000
    member_id = f'huanxing/test-member-{suffix}'
    member_hash = hashlib.sha256(member_id.encode()).hexdigest()
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star, :user_id, :nickname, 'active', '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {'owner': owner, 'star': f'mp{user_id}', 'user_id': user_id, 'nickname': f'发布测试_{suffix}'},
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_agents
                    (hasn_id, star_id, owner_id, display_name, agent_name, runtime_location,
                     skills, skill_bundles, profile_revision, api_key_hash,
                     status, created_via, created_time)
                VALUES
                    (:agent, :agent_star, :owner, :display_name, :agent_name, 'local',
                     '[]'::jsonb, '[]'::jsonb, 1, '', 'active', 'client', now())
                """
            ),
            {
                'agent': agent,
                'agent_star': f'{suffix}#star',
                'owner': owner,
                'display_name': '发布测试分身',
                'agent_name': f'publish-{suffix}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 104857600, 0, 0, 'admin_override', 'marketplace-publish-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_marketplace.marketplace_skill
                    (skill_id, namespace, slug, status, visibility, requested_visibility,
                     name, source_type, pricing_type, price, is_private, is_official,
                     is_common, download_count, star_count, created_time)
                VALUES
                    (:skill_id, 'huanxing', :slug, 'published', 'public', 'public',
                     :slug, 'huanxing', 'free', 0, false, false, false, 0, 0, now())
                """
            ),
            {'skill_id': member_id, 'slug': f'test-member-{suffix}'},
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_marketplace.marketplace_skill_version
                    (skill_id, version, content_hash, file_hash, is_latest, published_at, created_time)
                VALUES
                    (:skill_id, '1.0.0', :digest, :digest, true, now(), now())
                """
            ),
            {'skill_id': member_id, 'digest': member_hash},
        )
    return SimpleNamespace(
        owner=owner,
        agent=agent,
        user_id=user_id,
        member_id=member_id,
        asset_ids=[],
        marketplace_resources=[],
    )


async def _cleanup(identity: SimpleNamespace) -> None:
    async with async_db_session() as db:
        objects = (
            await db.execute(
                text('SELECT storage_id, object_key FROM hasn_storage_objects WHERE owner_hasn_id = :owner'),
                {'owner': identity.owner},
            )
        ).mappings().all()
        for obj in objects:
            await StorageService.delete_object(
                db,
                storage_id=int(obj['storage_id']),
                object_key=str(obj['object_key']),
            )
        versions = (
            await db.execute(
                text(
                    """
                    SELECT 'skill' AS kind, v.skill_id AS resource_id, v.version
                    FROM hasn_marketplace.marketplace_skill_version AS v
                    JOIN hasn_marketplace.marketplace_skill AS s ON s.skill_id = v.skill_id
                    WHERE s.hasn_id = :owner
                    UNION ALL
                    SELECT 'template', v.template_id, v.version
                    FROM hasn_marketplace.marketplace_template_version AS v
                    JOIN hasn_marketplace.marketplace_template AS t ON t.template_id = v.template_id
                    WHERE t.hasn_id = :owner
                    """
                ),
                {'owner': identity.owner},
            )
        ).mappings().all()
        for version in versions:
            item_type = 'skill' if version['kind'] == 'skill' else 'template'
            try:
                await marketplace_storage_service.delete_package(
                    db,
                    item_type=item_type,
                    item_id=str(version['resource_id']),
                    version=str(version['version']),
                )
            except Exception:
                pass

    async with async_db_session.begin() as db:
        await db.execute(
            text('DELETE FROM hasn_artifact_registration_outbox WHERE owner_hasn_id = :owner'),
            {'owner': identity.owner},
        )
        await db.execute(
            text(
                """
                DELETE FROM hasn_artifact_contributions
                WHERE artifact_id IN (
                    SELECT artifact_id FROM hasn_artifacts WHERE owner_hasn_id = :owner
                )
                """
            ),
            {'owner': identity.owner},
        )
        await db.execute(text('DELETE FROM hasn_artifacts WHERE owner_hasn_id = :owner'), {'owner': identity.owner})
        cleanup_statements = (
            """DELETE FROM hasn_marketplace.marketplace_agent_publish_request
               WHERE owner_hasn_id = :owner""",
            """DELETE FROM hasn_marketplace.marketplace_skill_version
               WHERE skill_id IN (
                   SELECT skill_id FROM hasn_marketplace.marketplace_skill WHERE hasn_id = :owner
               )""",
            """DELETE FROM hasn_marketplace.marketplace_template_version
               WHERE template_id IN (
                   SELECT template_id FROM hasn_marketplace.marketplace_template WHERE hasn_id = :owner
               )""",
            'DELETE FROM hasn_marketplace.marketplace_skill WHERE hasn_id = :owner',
            'DELETE FROM hasn_marketplace.marketplace_template WHERE hasn_id = :owner',
            'DELETE FROM hasn_marketplace.marketplace_skill_version WHERE skill_id = :member_id',
            'DELETE FROM hasn_marketplace.marketplace_skill WHERE skill_id = :member_id',
        )
        for statement in cleanup_statements:
            await db.execute(
                text(statement),
                {'owner': identity.owner, 'member_id': identity.member_id},
            )
        for table in (
            'hasn_storage_entries',
            'hasn_asset_bindings',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'), {'owner': identity.owner})  # noqa: S608
        await db.execute(text('DELETE FROM hasn_agents WHERE hasn_id = :agent'), {'agent': identity.agent})
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': identity.owner})


@pytest_asyncio.fixture
async def e2e():
    identity = await _seed_identity_and_quota()
    session = async_db_session()

    async def _yield_session():
        yield session

    async def _auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=identity.agent,
            agent_name='marketplace-publish-test',
            owner_hasn_id=identity.owner,
            owner_user_id=identity.user_id,
            session_uuid=f'publish-{identity.agent}',
            expire_time=timezone.now() + timedelta(minutes=5),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _auth
    identity.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    identity.storage = OwnerStorageService(async_db_session)
    try:
        yield identity
    finally:
        await identity.http.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await _cleanup(identity)


async def _upload_asset(e2e: SimpleNamespace, payload: bytes, *, name: str) -> str:
    stored = await e2e.storage.upload_bytes(
        owner_hasn_id=e2e.owner,
        data=payload,
        filename=name,
        mime='application/zip',
        category='user_upload',
        source_app='marketplace_publish_test',
        idempotency_key=f'{name}-{hashlib.sha256(payload).hexdigest()[:16]}',
    )
    e2e.asset_ids.append(stored.asset_id)
    return f'hasn://asset/{stored.asset_id}'


async def test_publish_routes_accept_only_asset_contract_and_trusted_headers(e2e) -> None:
    expected = {
        '/api/v1/marketplace/agent/publish/skills',
        '/api/v1/marketplace/agent/publish/templates',
        '/api/v1/marketplace/agent/publish/skill-packs',
    }
    openapi = _APP.openapi()
    assert expected <= set(openapi['paths'])
    for path in expected:
        operation = openapi['paths'][path]['post']
        parameter_names = {parameter['name'] for parameter in operation.get('parameters', [])}
        assert 'Idempotency-Key' in parameter_names
        assert 'X-Hasn-Work-Session-Id' in parameter_names
        schema_ref = operation['requestBody']['content']['application/json']['schema']['$ref']
        schema_name = schema_ref.rsplit('/', 1)[-1]
        body_fields = set(openapi['components']['schemas'][schema_name]['properties'])
        assert body_fields == {'asset_uri', 'changelog', 'visibility', 'submit_review'}
        assert not body_fields & {'agent_hasn_id', 'owner_hasn_id', 'user_id', 'content_hash', 'file_hash'}


async def test_skill_publish_replays_same_key_and_rejects_changed_content_without_duplicate(e2e) -> None:
    slug = f'skill-{uuid.uuid4().hex[:8]}'
    first_asset = await _upload_asset(e2e, _skill_zip(slug), name=f'{slug}.zip')
    changed_asset = await _upload_asset(
        e2e,
        _skill_zip(f'{slug}-changed', body='内容发生变化'),
        name=f'{slug}-changed.zip',
    )
    headers = {
        'Idempotency-Key': f'publish-{uuid.uuid4().hex}',
        'X-Hasn-Work-Session-Id': f'ws_{uuid.uuid4().hex[:20]}',
    }

    first = await e2e.http.post(
        '/api/v1/marketplace/agent/publish/skills',
        headers=headers,
        json={
            'asset_uri': first_asset,
            'changelog': '首次发布',
            'visibility': 'public',
            'submit_review': False,
        },
    )
    assert first.status_code == 200, first.text
    result = first.json()['data']
    assert result['resource_id'] == f'user/{e2e.owner}/{slug}'
    assert result['resource_uri'] == f'hasn://marketplace/skills/user/{e2e.owner}/{slug}'
    assert result['asset_uri'] == first_asset
    assert result['status'] == 'draft'
    assert result['visibility'] == 'private'
    assert result['requested_visibility'] == 'public'
    assert result['file_hash'] == hashlib.sha256(_skill_zip(slug)).hexdigest()
    assert result['content_hash']

    async with async_db_session() as db:
        artifact = (
            await db.execute(
                text(
                    """
                    SELECT resource_kind, resource_uri, session_id, dispatch_id, origin_ref
                    FROM hasn_artifacts
                    WHERE owner_hasn_id = :owner AND agent_hasn_id = :agent
                    """
                ),
                {'owner': e2e.owner, 'agent': e2e.agent},
            )
        ).mappings().one()
    assert dict(artifact) == {
        'resource_kind': 'marketplace.skill',
        'resource_uri': result['resource_uri'],
        'session_id': headers['X-Hasn-Work-Session-Id'],
        'dispatch_id': headers['Idempotency-Key'],
        'origin_ref': f'resource:marketplace:skill:{result["resource_id"]}',
    }

    replay = await e2e.http.post(
        '/api/v1/marketplace/agent/publish/skills',
        headers=headers,
        json={
            'asset_uri': first_asset,
            'changelog': '首次发布',
            'visibility': 'public',
            'submit_review': False,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()['data'] == result

    conflict = await e2e.http.post(
        '/api/v1/marketplace/agent/publish/skills',
        headers=headers,
        json={
            'asset_uri': changed_asset,
            'visibility': 'private',
            'submit_review': False,
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()['data']['resource_id'] == result['resource_id']
    assert conflict.json()['data']['content_hash'] == result['content_hash']

    async with async_db_session() as db:
        counts = (
            await db.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM hasn_marketplace.marketplace_agent_publish_request
                         WHERE agent_hasn_id = :agent AND resource_kind = 'skill') AS requests,
                        (SELECT count(*) FROM hasn_marketplace.marketplace_skill
                         WHERE hasn_id = :owner) AS resources
                    """
                ),
                {'agent': e2e.agent, 'owner': e2e.owner},
            )
        ).mappings().one()
    assert dict(counts) == {'requests': 1, 'resources': 1}


async def test_submit_review_uses_production_session_boundary(e2e) -> None:
    """生产会话依赖允许服务先提交草稿，再在新事务中完成提审。"""
    slug = f'session-boundary-{uuid.uuid4().hex[:8]}'
    asset_uri = await _upload_asset(e2e, _skill_zip(slug), name=f'{slug}.zip')
    session_override = _APP.dependency_overrides.pop(get_db)
    try:
        response = await e2e.http.post(
            '/api/v1/marketplace/agent/publish/skills',
            headers={'Idempotency-Key': f'session-boundary-{uuid.uuid4().hex}'},
            json={
                'asset_uri': asset_uri,
                'visibility': 'public',
                'submit_review': True,
            },
        )
    finally:
        _APP.dependency_overrides[get_db] = session_override

    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['resource_id'] == f'user/{e2e.owner}/{slug}'
    assert result['status'] == 'pending_review'
    assert result['review_submission']['status'] == 'submitted'


async def test_publish_rejects_foreign_owner_asset(e2e) -> None:
    other = await _seed_identity_and_quota()
    try:
        storage = OwnerStorageService(async_db_session)
        payload = _skill_zip(f'foreign-{uuid.uuid4().hex[:8]}')
        stored = await storage.upload_bytes(
            owner_hasn_id=other.owner,
            data=payload,
            filename='foreign.zip',
            mime='application/zip',
            category='user_upload',
            source_app='marketplace_publish_test',
            idempotency_key=f'foreign-{uuid.uuid4().hex}',
        )
        response = await e2e.http.post(
            '/api/v1/marketplace/agent/publish/skills',
            headers={'Idempotency-Key': f'foreign-{uuid.uuid4().hex}'},
            json={'asset_uri': f'hasn://asset/{stored.asset_id}'},
        )
        assert response.status_code == 403, response.text
    finally:
        await _cleanup(other)


async def test_template_and_skill_pack_publish_use_owner_namespace_and_submit_review(e2e) -> None:
    template_slug = f'template-{uuid.uuid4().hex[:8]}'
    pack_slug = f'pack-{uuid.uuid4().hex[:8]}'
    template_asset = await _upload_asset(e2e, _template_zip(template_slug), name=f'{template_slug}.zip')
    pack_asset = await _upload_asset(
        e2e,
        _pack_zip(pack_slug, e2e.member_id),
        name=f'{pack_slug}.zip',
    )
    template_headers = {
        'Idempotency-Key': f'template-{uuid.uuid4().hex}',
        'X-Hasn-Work-Session-Id': f'ws_{uuid.uuid4().hex[:20]}',
    }
    pack_headers = {
        'Idempotency-Key': f'pack-{uuid.uuid4().hex}',
        'X-Hasn-Work-Session-Id': f'ws_{uuid.uuid4().hex[:20]}',
    }

    template = await e2e.http.post(
        '/api/v1/marketplace/agent/publish/templates',
        headers=template_headers,
        json={'asset_uri': template_asset, 'visibility': 'public', 'submit_review': True},
    )
    pack = await e2e.http.post(
        '/api/v1/marketplace/agent/publish/skill-packs',
        headers=pack_headers,
        json={'asset_uri': pack_asset, 'visibility': 'public', 'submit_review': True},
    )

    assert template.status_code == 200, template.text
    assert pack.status_code == 200, pack.text
    template_result = template.json()['data']
    pack_result = pack.json()['data']
    assert template_result['resource_id'] == f'user/{e2e.owner}/{template_slug}'
    assert template_result['status'] == 'pending_review'
    assert template_result['visibility'] == 'private'
    assert template_result['review_submission']['status'] == 'submitted'
    assert pack_result['resource_id'] == f'user/{e2e.owner}/{pack_slug}'
    assert pack_result['status'] == 'pending_review'
    assert pack_result['visibility'] == 'private'
    assert pack_result['review_submission']['status'] == 'submitted'
    assert pack_result['member_skill_ids'] == [e2e.member_id]

    async with async_db_session() as db:
        artifacts = (
            await db.execute(
                text(
                    """
                    SELECT resource_kind, resource_uri, session_id, dispatch_id, origin_ref, action
                    FROM hasn_artifacts
                    WHERE owner_hasn_id = :owner AND agent_hasn_id = :agent
                    ORDER BY resource_kind
                    """
                ),
                {'owner': e2e.owner, 'agent': e2e.agent},
            )
        ).mappings().all()
    assert [dict(artifact) for artifact in artifacts] == [
        {
            'resource_kind': 'marketplace.skill_pack',
            'resource_uri': pack_result['resource_uri'],
            'session_id': pack_headers['X-Hasn-Work-Session-Id'],
            'dispatch_id': pack_headers['Idempotency-Key'],
            'origin_ref': f'resource:marketplace:skill_pack:{pack_result["resource_id"]}',
            'action': 'update',
        },
        {
            'resource_kind': 'marketplace.template',
            'resource_uri': template_result['resource_uri'],
            'session_id': template_headers['X-Hasn-Work-Session-Id'],
            'dispatch_id': template_headers['Idempotency-Key'],
            'origin_ref': f'resource:marketplace:template:{template_result["resource_id"]}',
            'action': 'update',
        },
    ]
