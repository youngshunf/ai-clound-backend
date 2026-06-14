from __future__ import annotations

import json

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.authentication import AuthCredentials
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.marketplace.api.router import admin as marketplace_admin
from backend.app.marketplace.api.router import app as marketplace_app
from backend.app.marketplace.api.router import open_api as marketplace_open
from backend.app.marketplace.api.router import publish as marketplace_publish
from backend.app.marketplace.api.router import webhook as marketplace_webhook
from backend.app.marketplace.api.v1.publish import PublishUser, verify_publish_api_key
from backend.app.marketplace.model.marketplace_download import MarketplaceDownload
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion
from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion
from backend.app.marketplace.service.clawhub_sync_service import ClawHubSyncService
from backend.app.marketplace.service.github_app_sync_service import github_app_sync_service
from backend.app.marketplace.service.github_sync_service import GitHubSyncService, github_sync_service
from backend.app.marketplace.service.translation_service import translation_service
from backend.app.marketplace.schema.marketplace_skill import CreateMarketplaceSkillParam, UpdateMarketplaceSkillParam
from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.app.marketplace.service.translation_service import TranslationService
from backend.app.router import router as app_router
from backend.common.exception import errors
from backend.common.exception.errors import BaseExceptionError
from backend.common.response.response_schema import response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.rbac import rbac_verify
from backend.database.db import get_db, get_db_transaction
from backend.utils.serializers import MsgSpecJSONResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


@dataclass
class _User:
    id: int = 1001
    hasn_id: str = 'h_test_owner_001'
    username: str = 'test-user'
    nickname: str = '测试用户'
    is_superuser: bool = True
    is_staff: bool = True


def _skill_zip(
    *,
    name: str = 'Insurance Advisor',
    description: str = 'Insurance planning helper',
    version: str = '1.2.3',
    tags: str = 'insurance, china, advisor',
) -> bytes:
    body = f"""---
name: {name}
description: {description}
version: {version}
tags: {tags}
category: finance
---

# {name}
"""
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as zf:
        zf.writestr('SKILL.md', body)
        zf.writestr('icon.svg', '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    return buffer.getvalue()


def _skill_zip_with_version(version: str) -> bytes:
    return _skill_zip(version=version)


def _template_zip(
    *,
    name: str = 'Insurance Workspace',
    description: str = 'Insurance agent template',
    version: str = '2.0.0',
    tags: str = 'insurance, agent',
) -> bytes:
    template_yaml = f"""name: {name}
description: {description}
version: {version}
category: finance
tags: {tags}
skills:
  - user/h_test_owner_001/insurance-advisor
"""
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as zf:
        zf.writestr('template.yaml', template_yaml)
        zf.writestr('SOUL.md', '# Soul\n')
        zf.writestr('AGENTS.md', '# Agent\n')
        zf.writestr('icon.svg', '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    return buffer.getvalue()


def _admin_skill_payload(
    *,
    skill_id: str = 'huanxing/admin/admin-created-skill',
    name: str = 'Admin Created Skill',
    description: str = 'Created by admin',
) -> dict[str, Any]:
    namespace, slug = skill_id.rsplit('/', 1)
    return {
        'skill_id': skill_id,
        'namespace': namespace,
        'slug': slug,
        'status': 'published',
        'visibility': 'public',
        'name': name,
        'name_en': name,
        'name_zh': name,
        'description_en': description,
        'description_zh': description,
        'source_language': 'en',
        'category': 'admin',
        'tags': '["admin"]',
        'source_type': 'official',
        'pricing_type': 'free',
        'price': '0',
        'is_private': False,
        'is_official': True,
        'download_count': 0,
    }


def _admin_template_payload(
    *,
    template_id: str = 'huanxing/admin/admin-created-template',
    name: str = 'Admin Created Template',
    description: str = 'Created by admin',
) -> dict[str, Any]:
    namespace, slug = template_id.rsplit('/', 1)
    return {
        'template_id': template_id,
        'namespace': namespace,
        'slug': slug,
        'status': 'published',
        'visibility': 'public',
        'template_type': 'agent_template',
        'name': name,
        'name_en': name,
        'name_zh': name,
        'description': description,
        'description_en': description,
        'description_zh': description,
        'source_language': 'en',
        'pricing_type': 'free',
        'price': '0',
        'is_private': False,
        'is_official': True,
        'download_count': 0,
        'category': 'admin',
        'tags': 'admin',
        'source_type': 'official',
        'skill_dependencies': 'huanxing/admin/admin-created-skill',
    }


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # marketplace 模型已落独立 PG schema hasn_marketplace（ADR-15 轴①）。SQLite 无 schema 概念，
    # 会把 `hasn_marketplace.` 当作 attached database 名而报 unknown database；
    # 用 schema_translate_map 把该 schema 翻译为 None（无 schema），DDL/DML 都退化到本地表名。
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        future=True,
        execution_options={'schema_translate_map': {'hasn_marketplace': None}},
    )
    async with engine.begin() as conn:
        for table in (
            MarketplaceSkill.__table__,
            MarketplaceSkillVersion.__table__,
            MarketplaceTemplate.__table__,
            MarketplaceTemplateVersion.__table__,
            MarketplaceDownload.__table__,
        ):
            original_type = table.c.id.type
            original_jsonb_types = [
                (column, column.type) for column in table.c if column.type.__class__.__name__ == 'JSONB'
            ]
            table.c.id.type = sa.Integer()
            for column, _column_type in original_jsonb_types:
                column.type = sa.JSON()
            try:
                await conn.run_sync(table.create)
            finally:
                table.c.id.type = original_type
                for column, column_type in original_jsonb_types:
                    column.type = column_type

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()

    await engine.dispose()


@pytest.fixture
def app(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])

    @test_app.middleware('http')
    async def inject_user(
        request: Request,
        call_next: Callable[[Request], Awaitable[MsgSpecJSONResponse]],
    ) -> MsgSpecJSONResponse:
        request.scope['user'] = _User()
        request.scope['auth'] = AuthCredentials(['authenticated'])
        return await call_next(request)

    @test_app.exception_handler(BaseExceptionError)
    async def app_exception_handler(_request: Request, exc: BaseExceptionError) -> MsgSpecJSONResponse:  # noqa: RUF029
        return MsgSpecJSONResponse(
            status_code=200,
            content=response_base.fail(res=SimpleNamespace(code=exc.code, msg=exc.msg), data=exc.data).model_dump(),
        )

    async def _session_override() -> AsyncIterator[AsyncSession]:  # noqa: RUF029
        yield db_session

    def _auth_override() -> None:
        return None

    async def _publish_user_override() -> PublishUser:  # noqa: RUF029
        return PublishUser(
            user_id=1001,
            hasn_id='h_test_owner_001',
            username='test-user',
            nickname='测试用户',
        )

    async def _upload_skill_package(**kwargs: Any) -> tuple[str, str, int]:  # noqa: RUF029
        content = kwargs['content']
        skill_id = kwargs['skill_id']
        version = kwargs['version']
        return f'https://cdn.test/{skill_id}/{version}.zip', 'hash-from-test', len(content)

    async def _upload_template_package(**kwargs: Any) -> tuple[str, str, int]:  # noqa: RUF029
        content = kwargs['content']
        template_id = kwargs['template_id']
        version = kwargs['version']
        return f'https://cdn.test/{template_id}/{version}.zip', 'hash-from-test', len(content)

    async def _upload_icon(**kwargs: Any) -> str:  # noqa: RUF029
        return f"https://cdn.test/{kwargs['item_type']}/{kwargs['item_id']}/icon.svg"

    test_app.dependency_overrides[get_db] = _session_override
    test_app.dependency_overrides[get_db_transaction] = _session_override
    test_app.dependency_overrides[DependsJwtAuth.dependency] = _auth_override
    test_app.dependency_overrides[verify_publish_api_key] = _publish_user_override
    monkeypatch.setattr(
        'backend.app.marketplace.storage.s3_storage.marketplace_storage_service.upload_skill_package',
        _upload_skill_package,
    )
    monkeypatch.setattr(
        'backend.app.marketplace.storage.s3_storage.marketplace_storage_service.upload_template_package',
        _upload_template_package,
    )
    monkeypatch.setattr(
        'backend.app.marketplace.storage.s3_storage.marketplace_storage_service.upload_icon',
        _upload_icon,
    )

    test_app.include_router(marketplace_app)
    test_app.include_router(marketplace_admin)
    test_app.include_router(marketplace_open)
    test_app.include_router(marketplace_publish)
    test_app.include_router(marketplace_webhook)
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _route_for(app: FastAPI, method: str, path: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f'Route not found: {method} {path}')


