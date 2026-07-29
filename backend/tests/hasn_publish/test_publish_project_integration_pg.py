"""Publish 项目感知、联邦挂靠与稳定资源 URI 契约测试。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_artifact_contributions import HasnArtifactContributions
from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.app.hasn_publish.manifest import PUBLISH_AI_NATIVE_MANIFEST, build_publish_app
from backend.app.hasn_publish.model.site import Site
from backend.app.hasn_publish.service import project_linkage as _publish_project_linkage  # noqa: F401
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """真实 PostgreSQL 会话；用例结束回滚，不污染开发库。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


def test_publish_manifest_and_workbench_are_project_aware() -> None:
    """Publish 是可选挂靠的平台项目应用，资源描述符仍以 site 云端 ID 建 URI。"""
    manifest = PUBLISH_AI_NATIVE_MANIFEST
    assert manifest['project_aware'] is True
    assert manifest['project_required'] is False
    assert manifest['project_integration'] == 'project_aware'
    assert ai_native_app_registry.validate_manifest(manifest).valid is True

    app = build_publish_app()
    assert app.project_aware is True
    assert app.project_required is False

    descriptor = ai_native_app_registry.resource_descriptor('publish', 'publish.site')
    assert descriptor is not None
    assert descriptor.build_uri('42') == 'hasn://publish/sites/42'
    assert descriptor.open.route_template == '/apps/publish/sites/:id'


def test_publish_site_linkage_adapter_registered() -> None:
    """站点容器通过统一注册表挂靠，禁止项目服务跨 schema 散写。"""
    assert hasattr(Site, 'platform_project_id')
    adapter = project_linkage_registry.get('publish/sites')
    assert adapter is not None
    assert adapter.model is Site
    assert adapter.owner_column == 'owner_id'
    assert adapter.attach_column == 'platform_project_id'
    assert adapter.id_is_uuid is False
    assert adapter.is_container is True
    assert adapter.app_id == 'publish'
    assert adapter.kind == 'publish.site'
    assert adapter.title_column == 'title'
    assert adapter.deleted_column == 'deleted_time'


