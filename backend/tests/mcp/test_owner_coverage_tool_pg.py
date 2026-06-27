"""平台工具 hasn.owner.coverage.get 真实 PG 验收（「了解主人」KNOWU-P2，零 mock）。

验证采访分身经 owner 工具面读「主人 5 维画像还缺哪几维」：owner 身份强制取自
AgentContext.owner_hasn_id（绝不入 arguments），直调云端权威 assess_if_stale。

需要本地 PostgreSQL :15432，hasn_memory.owner_profile_coverage 表已建。
"""

from __future__ import annotations

import uuid

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.crud.crud_owner_profile_coverage import owner_profile_coverage_dao
from backend.app.hasn_memory.model import HasnOwnerMemory, OwnerProfileCoverage
from backend.app.hasn_memory.service.owner_profile_coverage_service import PROFILE_DIMENSIONS
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.owner import OwnerCoverageGetTool
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


def _ctx(owner_hasn_id: str) -> AgentContext:
    return AgentContext(
        hasn_id='a_knowu_test',
        owner_id=0,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
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


async def test_coverage_get_tool_returns_five_dimensions(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    # 注：assess_if_stale 内部新开 async_db_session（工具体如此），故先用本 session 落数据并提交
    session.add(
        HasnOwnerMemory(owner_id=owner, content='主人画像', version=1, last_merged_time=timezone.now())
    )
    await session.commit()
    for dim in ('work', 'interests'):
        await owner_profile_coverage_dao.upsert(
            session,
            owner_id=owner,
            dimension=dim,
            status='sufficient',
            confidence=Decimal('0.8'),
            summary='ok',
            missing_hint=None,
            evidence_version=1,
            assessed_time=timezone.now(),
        )
    await session.commit()

    try:
        tool = OwnerCoverageGetTool()
        result = await tool.execute(_ctx(owner), {})
        # 工具返回完整度字典：5 维齐全，缺什么采访什么
        assert len(result['dimensions']) == 5
        assert {d['dimension'] for d in result['dimensions']} == set(PROFILE_DIMENSIONS)
        assert result['all_sufficient'] is False
        assert result['sufficient_count'] == 2
        assert set(result['next_dimensions']) == {'residence', 'goals', 'life_plan'}
        # JSON 安全（confidence 为 float，可经 MCP 信封序列化）
        for d in result['dimensions']:
            assert isinstance(d['confidence'], float)
    finally:
        await session.execute(delete(OwnerProfileCoverage).where(OwnerProfileCoverage.owner_id == owner))
        await session.execute(delete(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner))
        await session.commit()
