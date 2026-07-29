"""获客项目化 S2 挂靠、项目聚合与资源 ACL 真实 PostgreSQL 测试。"""

from __future__ import annotations

import asyncio

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_provision import (
    GrowthProjectProvision,
)
from backend.app.hasn_growth.model.opportunity import Opportunity

# 单文件测试显式触发生产注册链的两个 Growth 模块。
from backend.app.hasn_growth.service import project_linkage as _growth_project_linkage  # ruff: ignore[unused-import]
from backend.app.hasn_growth.service import resource_adapter as _growth_resource_adapter  # ruff: ignore[unused-import]
from backend.app.hasn_growth.service.growth_project_app_service import growth_project_app_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_SQL_FILES = (
    _REPO / 'backend/sql/hasn_growth/007_create_growth_project_v4_tables.sql',
    _REPO / 'backend/sql/hasn_growth/008_create_growth_pii_key_state.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-project-v4-columns.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-pii-key-fence-triggers.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-playbook-trace-columns.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-project-association-uniques.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-30-growth-project-provision-state-machine.sql',
    _REPO / 'backend/sql/hasn_growth/010_create_growth_profile_tables.sql',
)


async def _apply_sql(session: AsyncSession) -> None:
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    for sql_file in _SQL_FILES:
        await connection.execute(sql_file.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _apply_sql(db)
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


async def _seed_project(
    session: AsyncSession,
    *,
    owner: str,
    name: str,
) -> HasnProject:
    project = HasnProject(owner_id=owner, name=name, status='active')
    session.add(project)
    await session.flush()
    return project


async def _seed_growth(
    session: AsyncSession,
    *,
    owner: str,
    project: HasnProject,
) -> GrowthProject:
    growth = GrowthProject(
        platform_project_id=project.id,
        user_id=1,
        owner_hasn_id=owner,
        owner_scope='personal',
        name='测试获客漏斗',
        status='draft',
        provision_status='pending',
    )
    session.add(growth)
    await session.flush()
    return growth


async def test_growth_link_is_idempotent_and_forbids_unlink_relink_cross_owner(
    session: AsyncSession,
) -> None:
    owner = 'h_growth_linkage_owner'
    project = await _seed_project(session, owner=owner, name='当前项目')
    other_project = await _seed_project(session, owner=owner, name='另一个项目')
    growth = await _seed_growth(session, owner=owner, project=project)
    uri = f'hasn://growth/projects/{growth.id}'

    replay = await project_linkage_registry.link(
        session,
        owner=owner,
        resource_uri=uri,
        project_id=project.id,
    )
    assert replay['linked'] is True
    assert replay['changed'] is False

    with pytest.raises(errors.ConflictError) as rebind:
        await project_linkage_registry.link(
            session,
            owner=owner,
            resource_uri=uri,
            project_id=other_project.id,
        )
    assert rebind.value.data['error_code'] == 'REBIND_NOT_SUPPORTED'

    with pytest.raises(errors.ConflictError) as unlink:
        await project_linkage_registry.unlink(
            session,
            owner=owner,
            resource_uri=uri,
            project_id=project.id,
        )
    assert unlink.value.data['error_code'] == 'PROJECT_REQUIRED'

    with pytest.raises(errors.NotFoundError):
        await project_linkage_registry.link(
            session,
            owner='h_growth_linkage_intruder',
            resource_uri=uri,
            project_id=project.id,
        )


async def test_growth_project_aggregation_contains_stable_child_uris(
    session: AsyncSession,
) -> None:
    owner = 'h_growth_aggregate_owner'
    project = await _seed_project(session, owner=owner, name='聚合项目')
    growth = await _seed_growth(session, owner=owner, project=project)
    customer = Customer(
        customer_no='C-S2',
        user_id=1,
        growth_project_id=growth.id,
        source_kind='manual',
        company_name='测试企业',
        lifecycle_status='active',
    )
    session.add(customer)
    await session.flush()
    opportunity = Opportunity(
        opportunity_no='O-S2',
        customer_id=customer.id,
        user_id=1,
        growth_project_id=growth.id,
        name='测试商机',
        stage='contacted',
        currency='CNY',
        created_by_kind='owner',
    )
    session.add(opportunity)
    await session.flush()

    uris = set(
        await project_linkage_registry.artifact_resource_uris(
            session,
            owner=owner,
            project_id=project.id,
        )
    )
    assert f'hasn://growth/projects/{growth.id}' in uris
    assert f'hasn://growth/leads/{growth.id}' in uris
    assert f'hasn://growth/customers/{customer.id}' in uris
    assert f'hasn://growth/opportunities/{opportunity.id}' in uris


async def test_growth_resource_adapters_load_only_authoritative_server_ids(
    session: AsyncSession,
) -> None:
    owner = 'h_growth_acl_owner'
    project = await _seed_project(session, owner=owner, name='ACL 项目')
    growth = await _seed_growth(session, owner=owner, project=project)
    customer = Customer(
        customer_no='C-ACL',
        user_id=1,
        growth_project_id=growth.id,
        source_kind='manual',
        lifecycle_status='active',
    )
    session.add(customer)
    await session.flush()
    opportunity = Opportunity(
        opportunity_no='O-ACL',
        customer_id=customer.id,
        user_id=1,
        growth_project_id=growth.id,
        name='ACL 商机',
        stage='contacted',
        currency='CNY',
        created_by_kind='owner',
    )
    session.add(opportunity)
    await session.flush()

    expected_ids = {
        'growth_project': str(growth.id),
        'growth_leads': str(growth.id),
        'growth_customer': str(customer.id),
        'growth_opportunity': str(opportunity.id),
    }
    for resource_type, server_id in expected_ids.items():
        adapter = resource_kind_registry.get(resource_type)
        assert adapter is not None
        meta = await adapter.load_meta(session, server_id)
        assert meta is not None
        assert meta.resource_id == server_id
        assert meta.owner_hasn_id == owner
        assert meta.visibility == 'private'
        assert await adapter.load_meta(session, f'local-{server_id}') is None


async def test_growth_project_enable_is_owner_scoped_and_idempotent(
    session: AsyncSession,
) -> None:
    owner = 'h_growth_enable_owner'
    platform_project = await _seed_project(
        session,
        owner=owner,
        name='待启用项目',
    )

    created = await growth_project_app_service.enable(
        session,
        owner_hasn_id=owner,
        owner_user_id=101,
        platform_project_id=platform_project.id,
        name=None,
        tagline='测试标语',
        command_id='11111111-1111-4111-8111-111111111111',
        idempotency_key='growth-enable-owner-1',
    )
    replayed = await growth_project_app_service.enable(
        session,
        owner_hasn_id=owner,
        owner_user_id=101,
        platform_project_id=platform_project.id,
        name=None,
        tagline='测试标语',
        command_id='11111111-1111-4111-8111-111111111111',
        idempotency_key='growth-enable-owner-1',
    )
    assert created['created'] is True
    assert replayed['created'] is False
    assert replayed['growth_project']['id'] == created['growth_project']['id']
    assert replayed['growth_project']['platform_project_id'] == str(platform_project.id)

    current = await growth_project_app_service.get_for_platform(
        session,
        owner_hasn_id=owner,
        platform_project_id=platform_project.id,
    )
    assert current['platform_project']['id'] == str(platform_project.id)
    assert current['growth_project']['id'] == created['growth_project']['id']

    with pytest.raises(errors.ConflictError) as duplicate:
        await growth_project_app_service.enable(
            session,
            owner_hasn_id=owner,
            owner_user_id=101,
            platform_project_id=platform_project.id,
            name='另一创建意图',
            tagline='测试标语',
            command_id='22222222-2222-4222-8222-222222222222',
            idempotency_key='growth-enable-owner-2',
        )
    assert duplicate.value.data['error_code'] == 'GROWTH_PROJECT_ALREADY_EXISTS'

    with pytest.raises(errors.NotFoundError):
        await growth_project_app_service.get_by_id(
            session,
            owner_hasn_id='h_growth_enable_intruder',
            growth_project_id=created['growth_project']['id'],
        )


async def test_growth_project_enable_rejects_archived_and_enterprise_projects(
    session: AsyncSession,
) -> None:
    owner = 'h_growth_enable_gate_owner'
    archived = HasnProject(owner_id=owner, name='已归档', status='archived')
    enterprise = HasnProject(
        owner_id=owner,
        name='企业项目',
        status='active',
        enterprise_id='11111111-1111-4111-8111-111111111111',
    )
    session.add_all([archived, enterprise])
    await session.flush()
    legacy_growth = GrowthProject(
        platform_project_id=archived.id,
        user_id=102,
        owner_hasn_id=owner,
        owner_scope='personal',
        name='待改挂漏斗',
        status='draft',
        provision_status='pending',
    )
    session.add(legacy_growth)
    await session.flush()

    with pytest.raises(errors.ConflictError) as archived_error:
        await growth_project_app_service.enable(
            session,
            owner_hasn_id=owner,
            owner_user_id=102,
            platform_project_id=archived.id,
            name=None,
            tagline=None,
            command_id='33333333-3333-4333-8333-333333333333',
            idempotency_key='growth-enable-archived',
        )
    assert archived_error.value.data['error_code'] == 'PROJECT_ARCHIVED'

    with pytest.raises(errors.RequestError) as enterprise_error:
        await growth_project_app_service.enable(
            session,
            owner_hasn_id=owner,
            owner_user_id=102,
            platform_project_id=enterprise.id,
            name=None,
            tagline=None,
            command_id='44444444-4444-4444-8444-444444444444',
            idempotency_key='growth-enable-enterprise',
        )
    assert enterprise_error.value.code == 422
    assert enterprise_error.value.data['error_code'] == 'ENTERPRISE_IDENTITY_MAPPING_REQUIRED'

    with pytest.raises(errors.RequestError) as link_error:
        await project_linkage_registry.link(
            session,
            owner=owner,
            resource_uri=f'hasn://growth/projects/{legacy_growth.id}',
            project_id=enterprise.id,
        )
    assert link_error.value.code == 422
    assert link_error.value.data['error_code'] == 'ENTERPRISE_IDENTITY_MAPPING_REQUIRED'


async def test_concurrent_growth_project_create_conflict_is_deterministic() -> None:
    """两个真实事务竞争同一平台项目锁，第二个不同创建意图稳定返回 409。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = 'h_growth_concurrent_owner'
    setup = sessions()
    first = sessions()
    second = sessions()
    platform_project_id = None
    try:
        await _apply_sql(setup)
        platform_project = HasnProject(
            owner_id=owner,
            name='并发项目',
            status='active',
        )
        setup.add(platform_project)
        await setup.commit()
        platform_project_id = platform_project.id

        created = await growth_project_app_service.enable(
            first,
            owner_hasn_id=owner,
            owner_user_id=103,
            platform_project_id=platform_project.id,
            name='并发漏斗 A',
            tagline=None,
            command_id='55555555-5555-4555-8555-555555555555',
            idempotency_key='growth-enable-concurrent-a',
        )
        assert created['created'] is True

        second_request = asyncio.create_task(
            growth_project_app_service.enable(
                second,
                owner_hasn_id=owner,
                owner_user_id=103,
                platform_project_id=platform_project.id,
                name='并发漏斗 B',
                tagline=None,
                command_id='66666666-6666-4666-8666-666666666666',
                idempotency_key='growth-enable-concurrent-b',
            )
        )
        await asyncio.sleep(0.05)
        assert not second_request.done()

        await first.commit()
        with pytest.raises(errors.ConflictError) as conflict:
            await second_request
        assert conflict.value.data['error_code'] == 'GROWTH_PROJECT_ALREADY_EXISTS'
        await second.rollback()
    finally:
        await first.rollback()
        await second.rollback()
        await first.close()
        await second.close()
        await setup.close()
        if platform_project_id is not None:
            cleanup = sessions()
            try:
                await cleanup.execute(
                    sa.delete(GrowthProjectProvision).where(
                        GrowthProjectProvision.growth_project_id.in_(
                            sa.select(GrowthProject.id).where(GrowthProject.platform_project_id == platform_project_id)
                        )
                    )
                )
                await cleanup.execute(
                    sa.delete(GrowthProject).where(GrowthProject.platform_project_id == platform_project_id)
                )
                await cleanup.execute(sa.delete(HasnProject).where(HasnProject.id == platform_project_id))
                await cleanup.commit()
            finally:
                await cleanup.close()
        await engine.dispose()


async def test_growth_enable_creates_reliable_steps_and_lifecycle_is_explicit(
    session: AsyncSession,
) -> None:
    """开通命令有四个持久步骤；归档恢复只回 paused，不隐式恢复自动动作。"""
    owner = 'h_growth_s4_lifecycle_owner'
    platform_project = await _seed_project(
        session,
        owner=owner,
        name='S4 生命周期项目',
    )
    command_id = '77777777-7777-4777-8777-777777777777'
    result = await growth_project_app_service.enable(
        session,
        owner_hasn_id=owner,
        owner_user_id=104,
        platform_project_id=platform_project.id,
        name=None,
        tagline='可靠开通',
        command_id=command_id,
        idempotency_key='growth-enable-s4-lifecycle',
    )
    growth_id = result['growth_project']['id']

    provisions = (
        (
            await session.execute(
                sa
                .select(GrowthProjectProvision)
                .where(GrowthProjectProvision.growth_project_id == growth_id)
                .order_by(GrowthProjectProvision.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.step for row in provisions] == [
        'create_funnel',
        'create_knowledge',
        'attach_knowledge',
        'seed_knowledge',
    ]
    assert [row.status for row in provisions] == [
        'success',
        'pending',
        'pending',
        'pending',
    ]
    assert {row.command_id for row in provisions} == {command_id}
    assert result['growth_project']['provision_steps'][0]['status'] == 'success'

    other_platform = await _seed_project(
        session,
        owner=owner,
        name='S4 另一平台项目',
    )
    with pytest.raises(errors.ConflictError) as reused_key:
        await growth_project_app_service.enable(
            session,
            owner_hasn_id=owner,
            owner_user_id=104,
            platform_project_id=other_platform.id,
            name=None,
            tagline='错误复用稳定键',
            command_id=command_id,
            idempotency_key='growth-enable-s4-lifecycle',
        )
    assert reused_key.value.data['error_code'] == 'GROWTH_IDEMPOTENCY_CONFLICT'

    paused = await growth_project_app_service.pause(
        session,
        owner_hasn_id=owner,
        growth_project_id=growth_id,
    )
    assert paused['status'] == 'paused'

    archived = await growth_project_app_service.archive(
        session,
        owner_hasn_id=owner,
        growth_project_id=growth_id,
    )
    assert archived['status'] == 'archived'

    restored = await growth_project_app_service.restore(
        session,
        owner_hasn_id=owner,
        growth_project_id=growth_id,
    )
    assert restored['status'] == 'paused'

    with pytest.raises(errors.ConflictError) as not_ready:
        await growth_project_app_service.resume(
            session,
            owner_hasn_id=owner,
            growth_project_id=growth_id,
        )
    assert not_ready.value.data['error_code'] == 'GROWTH_PROJECT_NOT_READY'

    for provision in provisions:
        provision.status = 'success'
    growth = await session.get(GrowthProject, UUID(growth_id))
    assert growth is not None
    growth.provision_status = 'ready'
    with pytest.raises(errors.ConflictError) as readiness_blocked:
        await growth_project_app_service.resume(
            session,
            owner_hasn_id=owner,
            growth_project_id=growth_id,
        )
    assert (
        readiness_blocked.value.data['error_code']
        == 'GROWTH_PROJECT_READINESS_BLOCKED'
    )