@pytest.mark.asyncio
async def test_publish_create_inherits_only_owned_active_project(session) -> None:
    """create 只能继承同 Owner 的 active 项目，空项目保持兼容可选挂靠。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_{tag}'
    other_owner = f'h_publish_other_{tag}'
    active = HasnProject(owner_id=owner, name='活动项目', status='active')
    archived = HasnProject(owner_id=owner, name='归档项目', status='archived')
    foreign = HasnProject(owner_id=other_owner, name='他人项目', status='active')
    session.add_all([active, archived, foreign])
    await session.flush()

    created = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=str(active.id),
        title='项目落地页',
        asset_id=f'asset_{tag}',
        source_app='growth',
        source_ref=f'growth:{tag}',
    )
    assert created['site']['platform_project_id'] == str(active.id)
    row = await session.get(Site, created['site']['id'])
    assert row is not None and row.platform_project_id == active.id

    detached = await publish_service.create_site(
        session,
        owner_id=owner,
        title='未挂项目站点',
        asset_id=f'asset_detached_{tag}',
    )
    assert detached['site']['platform_project_id'] is None

    with pytest.raises(errors.NotFoundError, match='平台项目不存在或不属于你'):
        await publish_service.create_site(
            session,
            owner_id=owner,
            platform_project_id=str(foreign.id),
            title='越权站点',
            asset_id=f'asset_foreign_{tag}',
        )
    with pytest.raises(errors.ConflictError, match='项目已归档'):
        await publish_service.create_site(
            session,
            owner_id=owner,
            platform_project_id=str(archived.id),
            title='归档项目站点',
            asset_id=f'asset_archived_{tag}',
        )
    with pytest.raises(errors.RequestError, match='平台项目 ID'):
        await publish_service.create_site(
            session,
            owner_id=owner,
            platform_project_id='not-a-uuid',
            title='非法项目站点',
            asset_id=f'asset_invalid_{tag}',
        )


@pytest.mark.asyncio
async def test_publish_linkage_and_project_flow_use_cloud_site_id(session) -> None:
    """显式挂靠、ACL 与项目产物流都使用云端 site.id，不接受本地或跨 Owner 资源。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_link_{tag}'
    intruder = f'h_publish_intruder_{tag}'
    project = HasnProject(owner_id=owner, name='发布项目', status='active')
    site = Site(
        owner_id=owner,
        kind='page',
        title='稳定站点',
        slug=f'pub-{tag}',
        source_app='growth',
        status='active',
        visibility='public',
    )
    session.add_all([project, site])
    await session.flush()

    uri = f'hasn://publish/sites/{site.id}'
    artifact = HasnArtifacts(
        artifact_id=f'art_{uuid.uuid4().hex[:16]}',
        agent_hasn_id=f'a_{tag}',
        owner_hasn_id=owner,
        artifact_key=f'{uri}#{tag}',
        artifact_kind='resource',
        kind='resource',
        title='发布站点产物',
        resource_uri=uri,
        source_kind='app_write',
        action='create',
        status='active',
    )
    session.add(artifact)
    await session.flush()
    session.add(
        HasnArtifactContributions(
            contribution_id=f'con_{uuid.uuid4().hex[:20]}',
            artifact_id=artifact.artifact_id,
            owner_hasn_id=owner,
            agent_hasn_id=f'a_{tag}',
            action='create',
            source_kind='app_write',
            idempotency_key=f'publish-link:{tag}',
        )
    )
    await session.flush()

    with pytest.raises(errors.NotFoundError):
        await project_linkage_registry.link(
            session,
            owner=intruder,
            resource_uri=uri,
            project_id=project.id,
        )

    linked = await project_linkage_registry.link(
        session,
        owner=owner,
        resource_uri=uri,
        project_id=project.id,
    )
    assert linked['changed'] is True
    await session.refresh(site)
    assert site.platform_project_id == project.id

    resources = await project_linkage_registry.list_linked_resources(
        session,
        owner=owner,
        project_id=project.id,
    )
    assert any(item['resource_uri'] == uri and item['title'] == site.title for item in resources)

    flow = await project_service.project_artifact_flow(
        session,
        owner=owner,
        project_id=project.id,
    )
    assert any(item['resource_uri'] == uri for item in flow['items'])

    await project_linkage_registry.unlink(
        session,
        owner=owner,
        resource_uri=uri,
        project_id=project.id,
    )
    await session.refresh(site)
    assert site.platform_project_id is None


