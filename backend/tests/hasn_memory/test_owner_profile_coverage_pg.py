"""主人画像完整度判定服务真实 PG 验收（「了解主人」KNOWU-P1，零 mock）。

需要本地 PostgreSQL :15432（部署制品），hasn_memory.owner_profile_coverage 表已建。
确定性用例不依赖 LLM；一条 live-LLM 用例真打 new-api 网关（不可达则 skip，不造假）。

⚠️ OwnerProfileCoverageService.assess 内部 commit（持久化）——本测试用唯一 owner_id +
finally 显式清理 owner_memory / owner_profile_coverage 行，避免污染 dev 库。

不变量：
- 5 个固定维度（interests/work/residence/goals/life_plan），不多不少。
- owner_memory 为空 → 5 维全 missing，evidence_version=当前版本，不调 LLM。
- get_coverage：all_sufficient ⇔ next_dimensions 为空；sufficient_count + len(next) == 5。
- assess_if_stale：判定版本 == owner_memory 版本时不重判（快读）。
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

from backend.app.hasn_memory.model import HasnOwnerMemory, OwnerProfileCoverage
from backend.app.hasn_memory.service.owner_profile_coverage_service import (
    PROFILE_DIMENSIONS,
    owner_profile_coverage_service,
)
from backend.common.llm import llm_client
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_VALID_STATUS = {'missing', 'partial', 'sufficient'}


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


async def _cleanup(session, owner_id: str) -> None:
    await session.execute(delete(OwnerProfileCoverage).where(OwnerProfileCoverage.owner_id == owner_id))
    await session.execute(delete(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner_id))
    await session.commit()


async def _seed_memory(session, owner_id: str, content: str | None, version: int) -> None:
    session.add(
        HasnOwnerMemory(owner_id=owner_id, content=content, version=version, last_merged_time=timezone.now())
    )
    await session.commit()


def test_dimensions_are_exactly_five():
    assert PROFILE_DIMENSIONS == ('interests', 'work', 'residence', 'goals', 'life_plan')


async def test_empty_memory_assesses_all_missing(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    await _seed_memory(session, owner, content='', version=3)
    try:
        result = await owner_profile_coverage_service.assess(session, owner_id=owner)
        assert len(result['dimensions']) == 5
        assert {d['dimension'] for d in result['dimensions']} == set(PROFILE_DIMENSIONS)
        assert all(d['status'] == 'missing' for d in result['dimensions'])
        assert result['all_sufficient'] is False
        assert result['sufficient_count'] == 0
        assert set(result['next_dimensions']) == set(PROFILE_DIMENSIONS)
        # 落库：5 行，evidence_version == 当前 memory 版本（不调 LLM）
        rows = (
            await session.execute(select(OwnerProfileCoverage).where(OwnerProfileCoverage.owner_id == owner))
        ).scalars().all()
        assert len(rows) == 5
        assert all(int(r.evidence_version) == 3 for r in rows)
    finally:
        await _cleanup(session, owner)


async def test_get_coverage_derivation_all_sufficient(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    await _seed_memory(session, owner, content='主人画像', version=1)
    try:
        # 直接 upsert 5 个 sufficient 行（不经 LLM），验证派生口径
        from backend.app.hasn_memory.crud.crud_owner_profile_coverage import owner_profile_coverage_dao

        for dim in PROFILE_DIMENSIONS:
            await owner_profile_coverage_dao.upsert(
                session,
                owner_id=owner,
                dimension=dim,
                status='sufficient',
                confidence=Decimal('0.9'),
                summary=f'{dim} 已了解',
                missing_hint=None,
                evidence_version=1,
                assessed_time=timezone.now(),
            )
        await session.commit()

        cov = await owner_profile_coverage_service.get_coverage(session, owner_id=owner)
        assert cov['all_sufficient'] is True
        assert cov['next_dimensions'] == []
        assert cov['sufficient_count'] == 5
        assert cov['total'] == 5
    finally:
        await _cleanup(session, owner)


async def test_get_coverage_derivation_partial(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    await _seed_memory(session, owner, content='主人画像', version=1)
    try:
        from backend.app.hasn_memory.crud.crud_owner_profile_coverage import owner_profile_coverage_dao

        # 只 work + interests sufficient，其余缺行（视同 missing）
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

        cov = await owner_profile_coverage_service.get_coverage(session, owner_id=owner)
        assert cov['all_sufficient'] is False
        assert cov['sufficient_count'] == 2
        assert set(cov['next_dimensions']) == {'residence', 'goals', 'life_plan'}
        # 缺行维度补 missing 默认态 + missing_hint 非空
        missing = [d for d in cov['dimensions'] if d['dimension'] == 'residence'][0]
        assert missing['status'] == 'missing'
        assert missing['missing_hint']
    finally:
        await _cleanup(session, owner)


async def test_assess_if_stale_skips_when_fresh(session):
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    await _seed_memory(session, owner, content='主人画像', version=2)
    try:
        from backend.app.hasn_memory.crud.crud_owner_profile_coverage import owner_profile_coverage_dao

        # 5 行均 evidence_version=2（== memory 版本）→ 不应重判
        for dim in PROFILE_DIMENSIONS:
            await owner_profile_coverage_dao.upsert(
                session,
                owner_id=owner,
                dimension=dim,
                status='partial',
                confidence=Decimal('0.5'),
                summary='s',
                missing_hint='h',
                evidence_version=2,
                assessed_time=timezone.now(),
            )
        await session.commit()

        cov = await owner_profile_coverage_service.assess_if_stale(session, owner_id=owner)
        # 仍是 partial（未被重判覆盖）
        assert all(d['status'] == 'partial' for d in cov['dimensions'])
    finally:
        await _cleanup(session, owner)


async def test_assess_with_content_live_llm(session):
    """真打 new-api 网关给非空画像打分；网关未配置/不可达则 skip（不造假）。"""
    if not llm_client.is_configured:
        pytest.skip('LLM 网关未配置，跳过 live 判定')
    owner = f'h_knowu_{uuid.uuid4().hex[:8]}'
    content = (
        '昵称: 小测\n§\n职业: 在一家科技公司做后端工程师，最近在做支付系统\n§\n'
        '兴趣: 喜欢摄影、周末爬山\n§\n常驻城市: 深圳\n§\n'
        '近期目标: 三个月内上线新的对账服务\n§\n人生规划: 希望未来成为技术专家、保持工作与生活平衡'
    )
    await _seed_memory(session, owner, content=content, version=5)
    try:
        await owner_profile_coverage_service.assess(session, owner_id=owner)
        rows = (
            await session.execute(select(OwnerProfileCoverage).where(OwnerProfileCoverage.owner_id == owner))
        ).scalars().all()
        if not rows:
            pytest.skip('LLM 网关不可达（assess 未落行，best-effort 降级）')
        assert len(rows) == 5
        assert {r.dimension for r in rows} == set(PROFILE_DIMENSIONS)
        assert all(r.status in _VALID_STATUS for r in rows)
        assert all(int(r.evidence_version) == 5 for r in rows)
        # 信息相当充分的画像，至少有维度被判 partial/sufficient（非全 missing）
        assert any(r.status in {'partial', 'sufficient'} for r in rows)
    finally:
        await _cleanup(session, owner)
