"""获客 JSONB 数组字段的真实 PostgreSQL 契约。"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.lead_collection_job import LeadCollectionJob
from backend.app.hasn_growth.model.lead_source_config import LeadSourceConfig
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    database_session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield database_session
    finally:
        await database_session.rollback()
        await database_session.close()
        await engine.dispose()


async def test_growth_jsonb_array_fields_keep_list_shape(session: AsyncSession) -> None:
    tag = uuid4().hex[:12]
    job = LeadCollectionJob(
        job_no=f'JSON{tag.upper()}',
        keyword='SaaS',
        source_types=['public_web', 'maps'],
        status='pending',
        max_pages=1,
        max_results=1,
        request_config={},
        meta_data={},
    )
    config = LeadSourceConfig(source_type=f'json_{tag}', name=f'默认_{tag}')
    session.add_all([job, config])
    await session.flush()
    await session.refresh(job)
    await session.refresh(config)

    assert job.source_types == ['public_web', 'maps']
    assert config.min_contact_fields == ['email', 'phone']
    assert config.domain_blacklist == []
    assert config.country_blacklist == ['DE', 'FR', 'IT', 'NL', 'ES']