@pytest.mark.asyncio
async def test_publish_form_access_token_binds_site_revision_form_and_project(session) -> None:
    """公开表单令牌必须绑定权威站点、当前版本、表单和平台项目，站点换版后旧令牌失效。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_form_{tag}'
    project = HasnProject(owner_id=owner, name='表单项目', status='active')
    session.add(project)
    await session.flush()
    created = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=str(project.id),
        title='公开获客页',
        asset_id=f'asset_form_{tag}',
        source_app='growth',
        source_ref=f'growth:{tag}',
        visibility='public',
    )
    site_id = created['site']['id']
    revision_id = created['revision']['id']

    issued = await publish_service.issue_form_access_token(
        session,
        slug=created['site']['slug'],
        form_ref='growth-lead-v1',
        view_ticket=None,
    )
    assert issued['site_id'] == site_id
    assert issued['revision_id'] == revision_id
    assert issued['form_ref'] == 'growth-lead-v1'

    binding = await publish_service.resolve_form_access(
        session,
        publish_ref=created['site']['slug'],
        form_access_token=issued['form_access_token'],
    )
    assert binding == {
        'site_id': site_id,
        'revision_id': revision_id,
        'form_ref': 'growth-lead-v1',
        'owner_hasn_id': owner,
        'platform_project_id': str(project.id),
        'visibility': 'public',
    }

    await publish_service.update_site(
        session,
        owner_id=owner,
        site_id=site_id,
        asset_id=f'asset_form_v2_{tag}',
        content_hash=f'form-v2-{tag}',
    )
    with pytest.raises(errors.ForbiddenError, match='版本'):
        await publish_service.resolve_form_access(
            session,
            publish_ref=created['site']['slug'],
            form_access_token=issued['form_access_token'],
        )


@pytest.mark.asyncio
async def test_publish_form_access_requires_password_session_and_rejects_private_site(session) -> None:
    """口令站点必须先持有效访问票；private 站点即使有 Owner 访问票也不签发表单令牌。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_form_acl_{tag}'
    project = HasnProject(owner_id=owner, name='表单权限项目', status='active')
    session.add(project)
    await session.flush()
    password_site = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=str(project.id),
        title='口令获客页',
        asset_id=f'asset_password_{tag}',
        source_app='growth',
        source_ref=str(uuid.uuid4()),
        visibility='password',
        password='correct-password',
    )
    with pytest.raises(errors.ForbiddenError, match='访问校验'):
        await publish_service.issue_form_access_token(
            session,
            slug=password_site['site']['slug'],
            form_ref='growth-lead-v1',
            view_ticket=None,
        )
    view_ticket = publish_service.issue_view_ticket(
        site_id=password_site['site']['id'],
        owner_id=owner,
    )['ticket']
    issued = await publish_service.issue_form_access_token(
        session,
        slug=password_site['site']['slug'],
        form_ref='growth-lead-v1',
        view_ticket=view_ticket,
    )
    assert issued['site_id'] == password_site['site']['id']

    private_site = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=str(project.id),
        title='私有获客页',
        asset_id=f'asset_private_{tag}',
        source_app='growth',
        source_ref=str(uuid.uuid4()),
        visibility='private',
    )
    private_ticket = publish_service.issue_view_ticket(
        site_id=private_site['site']['id'],
        owner_id=owner,
    )['ticket']
    with pytest.raises(errors.ForbiddenError, match='私有站点'):
        await publish_service.issue_form_access_token(
            session,
            slug=private_site['site']['slug'],
            form_ref='growth-lead-v1',
            view_ticket=private_ticket,
        )


@pytest.mark.asyncio
async def test_growth_publish_create_is_idempotent_by_authoritative_source(session) -> None:
    """同一 Growth 项目重复建站复用稳定 site.id；内容变化只新增 revision，不产生第二站点。"""
    tag = uuid.uuid4().hex[:10]
    owner = f'h_publish_growth_{tag}'
    project = HasnProject(owner_id=owner, name='幂等落地页项目', status='active')
    other_project = HasnProject(owner_id=owner, name='其他项目', status='active')
    session.add_all([project, other_project])
    await session.flush()

    first = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=project.id,
        title='项目落地页',
        asset_id=f'asset_growth_{tag}',
        content_hash=f'hash_growth_{tag}',
        source_app='growth',
        source_ref=f'growth-project-{tag}',
        visibility='public',
    )
    replay = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=project.id,
        title='项目落地页',
        asset_id=f'asset_growth_{tag}',
        content_hash=f'hash_growth_{tag}',
        source_app='growth',
        source_ref=f'growth-project-{tag}',
        visibility='public',
    )
    assert replay['site']['id'] == first['site']['id']
    assert replay['revision']['id'] == first['revision']['id']
    assert replay['reused'] is True

    updated = await publish_service.create_site(
        session,
        owner_id=owner,
        platform_project_id=project.id,
        title='项目落地页第二版',
        asset_id=f'asset_growth_v2_{tag}',
        content_hash=f'hash_growth_v2_{tag}',
        source_app='growth',
        source_ref=f'growth-project-{tag}',
        visibility='public',
    )
    assert updated['site']['id'] == first['site']['id']
    assert updated['revision']['id'] != first['revision']['id']
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Site)
        .where(
            Site.owner_id == owner,
            Site.source_app == 'growth',
            Site.source_ref == f'growth-project-{tag}',
        )
    )
    assert count == 1

    with pytest.raises(errors.ConflictError, match='其他平台项目'):
        await publish_service.create_site(
            session,
            owner_id=owner,
            platform_project_id=other_project.id,
            title='伪造项目落地页',
            asset_id=f'asset_growth_other_{tag}',
            content_hash=f'hash_growth_other_{tag}',
            source_app='growth',
            source_ref=f'growth-project-{tag}',
            visibility='public',
        )
