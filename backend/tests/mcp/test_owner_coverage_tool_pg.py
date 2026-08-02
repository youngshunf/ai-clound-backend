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
from backend.app.mcp.tools.owner import (
    OwnerCoverageGetTool,
    OwnerGrowthClaimTool,
    OwnerOnboardingClaimTool,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


def _ctx(owner_hasn_id: str) -> AgentContext:
    return AgentContext(
        hasn_id='a_knowu_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    # 进场也先释放全局池：前序测试文件（TestClient 等）可能留下绑在别的事件循环上的池连接，
    # 被测工具中途新开 async_db_session 会拿到它们 → 「different loop」。对称于 teardown 的 dispose。
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        # 被测工具内部用全局 async_db_session（生产真实行为）；pytest-asyncio 每测试新事件循环，
        # 全局引擎的连接池会绑在上一个循环上 → 下个测试报「different loop」。每测试后释放全局池，
        # 让下个测试拿到本循环的新连接（与 cloud MCP 工具测试同源处理）。
        await async_engine.dispose()


async def test_coverage_get_tool_returns_five_dimensions(session) -> None:
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    # 注：assess_if_stale 内部新开 async_db_session（工具体如此），故先用本 session 落数据并提交
    session.add(
        HasnOwnerMemory(owner_id=owner, content='主人画像', version=1, last_merged_time=timezone.now())
    )
    await session.commit()
    # 落齐全 5 维（work/interests 充分，其余 missing），evidence_version 与 owner_memory 版本一致
    # → assess_if_stale 走快读路径、不触发 LLM 重判，断言确定且不依赖 LLM 可用性（failover 链可用后
    # assess 会真打模型重判，若只落 2 维会强制重判并覆盖预置行 → 此处落齐 5 维走读路径）。
    seeded_status = {
        'work': 'sufficient',
        'interests': 'sufficient',
        'residence': 'missing',
        'goals': 'missing',
        'life_plan': 'missing',
    }
    for dim, status in seeded_status.items():
        is_sufficient = status == 'sufficient'
        await owner_profile_coverage_dao.upsert(
            session,
            owner_id=owner,
            dimension=dim,
            status=status,
            confidence=Decimal('0.8') if is_sufficient else Decimal(0),
            summary='ok' if is_sufficient else None,
            missing_hint=None if is_sufficient else 'todo',
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


async def test_onboarding_and_growth_claim_tools(session) -> None:
    """hasn.owner.onboarding.claim / hasn.owner.growth.claim：owner 取自凭证、周期节奏闸生效。

    首次认领 True，冷却期内再认领 False；采访/成长两闸互相独立（各自时间戳列）。
    """
    from sqlalchemy import text as _text

    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    try:
        onboarding = OwnerOnboardingClaimTool()
        first = await onboarding.execute(_ctx(owner), {})
        assert first['claimed'] is True
        assert first['cooldown_days'] == 7
        # 冷却期内（刚认领过）立即再认领 → 不认领，避免每天新起采访会话
        second = await onboarding.execute(_ctx(owner), {})
        assert second['claimed'] is False
        # 成长 claim 与采访独立 → 同一 owner 首次认领仍 True，自定义冷却生效
        growth = OwnerGrowthClaimTool()
        g = await growth.execute(_ctx(owner), {'cooldown_days': 3})
        assert g['claimed'] is True
        assert g['cooldown_days'] == 3
    finally:
        # 工具经全局 async_db_session 落库；用本测试的独立 session 清理（同库）。
        await session.execute(_text('DELETE FROM hasn_plan.preference WHERE owner_hasn_id = :o'), {'o': owner})
        await session.commit()