def test_marketplace_cloud_routes_are_canonical(client: TestClient) -> None:
    paths = {route.path for route in client.app.routes}

    assert '/api/v1/marketplace/open/skills/search' in paths
    assert '/api/v1/marketplace/open/skills/{resource_id:path}' in paths
    assert '/api/v1/marketplace/open/skills/{resource_id:path}/download' in paths
    assert '/api/v1/marketplace/open/templates/search' in paths
    assert '/api/v1/marketplace/open/templates/{resource_id:path}' in paths
    assert '/api/v1/marketplace/open/templates/{resource_id:path}/download' in paths
    assert '/api/v1/marketplace/open/trending/skills' in paths
    assert '/api/v1/marketplace/open/trending/templates' in paths
    assert '/api/v1/marketplace/open/categories' in paths
    assert '/api/v1/marketplace/open/categories/{category_slug}/skills' in paths
    assert '/api/v1/marketplace/open/categories/{category_slug}/templates' in paths
    assert '/api/v1/marketplace/app/skills/upload' in paths
    assert '/api/v1/marketplace/app/skills/{resource_id:path}/submit-review' in paths
    assert '/api/v1/marketplace/app/skills/{resource_id:path}/publish' in paths
    assert '/api/v1/marketplace/admin/skills' in paths
    assert '/api/v1/marketplace/admin/skills/{resource_id:path}' in paths
    assert '/api/v1/marketplace/admin/skills/{resource_id:path}/approve' in paths
    assert '/api/v1/marketplace/admin/skills/{resource_id:path}/suspend' in paths
    assert '/api/v1/marketplace/admin/templates' in paths
    assert '/api/v1/marketplace/admin/templates/{resource_id:path}' in paths
    assert '/api/v1/marketplace/admin/sync/github/templates' in paths
    assert '/api/v1/marketplace/webhook/github/skills' in paths
    assert '/api/v1/marketplace/webhook/github/templates' in paths
    assert '/api/v1/marketplace/publish/skill' in paths
    assert '/api/v1/marketplace/publish/template' in paths
    assert '/api/v1/marketplace/client/apps' not in paths
    assert '/api/v1/marketplace/download/skill/{skill_id:path}/{version}' not in paths
    assert '/api/v1/marketplace/download/app/{app_id:path}/{version}' not in paths
    assert '/api/v1/marketplace/search' not in paths
    assert '/api/v1/marketplace/publish/app' not in paths
    assert '/api/v1/marketplace/admin/sync/github/apps' not in paths
    assert '/api/v1/marketplace/admin/webhook/github/apps' not in paths
    assert '/api/v1/marketplace/admin/webhook/github/templates' not in paths
    assert '/api/v1/marketplace/admin/skills/legacy' not in paths
    assert '/api/v1/marketplace/admin/templates/legacy' not in paths


def test_admin_review_and_sync_routes_require_rbac(client: TestClient) -> None:
    paths = [
        '/api/v1/marketplace/admin/skills/{resource_id:path}/approve',
        '/api/v1/marketplace/admin/skills/{resource_id:path}/reject',
        '/api/v1/marketplace/admin/skills/{resource_id:path}/suspend',
        '/api/v1/marketplace/admin/templates/{resource_id:path}/approve',
        '/api/v1/marketplace/admin/templates/{resource_id:path}/reject',
        '/api/v1/marketplace/admin/templates/{resource_id:path}/suspend',
        '/api/v1/marketplace/admin/sync/github',
        '/api/v1/marketplace/admin/sync/github/templates',
        '/api/v1/marketplace/admin/sync/clawhub',
        '/api/v1/marketplace/admin/sync/cache',
    ]
    for path in paths:
        route = _route_for(client.app, 'POST' if not path.endswith('/cache') else 'DELETE', path)
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert rbac_verify in dependency_calls


def test_production_marketplace_routes_do_not_expose_non_contract_surfaces() -> None:
    production_app = FastAPI()
    production_app.include_router(app_router)
    paths = {route.path for route in production_app.routes}
    marketplace_paths = {path for path in paths if path.startswith('/api/v1/marketplace')}
    allowed_prefixes = (
        '/api/v1/marketplace/open',
        '/api/v1/marketplace/app',
        '/api/v1/marketplace/publish',
        '/api/v1/marketplace/admin',
        '/api/v1/marketplace/webhook',
    )

    assert marketplace_paths
    assert all(path.startswith(allowed_prefixes) for path in marketplace_paths)
    assert not any(path.startswith('/api/v1/marketplace/agent') for path in marketplace_paths)
    assert not any('/marketplace/sync/logs' in path for path in marketplace_paths)
    assert not any(path.startswith('/api/v1/marketplace/admin/skills/management') for path in marketplace_paths)


def test_namespaced_resource_id_columns_are_wide_enough() -> None:
    assert MarketplaceSkill.__table__.c.skill_id.type.length == 255
    assert MarketplaceSkillVersion.__table__.c.skill_id.type.length == 255
    assert MarketplaceTemplate.__table__.c.template_id.type.length == 255
    assert MarketplaceTemplateVersion.__table__.c.template_id.type.length == 255
    assert MarketplaceDownload.__table__.c.resource_id.type.length == 255

    sql_root = Path(__file__).resolve().parents[1] / 'backend' / 'sql' / 'marketplace'
    assert '"skill_id" varchar(255)' in (sql_root / 'tables' / 'marketplace_skill.sql').read_text()
    assert '"skill_id" varchar(255)' in (sql_root / 'tables' / 'marketplace_skill_version.sql').read_text()
    assert '"template_id" varchar(255)' in (sql_root / 'tables' / 'marketplace_template.sql').read_text()
    assert '"template_id" varchar(255)' in (sql_root / 'tables' / 'marketplace_template_version.sql').read_text()
    assert '"resource_id" varchar(255)' in (sql_root / 'tables' / 'marketplace_download.sql').read_text()
    migration_sql = (sql_root / 'migrations' / '2026-05-28-marketplace-user-status-fields.sql').read_text()
    assert 'idx_marketplace_skill_namespace_slug' in migration_sql
    assert 'idx_marketplace_template_namespace_slug' in migration_sql


