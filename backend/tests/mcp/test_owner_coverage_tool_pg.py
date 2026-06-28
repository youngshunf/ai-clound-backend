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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.crud.crud_owner_profile_coverage import owner_profile_coverage_dao
from backend.app.hasn_memory.model import HasnOwnerMemory, OwnerProfileCoverage
from backend.app.hasn_memory.model.owner_memory import HasnOwnerMemoryContribution
from backend.app.hasn_memory.service.owner_profile_coverage_service import PROFILE_DIMENSIONS
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.owner import OwnerCoverageGetTool, OwnerMemoryContributeTool
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
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
        # 被测工具内部用全局 async_db_session（生产真实行为）；pytest-asyncio 每测试新事件循环，
        # 全局引擎的连接池会绑在上一个循环上 → 下个测试报「different loop」。每测试后释放全局池，
        # 让下个测试拿到本循环的新连接（与 cloud MCP 工具测试同源处理）。
        await async_engine.dispose()


async def test_coverage_get_tool_returns_five_dimensions(session):
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
            confidence=Decimal('0.8') if is_sufficient else Decimal('0'),
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


async def test_memory_contribute_tool_lands_contribution(session):
    """采访分身经 hasn.owner.memory.contribute 写入观察：contribution 必落库（accepted）。

    合并依赖 LLM（dev 无余额时合并延后、contribution 留 pending），故只断言「贡献落库」这条
    确定性事实——accepted=True、库里有该 owner 的 contribution 行、owner/agent 身份取自凭证。
    """
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    agent_hasn_id = 'a_knowu_interviewer'
    ctx = AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=0,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id=owner,
    )
    try:
        tool = OwnerMemoryContributeTool()
        result = await tool.execute(
            ctx,
            {'content': '主人是后端工程师，主攻 Rust 与分布式系统，近期目标是三个月内通过 PMP 认证。'},
        )
        # 贡献必被接受（合并是否成功取决于 LLM 可用性，宽容处理）
        assert result['accepted'] is True
        assert result.get('contribution_id') is not None
        assert isinstance(result['merged'], bool)
        # 库里确有该 owner 的一条 contribution（身份取自凭证，绝不入参）
        rows = list(
            (
                await session.execute(
                    select(HasnOwnerMemoryContribution).where(
                        HasnOwnerMemoryContribution.owner_id == owner
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].agent_hasn_id == agent_hasn_id
        assert '后端工程师' in rows[0].content
    finally:
        await session.execute(
            delete(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.owner_id == owner)
        )
        await session.execute(delete(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner))
        await session.commit()


async def test_memory_contribute_tool_merge_deferred_on_failure(session, monkeypatch):
    """合并失败时如实透出 merge_deferred + merge_error；贡献仍落库（零 fake，不产生假合并）。

    用 monkeypatch 让 merge_owner_memory 抛错（模拟 LLM 网关挂/余额不足等 infra 失败，不伪造业务
    数据）——验证工具返回 accepted=True、merged=False、merge_deferred=True、merge_error 非空，
    且 contribution 仍被持久化（pending，留待后续重试）。
    """
    from backend.app.mcp.tools import owner as owner_tool_mod

    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'

    async def _boom(*_args, **_kwargs):
        raise RuntimeError('simulated LLM gateway failure')

    monkeypatch.setattr(owner_tool_mod.owner_memory_service, 'merge_owner_memory', _boom)
    try:
        tool = OwnerMemoryContributeTool()
        result = await tool.execute(
            AgentContext(
                hasn_id='a_knowu_interviewer',
                owner_id=0,
                scopes=[],
                agent_status='active',
                metadata={},
                owner_hasn_id=owner,
            ),
            {'content': '主人常驻昆明五华区，注重健康与抗衰老。'},
        )
        assert result['accepted'] is True
        assert result['merged'] is False
        assert result['merge_deferred'] is True
        assert isinstance(result['merge_error'], str) and result['merge_error']
        assert result.get('contribution_id') is not None
        # 贡献仍落库（pending），合并留待下次
        rows = list(
            (
                await session.execute(
                    select(HasnOwnerMemoryContribution).where(
                        HasnOwnerMemoryContribution.owner_id == owner
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == 'pending'
    finally:
        await session.execute(
            delete(HasnOwnerMemoryContribution).where(HasnOwnerMemoryContribution.owner_id == owner)
        )
        await session.commit()


async def test_memory_contribute_tool_rejects_empty_content(session):
    """空 content 直接拒绝、不落库（零 fake，不产生假贡献）。"""
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    tool = OwnerMemoryContributeTool()
    result = await tool.execute(
        AgentContext(
            hasn_id='a_knowu_interviewer',
            owner_id=0,
            scopes=[],
            agent_status='active',
            metadata={},
            owner_hasn_id=owner,
        ),
        {'content': '   '},
    )
    assert result['accepted'] is False
    assert result['merged'] is False
    count = (
        await session.execute(
            select(func.count())
            .select_from(HasnOwnerMemoryContribution)
            .where(HasnOwnerMemoryContribution.owner_id == owner)
        )
    ).scalar_one()
    assert int(count or 0) == 0
