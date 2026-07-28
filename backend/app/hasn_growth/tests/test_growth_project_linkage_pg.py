"""获客项目化 S2 挂靠、项目聚合与资源 ACL 真实 PostgreSQL 测试。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.opportunity import Opportunity

# 单文件测试显式触发生产注册链的两个 Growth 模块。
from backend.app.hasn_growth.service import project_linkage as _growth_project_linkage  # ruff: ignore[unused-import]
from backend.app.hasn_growth.service import resource_adapter as _growth_resource_adapter  # ruff: ignore[unused-import]
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