def test_marketplace_skill_sync_timestamps_are_timezone_aware() -> None:
    assert MarketplaceSkill.__table__.c.synced_at.type.timezone is True
    assert MarketplaceSkill.__table__.c.translated_at.type.timezone is True


def test_marketplace_template_datetime_columns_are_timezone_aware() -> None:
    from backend.app.marketplace.model import MarketplaceTemplate

    # DB columns are timestamptz; the model must declare them tz-aware or
    # inserting tz-aware values (timezone.now()) fails under asyncpg.
    for column in ('reviewed_at', 'published_at', 'suspended_at', 'synced_at', 'translated_at'):
        assert MarketplaceTemplate.__table__.c[column].type.timezone is True, column


def test_marketplace_skill_has_bilingual_tag_columns() -> None:
    assert 'tags_en' in MarketplaceSkill.__table__.c
    assert 'tags_zh' in MarketplaceSkill.__table__.c

    sql_root = Path(__file__).resolve().parents[1] / 'backend' / 'sql' / 'marketplace'
    table_sql = (sql_root / 'tables' / 'marketplace_skill.sql').read_text()
    migration_sql = (sql_root / 'migrations' / '2026-05-29-marketplace-skill-bilingual-tags.sql').read_text()

    assert '"tags_en" text' in table_sql
    assert '"tags_zh" text' in table_sql
    assert 'ADD COLUMN IF NOT EXISTS "tags_en" text' in migration_sql
    assert 'ADD COLUMN IF NOT EXISTS "tags_zh" text' in migration_sql


