"""knowledge 模块测试 fixtures：真实本地 PostgreSQL（15432）+ 真实 RAGFlow（零 mock）。

- `session`：真实 PG AsyncSession，flush 不 commit，结束 rollback（PG 侧不留残留）；
- `ragflow_ready`：探活 active public knowledge 实例的 endpoint；不可达则 skip（不可达≠绿，如实跳过）。
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.database.db import SQLALCHEMY_DATABASE_URL


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def ragflow_ready(session):
    """真实 RAGFlow 实例可达性探活；不可达 skip（集成测试要求真实引擎）。"""
    from backend.app.hasn_knowledge.service.instance import resolve_knowledge_instance
    from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError

    try:
        client, config = await resolve_knowledge_instance(session)
    except KnowledgeProviderError as exc:
        pytest.skip(f'knowledge 实例未配置，跳过: {exc}')
    try:
        async with httpx.AsyncClient(timeout=5) as probe:
            response = await probe.get(config.endpoint)
        if response.status_code >= 500:
            pytest.skip(f'RAGFlow 实例异常（HTTP {response.status_code}），跳过')
    except httpx.HTTPError as exc:
        pytest.skip(f'RAGFlow 实例不可达，跳过: {exc!r}')
    return client