def test_user_skill_publish_review_open_download_and_unpublish_e2e(client: TestClient) -> None:
    upload = client.post(
        '/api/v1/marketplace/app/skills/upload',
        files={'file': ('skill.zip', _skill_zip(), 'application/zip')},
        data={'slug': 'insurance-advisor'},
    )
    assert upload.status_code == 200, upload.text
    uploaded = upload.json()['data']
    assert uploaded['skill_id'] == 'user/h_test_owner_001/insurance-advisor'
    assert uploaded['namespace'] == 'user/h_test_owner_001'
    assert uploaded['slug'] == 'insurance-advisor'
    assert uploaded['user_id'] == 1001
    assert uploaded['hasn_id'] == 'h_test_owner_001'
    assert uploaded['status'] == 'draft'
    assert uploaded['visibility'] == 'private'

    draft_search = client.get('/api/v1/marketplace/open/skills/search', params={'keyword': 'Insurance'})
    assert draft_search.status_code == 200, draft_search.text
    assert draft_search.json()['items'] == []

    submit = client.post('/api/v1/marketplace/app/skills/user/h_test_owner_001/insurance-advisor/submit-review')
    assert submit.status_code == 200, submit.text
    assert submit.json()['data']['status'] == 'pending_review'

    approve = client.post(
        '/api/v1/marketplace/admin/skills/user/h_test_owner_001/insurance-advisor/approve',
        json={'review_note': 'ok'},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()['data']['status'] == 'published'
    assert approve.json()['data']['visibility'] == 'public'

    open_search = client.get('/api/v1/marketplace/open/skills/search', params={'keyword': 'Insurance'})
    assert open_search.status_code == 200, open_search.text
    search_data = open_search.json()
    assert search_data['total'] == 1
    assert search_data['items'][0]['skill_id'] == 'user/h_test_owner_001/insurance-advisor'

    detail = client.get('/api/v1/marketplace/open/skills/user/h_test_owner_001/insurance-advisor')
    assert detail.status_code == 200, detail.text
    assert detail.json()['skill_id'] == 'user/h_test_owner_001/insurance-advisor'

    download = client.get(
        '/api/v1/marketplace/open/skills/user/h_test_owner_001/insurance-advisor/download',
        follow_redirects=False,
    )
    assert download.status_code == 302, download.text
    assert download.headers['location'] == 'https://cdn.test/user/h_test_owner_001/insurance-advisor/1.2.3.zip'

    unpublish = client.post('/api/v1/marketplace/app/skills/user/h_test_owner_001/insurance-advisor/unpublish')
    assert unpublish.status_code == 200, unpublish.text
    assert unpublish.json()['data']['status'] == 'unpublished'

    hidden_search = client.get('/api/v1/marketplace/open/skills/search', params={'keyword': 'Insurance'})
    assert hidden_search.status_code == 200, hidden_search.text
    assert hidden_search.json()['items'] == []


def test_user_template_publish_review_open_download_and_unpublish_e2e(client: TestClient) -> None:
    upload = client.post(
        '/api/v1/marketplace/app/templates/upload',
        files={'file': ('template.zip', _template_zip(), 'application/zip')},
        data={'slug': 'insurance-workspace'},
    )
    assert upload.status_code == 200, upload.text
    uploaded = upload.json()['data']
    assert uploaded['template_id'] == 'user/h_test_owner_001/insurance-workspace'
    assert uploaded['namespace'] == 'user/h_test_owner_001'
    assert uploaded['slug'] == 'insurance-workspace'
    assert uploaded['status'] == 'draft'
    assert uploaded['visibility'] == 'private'

    draft_search = client.get('/api/v1/marketplace/open/templates/search', params={'keyword': 'Insurance'})
    assert draft_search.status_code == 200, draft_search.text
    assert draft_search.json()['items'] == []

    submit = client.post('/api/v1/marketplace/app/templates/user/h_test_owner_001/insurance-workspace/submit-review')
    assert submit.status_code == 200, submit.text
    assert submit.json()['data']['status'] == 'pending_review'

    approve = client.post(
        '/api/v1/marketplace/admin/templates/user/h_test_owner_001/insurance-workspace/approve',
        json={'review_note': 'ok'},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()['data']['status'] == 'published'
    assert approve.json()['data']['visibility'] == 'public'

    open_search = client.get('/api/v1/marketplace/open/templates/search', params={'keyword': 'Insurance'})
    assert open_search.status_code == 200, open_search.text
    search_data = open_search.json()
    assert search_data['total'] == 1
    assert search_data['items'][0]['template_id'] == 'user/h_test_owner_001/insurance-workspace'
    assert 'user/h_test_owner_001/insurance-advisor' in search_data['items'][0]['skill_dependencies']

    detail = client.get('/api/v1/marketplace/open/templates/user/h_test_owner_001/insurance-workspace')
    assert detail.status_code == 200, detail.text
    assert detail.json()['template_id'] == 'user/h_test_owner_001/insurance-workspace'

    download = client.get(
        '/api/v1/marketplace/open/templates/user/h_test_owner_001/insurance-workspace/download',
        follow_redirects=False,
    )
    assert download.status_code == 302, download.text
    assert download.headers['location'] == 'https://cdn.test/user/h_test_owner_001/insurance-workspace/2.0.0.zip'

    unpublish = client.post('/api/v1/marketplace/app/templates/user/h_test_owner_001/insurance-workspace/unpublish')
    assert unpublish.status_code == 200, unpublish.text
    assert unpublish.json()['data']['status'] == 'unpublished'

    hidden_search = client.get('/api/v1/marketplace/open/templates/search', params={'keyword': 'Insurance'})
    assert hidden_search.status_code == 200, hidden_search.text
    assert hidden_search.json()['items'] == []


def test_app_skill_metadata_update_delete_and_admin_suspend(client: TestClient) -> None:
    upload = client.post(
        '/api/v1/marketplace/app/skills/upload',
        files={'file': ('skill.zip', _skill_zip(), 'application/zip')},
        data={'slug': 'managed-skill'},
    )
    assert upload.status_code == 200, upload.text

    patch = client.patch(
        '/api/v1/marketplace/app/skills/user/h_test_owner_001/managed-skill',
        json={
            'name': 'Managed Skill',
            'description': 'Updated description',
            'category': 'ops',
            'tags': ['ops', 'agent'],
        },
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()['data']['name'] == 'Managed Skill'
    assert patch.json()['data']['category'] == 'ops'

    publish = client.post('/api/v1/marketplace/app/skills/user/h_test_owner_001/managed-skill/publish')
    assert publish.status_code == 200, publish.text
    assert publish.json()['data']['status'] == 'pending_review'

    approve = client.post('/api/v1/marketplace/admin/skills/user/h_test_owner_001/managed-skill/approve')
    assert approve.status_code == 200, approve.text

    suspend = client.post(
        '/api/v1/marketplace/admin/skills/user/h_test_owner_001/managed-skill/suspend',
        json={'suspend_reason': 'policy'},
    )
    assert suspend.status_code == 200, suspend.text
    assert suspend.json()['data']['status'] == 'suspended'
    assert suspend.json()['data']['visibility'] == 'private'

    hidden = client.get('/api/v1/marketplace/open/skills/search', params={'keyword': 'Managed'})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()['items'] == []

    delete = client.delete('/api/v1/marketplace/app/skills/user/h_test_owner_001/managed-skill')
    assert delete.status_code == 200, delete.text


def test_admin_skill_management_uses_namespaced_resource_id(client: TestClient) -> None:
    create = client.post('/api/v1/marketplace/admin/skills', json=_admin_skill_payload())
    assert create.status_code == 200, create.text
    created = create.json()['data']
    assert created['skill_id'] == 'huanxing/admin/admin-created-skill'
    assert created['namespace'] == 'huanxing/admin'
    assert created['slug'] == 'admin-created-skill'

    detail = client.get('/api/v1/marketplace/admin/skills/huanxing/admin/admin-created-skill')
    assert detail.status_code == 200, detail.text
    assert detail.json()['data']['skill_id'] == 'huanxing/admin/admin-created-skill'

    update_payload = _admin_skill_payload(name='Admin Updated Skill', description='Updated by admin')
    update = client.put('/api/v1/marketplace/admin/skills/huanxing/admin/admin-created-skill', json=update_payload)
    assert update.status_code == 200, update.text
    assert update.json()['data']['name'] == 'Admin Updated Skill'

    delete = client.delete('/api/v1/marketplace/admin/skills/huanxing/admin/admin-created-skill')
    assert delete.status_code == 200, delete.text

    missing = client.get('/api/v1/marketplace/admin/skills/huanxing/admin/admin-created-skill')
    assert missing.status_code == 200
    assert missing.json()['code'] == 404


def test_admin_template_management_uses_namespaced_resource_id(client: TestClient) -> None:
    create = client.post('/api/v1/marketplace/admin/templates', json=_admin_template_payload())
    assert create.status_code == 200, create.text
    created = create.json()['data']
    assert created['template_id'] == 'huanxing/admin/admin-created-template'
    assert created['namespace'] == 'huanxing/admin'
    assert created['slug'] == 'admin-created-template'

    detail = client.get('/api/v1/marketplace/admin/templates/huanxing/admin/admin-created-template')
    assert detail.status_code == 200, detail.text
    assert detail.json()['data']['template_id'] == 'huanxing/admin/admin-created-template'

    update_payload = _admin_template_payload(name='Admin Updated Template', description='Updated by admin')
    update = client.put(
        '/api/v1/marketplace/admin/templates/huanxing/admin/admin-created-template',
        json=update_payload,
    )
    assert update.status_code == 200, update.text
    assert update.json()['data']['name'] == 'Admin Updated Template'

    delete = client.delete('/api/v1/marketplace/admin/templates/huanxing/admin/admin-created-template')
    assert delete.status_code == 200, delete.text

    missing = client.get('/api/v1/marketplace/admin/templates/huanxing/admin/admin-created-template')
    assert missing.status_code == 200
    assert missing.json()['code'] == 404


def test_publish_api_uses_user_namespace_and_keeps_resources_private(client: TestClient) -> None:
    skill_response = client.post(
        '/api/v1/marketplace/publish/skill',
        files={'file': ('skill.zip', _skill_zip(name='Published By Key'), 'application/zip')},
        data={'slug': 'key-skill'},
    )
    assert skill_response.status_code == 200, skill_response.text
    skill_data = skill_response.json()['data']
    assert skill_data['id'] == 'user/h_test_owner_001/key-skill'
    assert skill_data['namespace'] == 'user/h_test_owner_001'
    assert skill_data['status'] == 'draft'
    assert skill_data['visibility'] == 'private'

    template_response = client.post(
        '/api/v1/marketplace/publish/template',
        files={'file': ('template.zip', _template_zip(name='Published Template'), 'application/zip')},
        data={'slug': 'key-template'},
    )
    assert template_response.status_code == 200, template_response.text
    template_data = template_response.json()['data']
    assert template_data['id'] == 'user/h_test_owner_001/key-template'
    assert template_data['namespace'] == 'user/h_test_owner_001'
    assert template_data['status'] == 'draft'
    assert template_data['visibility'] == 'private'

    old_app = client.post(
        '/api/v1/marketplace/publish/app',
        files={'file': ('template.zip', _template_zip(), 'application/zip')},
    )
    assert old_app.status_code == 404


@pytest.mark.asyncio
async def test_user_upload_requires_hasn_identity(db_session: AsyncSession) -> None:
    with pytest.raises(errors.AuthorizationError):
        await marketplace_skill_service.upload_user_skill(
            db=db_session,
            user_id=1001,
            hasn_id='',
            content=_skill_zip(),
            filename='skill.zip',
            slug='missing-hasn-skill',
        )

    with pytest.raises(errors.AuthorizationError):
        await marketplace_template_service.upload_user_template(
            db=db_session,
            user_id=1001,
            hasn_id='',
            content=_template_zip(),
            filename='template.zip',
            slug='missing-hasn-template',
        )


@pytest.mark.asyncio
async def test_open_browse_categories_and_trending_hide_private_resources(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    db_session.add_all([
        MarketplaceSkill(
            skill_id='huanxing/finance/public-skill',
            namespace='huanxing/finance',
            slug='public-skill',
            name_en='Public Skill',
            name_zh='Public Skill',
            description_en='Visible',
            description_zh='Visible',
            category='finance',
            tags='["finance"]',
            source_type='official',
            pricing_type='free',
            price=0,
            is_private=False,
            is_official=True,
            status='published',
            visibility='public',
            download_count=7,
        ),
        MarketplaceSkill(
            skill_id='huanxing/finance/private-skill',
            namespace='huanxing/finance',
            slug='private-skill',
            name_en='Private Skill',
            name_zh='Private Skill',
            description_en='Hidden',
            description_zh='Hidden',
            category='finance',
            tags='["finance"]',
            source_type='official',
            pricing_type='free',
            price=0,
            is_private=True,
            is_official=True,
            status='draft',
            visibility='private',
        ),
        MarketplaceTemplate(
            template_id='huanxing/finance/public-template',
            namespace='huanxing/finance',
            slug='public-template',
            template_type='agent_template',
            name='Public Template',
            name_en='Public Template',
            name_zh='Public Template',
            description='Visible',
            description_en='Visible',
            description_zh='Visible',
            category='finance',
            tags='finance',
            source_type='official',
            pricing_type='free',
            price=0,
            is_private=False,
            is_official=True,
            status='published',
            visibility='public',
            download_count=5,
            skill_dependencies='huanxing/finance/public-skill',
        ),
    ])
    await db_session.commit()

    categories = client.get('/api/v1/marketplace/open/categories')
    assert categories.status_code == 200, categories.text
    finance = next(item for item in categories.json()['items'] if item['category'] == 'finance')
    assert finance['skill_count'] == 1
    assert finance['template_count'] == 1

    skills = client.get('/api/v1/marketplace/open/categories/finance/skills')
    assert skills.status_code == 200, skills.text
    assert [item['skill_id'] for item in skills.json()['items']] == ['huanxing/finance/public-skill']

    templates = client.get('/api/v1/marketplace/open/categories/finance/templates')
    assert templates.status_code == 200, templates.text
    assert [item['template_id'] for item in templates.json()['items']] == ['huanxing/finance/public-template']

    trending_skills = client.get('/api/v1/marketplace/open/trending/skills')
    assert trending_skills.status_code == 200, trending_skills.text
    assert 'huanxing/finance/public-skill' in [item['skill_id'] for item in trending_skills.json()['items']]
    assert 'huanxing/finance/private-skill' not in [item['skill_id'] for item in trending_skills.json()['items']]

    trending_templates = client.get('/api/v1/marketplace/open/trending/templates')
    assert trending_templates.status_code == 200, trending_templates.text
    assert 'huanxing/finance/public-template' in [item['template_id'] for item in trending_templates.json()['items']]


@pytest.mark.asyncio
async def test_open_skill_resource_id_uses_last_slash_for_namespace(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(
        MarketplaceSkill(
            skill_id='clawhub/mnetfairy/ai-insurance-advisor',
            namespace='clawhub/mnetfairy',
            slug='ai-insurance-advisor',
            name_en='AI Insurance Advisor',
            name_zh='AI Insurance Advisor',
            description_en='Insurance helper',
            description_zh='Insurance helper',
            category='finance',
            tags='["insurance"]',
            source_type='clawhub',
            pricing_type='free',
            price=0,
            is_private=False,
            is_official=False,
            status='published',
            visibility='public',
        )
    )
    await db_session.commit()

    response = client.get('/api/v1/marketplace/open/skills/clawhub/mnetfairy/ai-insurance-advisor')
    assert response.status_code == 200, response.text
    assert response.json()['namespace'] == 'clawhub/mnetfairy'
    assert response.json()['slug'] == 'ai-insurance-advisor'


@pytest.mark.asyncio
async def test_open_skill_detail_rejects_legacy_bare_skill_id(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(
        MarketplaceSkill(
            skill_id='legacy-skill',
            namespace=None,
            slug='legacy-skill',
            name_en='Legacy Skill',
            name_zh='Legacy Skill',
            description_en='Legacy helper',
            description_zh='Legacy helper',
            category='legacy',
            tags='["legacy"]',
            source_type='official',
            pricing_type='free',
            price=0,
            is_private=False,
            is_official=True,
            status='published',
            visibility='public',
        )
    )
    await db_session.commit()

    response = client.get('/api/v1/marketplace/open/skills/legacy-skill')

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_open_skill_search_supports_source_type_namespace_and_sort_alias(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    db_session.add_all([
        MarketplaceSkill(
            skill_id='clawhub/mnetfairy/ai-insurance-advisor',
            namespace='clawhub/mnetfairy',
            slug='ai-insurance-advisor',
            name_en='AI Insurance Advisor',
            name_zh='AI Insurance Advisor',
            description_en='Insurance helper',
            description_zh='Insurance helper',
            category='finance',
            tags='["insurance"]',
            source_type='clawhub',
            pricing_type='free',
            price=0,
            is_private=False,
            is_official=False,
            status='published',
            visibility='public',
        ),
        MarketplaceSkill(
            skill_id='github/example/ai-insurance-advisor',
            namespace='github/example',
            slug='ai-insurance-advisor',
            name_en='AI Insurance Advisor',
            name_zh='AI Insurance Advisor',
            description_en='Insurance helper',
            description_zh='Insurance helper',
            category='finance',
            tags='["insurance"]',
            source_type='github',
            pricing_type='free',
            price=0,
            is_private=False,
            is_official=False,
            status='published',
            visibility='public',
        ),
    ])
    await db_session.commit()

    response = client.get(
        '/api/v1/marketplace/open/skills/search',
        params={
            'q': 'Insurance',
            'source_type': 'clawhub',
            'namespace': 'clawhub/mnetfairy',
            'sort': 'latest',
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data['total'] == 1
    assert data['items'][0]['skill_id'] == 'clawhub/mnetfairy/ai-insurance-advisor'


def test_skill_package_requires_skill_markdown_frontmatter(client: TestClient) -> None:
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as zf:
        zf.writestr('manifest.yaml', 'name: Legacy\n')
    response = client.post(
        '/api/v1/marketplace/app/skills/upload',
        files={'file': ('legacy.zip', buffer.getvalue(), 'application/zip')},
        data={'slug': 'legacy'},
    )
    assert response.status_code == 200
    assert response.json()['code'] == 400
    assert 'SKILL.md' in response.json()['msg']


def test_template_package_requires_description_even_with_display_name(client: TestClient) -> None:
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as zf:
        zf.writestr('template.yaml', 'display_name: No Description\nversion: 1.0.0\n')
        zf.writestr('SOUL.md', '# Soul\n')
        zf.writestr('AGENTS.md', '# Agent\n')

    response = client.post(
        '/api/v1/marketplace/app/templates/upload',
        files={'file': ('template.zip', buffer.getvalue(), 'application/zip')},
        data={'slug': 'missing-description'},
    )

    assert response.status_code == 200
    assert response.json()['code'] == 400
    assert 'description' in response.json()['msg']


def test_upload_rejects_unsafe_slug_and_version(client: TestClient) -> None:
    bad_slug = client.post(
        '/api/v1/marketplace/app/skills/upload',
        files={'file': ('skill.zip', _skill_zip(), 'application/zip')},
        data={'slug': '..'},
    )
    assert bad_slug.status_code == 200
    assert bad_slug.json()['code'] == 400

    bad_version = client.post(
        '/api/v1/marketplace/app/skills/upload',
        files={'file': ('skill.zip', _skill_zip_with_version('../secret'), 'application/zip')},
        data={'slug': 'safe-slug'},
    )
    assert bad_version.status_code == 200
    assert bad_version.json()['code'] == 400


def test_upload_rejects_hidden_system_files(client: TestClient) -> None:
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as zf:
        zf.writestr(
            'SKILL.md',
            '---\nname: Hidden File\n'
            'description: Should be rejected\n'
            '---\n'
            '# Skill\n',
        )
        zf.writestr('.DS_Store', 'binary junk')

    response = client.post(
        '/api/v1/marketplace/app/skills/upload',
        files={'file': ('hidden.zip', buffer.getvalue(), 'application/zip')},
        data={'slug': 'hidden-file'},
    )
    assert response.status_code == 200
    assert response.json()['code'] == 400


def test_marketplace_skill_sync_params_preserve_sync_metadata() -> None:
    synced_at = datetime(2026, 5, 29, 10, 30, 0)
    translated_at = datetime(2026, 5, 29, 10, 31, 0)
    payload = {
        'skill_id': 'github/anthropics-skills/git',
        'namespace': 'github/anthropics-skills',
        'slug': 'git',
        'name': 'Git Skill',
        'name_en': 'Git Skill',
        'name_zh': 'Git Skill',
        'description_en': 'Git helper',
        'description_zh': 'Git helper',
        'source_language': 'en',
        'tags': '["git"]',
        'tags_en': '["git"]',
        'tags_zh': '["Git"]',
        'source_type': 'github',
        'pricing_type': 'free',
        'price': Decimal('0'),
        'is_private': False,
        'is_official': False,
        'download_count': 13,
        'star_count': 8,
        'repo_path': 'github/anthropics-skills/skills/git',
        'git_commit_hash': 'abc123',
        'synced_at': synced_at,
        'translated_at': translated_at,
    }

    for schema_cls in (CreateMarketplaceSkillParam, UpdateMarketplaceSkillParam):
        data = schema_cls(**payload).model_dump()

        assert data['star_count'] == 8
        assert data['tags_en'] == '["git"]'
        assert data['tags_zh'] == '["Git"]'
        assert data['git_commit_hash'] == 'abc123'
        assert data['synced_at'] == synced_at
        assert data['translated_at'] == translated_at


def test_translation_service_parses_sse_chat_completion_chunks() -> None:
    raw = (
        'data: {"choices":[{"delta":{"content":"{\\"ok\\":"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"true}"}}]}\n\n'
        'data: [DONE]\n\n'
    )

    parsed = TranslationService._parse_sse_chat_response(raw)

    assert TranslationService._extract_chat_content(parsed) == '{"ok":true}'


@pytest.mark.asyncio
async def test_translation_service_falls_back_when_primary_model_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TranslationService()
    calls: list[str] = []

    class _Response:
        def __init__(self, model: str) -> None:
            self.status_code = 200
            self.headers = {'content-type': 'application/json'}
            self.text = ''
            self._model = model

        def json(self) -> dict[str, Any]:
            if self._model == 'primary-model':
                return {'choices': []}
            return {'choices': [{'message': {'content': '{"ok":true}'}}]}

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, _url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _Response:
            calls.append(json['model'])
            return _Response(json['model'])

    monkeypatch.setattr('backend.app.marketplace.service.translation_service.httpx.AsyncClient', _Client)
    monkeypatch.setattr('backend.app.marketplace.service.translation_service.settings.TRANSLATION_MODEL', 'primary-model')
    monkeypatch.setattr(
        'backend.app.marketplace.service.translation_service.settings.TRANSLATION_FALLBACK_MODEL',
        'fallback-model',
        raising=False,
    )

    content = await service._complete_chat([{'role': 'user', 'content': 'json'}], response_format={'type': 'json_object'})

    assert content == '{"ok":true}'
    assert calls == ['primary-model', 'primary-model', 'fallback-model']


@pytest.mark.asyncio
async def test_translation_service_normalizes_metadata_with_llm_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    service = TranslationService()

    async def _llm(
        _messages: list[dict[str, str]],
        max_tokens: int = 1000,
        *,
        response_format: dict[str, str] | None = None,
    ) -> str:
        assert response_format == {'type': 'json_object'}
        return json.dumps({
            'source_language': 'zh',
            'target_language': 'en',
            'name_en': 'Deals Finder',
            'name_zh': '虾虾优惠',
            'description_en': 'Cross-platform price comparison and coupon aggregation.',
            'description_zh': '跨平台电商比价与优惠券聚合工具。',
            'tags_en': ['price comparison', 'coupons', 'shopping'],
            'tags_zh': ['比价', '优惠券', '购物'],
        }, ensure_ascii=False)

    monkeypatch.setattr(service, '_complete_chat', _llm)

    normalized = await service.translate_skill_metadata(
        name='虾虾优惠',
        description='跨平台电商比价与优惠券聚合工具。',
        tag_hints=['1.6.2', 'coupon', 'shopping'],
    )

    assert normalized == {
        'source_language': 'zh',
        'target_language': 'en',
        'name_en': 'Deals Finder',
        'name_zh': '虾虾优惠',
        'description_en': 'Cross-platform price comparison and coupon aggregation.',
        'description_zh': '跨平台电商比价与优惠券聚合工具。',
        'tags_en': ['price comparison', 'coupons', 'shopping'],
        'tags_zh': ['比价', '优惠券', '购物'],
    }


def test_clawhub_filter_selects_top_100_by_rating_without_minimums() -> None:
    service = ClawHubSyncService()
    skills = [
        {
            'slug': f'skill-{index}',
            'stats': {'stars': index % 5, 'downloads': index},
            'updatedAt': index,
        }
        for index in range(130)
    ]

    selected = service._filter_skills(skills)

    assert len(selected) == 100
    assert [skill['slug'] for skill in selected[:4]] == [
        'skill-129',
        'skill-124',
        'skill-119',
        'skill-114',
    ]
    selected_keys = [
        (skill['stats']['stars'], skill['stats']['downloads'], skill['updatedAt'])
        for skill in selected
    ]
    assert selected_keys == sorted(selected_keys, reverse=True)
    assert 'skill-4' in {skill['slug'] for skill in selected}


@pytest.mark.asyncio
async def test_clawhub_sync_uses_owner_handle_and_llm_bilingual_tags(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ClawHubSyncService()

    async def _owner(_slug: str) -> str:
        return 'cheese9102'

    async def _category(_db: AsyncSession, _name: str, _description: str) -> str:
        return 'productivity'

    async def _download(**_kwargs: Any) -> str:
        return 'clawhub/cheese9102/xiaxiayouhui-deals'

    async def _translate(name: str | None = None, description: str | None = None, **_kwargs: Any) -> dict[str, str]:
        return {
            'name_en': name,
            'name_zh': name,
            'description_en': description,
            'description_zh': description,
            'source_language': 'en',
            'target_language': 'zh',
            'tags_en': ['price comparison', 'coupons', 'shopping'],
            'tags_zh': ['比价', '优惠券', '购物'],
        }

    monkeypatch.setattr(service, '_get_skill_owner', _owner)
    monkeypatch.setattr(service, '_classify_skill', _category)
    monkeypatch.setattr(service, '_download_skill_file', _download)
    monkeypatch.setattr(translation_service, 'translate_skill_metadata', _translate)

    await service._sync_skill(
        db_session,
        {
            'slug': 'xiaxiayouhui-deals',
            'displayName': 'Publish',
            'summary': '跨平台电商比价与优惠券聚合工具。',
            'stats': {'downloads': 42, 'stars': 7},
            'tags': {'latest': '1.6.2', 'coupon': '1.5.8', 'shopping': '1.5.8'},
            'latestVersion': {'version': '1.6.2', 'changelog': 'Initial', 'createdAt': 1780045762056},
        },
    )
    await db_session.flush()

    skill = await db_session.get(MarketplaceSkill, 1)
    assert skill is not None
    assert skill.skill_id == 'clawhub/cheese9102/xiaxiayouhui-deals'
    assert skill.namespace == 'clawhub/cheese9102'
    assert skill.author_name == 'cheese9102'
    assert skill.name == 'Publish'
    assert json.loads(skill.tags) == ['price comparison', 'coupons', 'shopping']
    assert json.loads(skill.tags_en) == ['price comparison', 'coupons', 'shopping']
    assert json.loads(skill.tags_zh) == ['比价', '优惠券', '购物']
    assert '1.6.2' not in skill.tags
    assert skill.repo_path == 'clawhub/cheese9102/xiaxiayouhui-deals'
    assert skill.star_count == 7
    assert skill.download_count == 42
    assert skill.synced_at is not None
    assert skill.translated_at is not None


@pytest.mark.asyncio
async def test_clawhub_owner_reads_value_page_owner_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ClawHubSyncService()

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {'value': {'page': [{'ownerHandle': 'adwilkinson'}]}}

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, _url: str) -> _Response:
            return _Response()

    monkeypatch.setattr('backend.app.marketplace.service.clawhub_sync_service.httpx.AsyncClient', _Client)

    assert await service._get_skill_owner('oneshot-ship') == 'adwilkinson'


@pytest.mark.asyncio
async def test_huanxing_hub_sync_uses_huanxing_and_github_roots_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / 'huanxing-skills' / 'finance' / 'advisor').mkdir(parents=True)
    (tmp_path / 'huanxing-skills' / 'finance' / 'advisor' / 'SKILL.md').write_text(
        '---\n'
        'name: Finance Advisor\n'
        'description: Helps finance work\n'
        'version: 1.4.0\n'
        'tags: finance, advisor\n'
        'category: ignored\n'
        '---\n'
        '# Skill\n',
        encoding='utf-8',
    )
    (tmp_path / 'huanxing-skills' / 'finance' / 'advisor' / 'manifest.yaml').write_text(
        'name: Wrong Manifest Name\nversion: 9.9.9\n',
        encoding='utf-8',
    )
    (tmp_path / 'huanxing-skills' / 'finance' / 'advisor' / 'icon.svg').write_text('<svg />', encoding='utf-8')

    (tmp_path / 'clawhub' / 'mnetfairy' / 'ai-insurance-advisor').mkdir(parents=True)
    (tmp_path / 'clawhub' / 'mnetfairy' / 'ai-insurance-advisor' / 'SKILL.md').write_text(
        '---\n'
        'name: AI Insurance Advisor\n'
        'description: Insurance helper\n'
        'version: 0.3.0\n'
        'tags: insurance, china, advisor\n'
        '---\n'
        '# Skill\n',
        encoding='utf-8',
    )

    (tmp_path / 'github' / 'anthropics-skills' / 'skills' / 'git').mkdir(parents=True)
    (tmp_path / 'github' / 'anthropics-skills' / 'skills' / 'git' / 'SKILL.md').write_text(
        '---\nname: Git Skill\ndescription: Git helper\ntags:\n  - git\n  - mcp\n---\n# Skill\n',
        encoding='utf-8',
    )
    (tmp_path / 'github' / 'anthropics-skills' / 'template').mkdir(parents=True)
    (tmp_path / 'github' / 'anthropics-skills' / 'template' / 'SKILL.md').write_text(
        '---\n'
        'name: template-skill\n'
        'description: Replace with description of the skill and when Claude should use it.\n'
        '---\n'
        '# Insert instructions below\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(github_sync_service, 'local_path', str(tmp_path))
    monkeypatch.setattr(
        github_sync_service,
        'repo',
        SimpleNamespace(head=SimpleNamespace(commit=SimpleNamespace(hexsha='abc123'))),
    )

    skills = await github_sync_service._scan_skills()
    by_id = {skill['skill_id']: skill for skill in skills}

    assert by_id['huanxing/finance/advisor']['namespace'] == 'huanxing/finance'
    assert by_id['huanxing/finance/advisor']['source_type'] == 'huanxing'
    assert by_id['huanxing/finance/advisor']['version'] == '1.4.0'
    assert by_id['huanxing/finance/advisor']['name'] == 'Finance Advisor'
    assert by_id['huanxing/finance/advisor']['tag_hints'] == ['finance', 'advisor']
    assert by_id['huanxing/finance/advisor']['icon_path'].name == 'icon.svg'
    assert 'clawhub/mnetfairy/ai-insurance-advisor' not in by_id
    assert 'github/anthropics-skills/template' not in by_id
    assert by_id['github/anthropics-skills/git']['namespace'] == 'github/anthropics-skills'
    assert by_id['github/anthropics-skills/git']['repo_path'] == 'github/anthropics-skills/skills/git'
    assert by_id['github/anthropics-skills/git']['tag_hints'] == ['git', 'mcp']


@pytest.mark.asyncio
async def test_github_and_huanxing_sync_use_llm_bilingual_tags(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = GitHubSyncService()

    async def _translate(name: str | None = None, description: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        return {
            'name_en': name,
            'name_zh': f'{name} 中文',
            'description_en': description,
            'description_zh': f'{description} 中文',
            'source_language': 'en',
            'target_language': 'zh',
            'tags_en': ['git', 'automation'],
            'tags_zh': ['Git', '自动化'],
        }

    monkeypatch.setattr(translation_service, 'translate_skill_metadata', _translate)
    async def _icon(_db: AsyncSession, _skill_data: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(service, '_resolve_icon_url', _icon)

    await service._sync_skill(
        db_session,
        {
            'skill_id': 'github/anthropics-skills/git',
            'namespace': 'github/anthropics-skills',
            'slug': 'git',
            'category': None,
            'repo_path': 'github/anthropics-skills/skills/git',
            'git_commit_hash': 'abc123',
            'icon_url': None,
            'icon_path': None,
            'emoji': None,
            'author_name': 'Anthropic',
            'tag_hints': ['git', 'mcp'],
            'pricing_type': 'free',
            'price': 0,
            'is_official': False,
            'is_private': False,
            'source_type': 'github',
            'source_language': 'en',
            'name': 'Git Skill',
            'description': 'Git helper',
            'version': '1.0.0',
            'changelog': 'Version 1.0.0',
            'versions': [{'version': '1.0.0', 'is_latest': True}],
        },
    )
    await db_session.flush()

    skill = await db_session.get(MarketplaceSkill, 1)
    assert skill is not None
    assert skill.tags == '["git", "automation"]'
    assert skill.tags_en == '["git", "automation"]'
    assert skill.tags_zh == '["Git", "自动化"]'


@pytest.mark.asyncio
async def test_github_sync_versions_default_published_at(db_session: AsyncSession) -> None:
    service = GitHubSyncService()

    await service._sync_skill_version(
        db_session,
        db_skill_id=1,
        skill_id='github/anthropics-skills/git',
        version_data={'version': '1.0.0', 'is_latest': True},
    )
    await db_session.flush()

    version = await db_session.get(MarketplaceSkillVersion, 1)
    assert version is not None
    assert version.skill_id == 'github/anthropics-skills/git'
    assert version.published_at is not None


def test_github_repository_update_refreshes_submodules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []

    class _Environment:
        def __init__(self, **kwargs: str) -> None:
            self.kwargs = kwargs

        def __enter__(self) -> None:
            calls.append(('env_enter', self.kwargs))

        def __exit__(self, *args: Any) -> None:
            calls.append(('env_exit', None))

    class _Origin:
        def pull(self) -> None:
            calls.append(('pull', None))

    class _Git:
        def custom_environment(self, **kwargs: str) -> _Environment:
            return _Environment(**kwargs)

        def submodule(self, *args: str) -> None:
            calls.append(('submodule', args))

    class _Repo:
        remotes = SimpleNamespace(origin=_Origin())
        git = _Git()

        def __init__(self, path: str) -> None:
            calls.append(('open', path))

    monkeypatch.setattr('backend.app.marketplace.service.github_sync_service.Repo', _Repo)
    service = GitHubSyncService()
    service.local_path = str(tmp_path)

    import asyncio

    asyncio.run(service._update_repository())

    assert calls == [
        ('open', str(tmp_path)),
        ('pull', None),
        ('submodule', ('sync', '--recursive')),
        ('env_enter', {'GIT_HTTP_VERSION': 'HTTP/1.1'}),
        ('submodule', ('update', '--init', '--recursive', '--remote')),
        ('env_exit', None),
    ]


def test_github_repository_clone_initializes_submodules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, Any]] = []

    class _Environment:
        def __init__(self, **kwargs: str) -> None:
            self.kwargs = kwargs

        def __enter__(self) -> None:
            calls.append(('env_enter', self.kwargs))

        def __exit__(self, *args: Any) -> None:
            calls.append(('env_exit', None))

    class _Git:
        def custom_environment(self, **kwargs: str) -> _Environment:
            return _Environment(**kwargs)

        def submodule(self, *args: str) -> None:
            calls.append(('submodule', args))

    class _Repo:
        git = _Git()

        @classmethod
        def clone_from(cls, url: str, path: str, **kwargs: Any) -> _Repo:
            calls.append(('clone', (url, path, kwargs)))
            return cls()

    monkeypatch.setattr('backend.app.marketplace.service.github_sync_service.Repo', _Repo)
    service = GitHubSyncService()
    service.local_path = str(tmp_path / 'missing-hub')
    service.repo_url = 'git@example.com:huanxing/huanxing-hub.git'

    import asyncio

    asyncio.run(service._update_repository())

    assert calls == [
        ('clone', (
            'git@example.com:huanxing/huanxing-hub.git',
            str(tmp_path / 'missing-hub'),
            {'multi_options': ['--recurse-submodules']},
        )),
        ('submodule', ('sync', '--recursive')),
        ('env_enter', {'GIT_HTTP_VERSION': 'HTTP/1.1'}),
        ('submodule', ('update', '--init', '--recursive', '--remote')),
        ('env_exit', None),
    ]


def test_github_webhook_treats_submodule_path_change_as_skill_change(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def _sync_from_github(_db: AsyncSession, force: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002
        calls.append(force)
        return {'success': True, 'synced': 2, 'failed': 0}

    monkeypatch.setattr(github_sync_service, 'sync_from_github', _sync_from_github)

    response = client.post(
        '/api/v1/marketplace/webhook/github/skills',
        headers={'X-GitHub-Event': 'push'},
        json={'commits': [{'modified': ['github/anthropics-skills']}]},
    )

    assert response.status_code == 200
    assert response.json()['data']['synced'] == 2
    assert calls == [True]


def test_github_webhook_ignores_clawhub_cache_changes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def _sync_from_github(_db: AsyncSession, force: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002
        calls.append(force)
        return {'success': True, 'synced': 1, 'failed': 0}

    monkeypatch.setattr(github_sync_service, 'sync_from_github', _sync_from_github)

    response = client.post(
        '/api/v1/marketplace/webhook/github/skills',
        headers={'X-GitHub-Event': 'push'},
        json={'commits': [{'modified': ['clawhub/mnetfairy/ai-insurance-advisor/SKILL.md']}]},
    )

    assert response.status_code == 200
    assert response.json()['data']['message'] == 'No skill changes detected'
    assert calls == []


@pytest.mark.asyncio
async def test_huanxing_hub_template_sync_uses_templates_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_dir = tmp_path / 'templates' / 'assistant' / 'research-agent'
    template_dir.mkdir(parents=True)
    (template_dir / 'template.yaml').write_text(
        'name: Research Agent\n'
        'description: Helps research work\n'
        'version: 0.2.0\n'
        'category: assistant\n'
        'tags:\n'
        '  - research\n'
        '  - agent\n'
        'skills:\n'
        '  - huanxing/productivity/search\n',
        encoding='utf-8',
    )
    (template_dir / 'icon.svg').write_text('<svg />', encoding='utf-8')

    monkeypatch.setattr(github_app_sync_service, 'local_path', str(tmp_path))
    monkeypatch.setattr(
        github_app_sync_service,
        'repo',
        SimpleNamespace(head=SimpleNamespace(commit=SimpleNamespace(hexsha='def456'))),
    )

    templates = await github_app_sync_service._scan_templates()

    assert len(templates) == 1
    template = templates[0]
    assert template['template_id'] == 'huanxing/assistant/research-agent'
    assert template['namespace'] == 'huanxing/assistant'
    assert template['slug'] == 'research-agent'
    assert template['source_repo_path'] == 'templates/assistant/research-agent'
    assert template['repo_path'] == 'templates/assistant/research-agent'
    assert template['skill_dependencies'] == 'huanxing/productivity/search'
    assert template['skill_dependencies_versioned'] == {'huanxing/productivity/search': '*'}
    assert template['icon_path'].name == 'icon.svg'
