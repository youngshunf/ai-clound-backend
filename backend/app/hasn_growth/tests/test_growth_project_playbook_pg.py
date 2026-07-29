"""S5 项目打法采用关系与不可变执行版本的真实 PostgreSQL 测试。"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_playbook import GrowthProjectPlaybook
from backend.app.hasn_growth.model.playbook import Playbook
from backend.app.hasn_growth.model.playbook_version import PlaybookVersion
from backend.app.hasn_growth.service.playbook_service import playbook_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:10]
    owner = f'h_growth_playbook_{tag}'
    user_id = 95_200_000_000 + int(uuid.uuid4().int % 700_000_000)
    platform = HasnProject(owner_id=owner, name=f'打法项目 {tag}', status='active')
    session.add(platform)
    await session.flush()
    growth = GrowthProject(
        platform_project_id=platform.id,
        user_id=user_id,
        owner_hasn_id=owner,
        owner_scope='personal',
        name=f'打法漏斗 {tag}',
        status='active',
        provision_status='ready',
    )
    builtin = Playbook(
        user_id=None,
        name=f'顾问式销售 {tag}',
        version=1,
        enabled=True,
        goal='取得首次有效回复',
        target_profile={'buyer_roles': ['销售负责人']},
        cadence=[{'day': 1, 'channel': 'email', 'goal': '建立联系'}],
        tone_guide='具体、克制',
        exit_rule={'max_silent_rounds': 2, 'action': 'stop'},
        is_builtin=True,
        owner_scope='personal',
    )
    foreign = Playbook(
        user_id=user_id + 1,
        name=f'他人打法 {tag}',
        version=1,
        enabled=True,
        is_builtin=False,
        owner_scope='personal',
    )
    session.add_all((growth, builtin, foreign))
    await session.flush()
    try:
        yield SimpleNamespace(
            session=session,
            owner=owner,
            user_id=user_id,
            growth=growth,
            builtin=builtin,
            foreign=foreign,
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_owner_adopts_frozen_playbook_and_template_changes_do_not_rewrite_history(
    ctx: SimpleNamespace,
) -> None:
    first = await playbook_service.adopt_for_project(
        ctx.session,
        owner_hasn_id=ctx.owner,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        playbook_id=ctx.builtin.id,
        expected_playbook_version=1,
        configuration={'daily_limit': 20},
    )
    assert first['status'] == 'active'
    assert first['playbook_version'] == 1
    replay = await playbook_service.adopt_for_project(
        ctx.session,
        owner_hasn_id=ctx.owner,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        playbook_id=ctx.builtin.id,
        expected_playbook_version=1,
        configuration={'daily_limit': 20},
    )
    assert replay['id'] == first['id']

    ctx.builtin.version = 2
    ctx.builtin.goal = '取得合格商机'
    ctx.builtin.cadence = [
        {'day': 1, 'channel': 'email', 'goal': '建立联系'},
        {
            'day': 4,
            'channel': 'manual_assist',
            'goal': '复核',
        },
    ]
    await ctx.session.flush()
    second = await playbook_service.adopt_for_project(
        ctx.session,
        owner_hasn_id=ctx.owner,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        playbook_id=ctx.builtin.id,
        expected_playbook_version=2,
        configuration={'daily_limit': 10},
    )
    assert second['id'] != first['id']
    assert second['playbook_version'] == 2

    old_execution = await playbook_service.get_execution_snapshot(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        growth_project_playbook_id=first['id'],
        require_active=False,
    )
    assert old_execution['definition']['goal'] == '取得首次有效回复'
    assert old_execution['configuration'] == {'daily_limit': 20}
    assert old_execution['status'] == 'retired'
    assert (
        await ctx.session.scalar(
            sa.select(sa.func.count()).select_from(PlaybookVersion).where(PlaybookVersion.playbook_id == ctx.builtin.id)
        )
    ) == 2


async def test_recommendations_are_read_only_until_owner_adopts(ctx: SimpleNamespace) -> None:
    before = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(GrowthProjectPlaybook)
        .where(GrowthProjectPlaybook.growth_project_id == ctx.growth.id)
    )
    recommendations = await playbook_service.recommend_for_project(
        ctx.session,
        owner_hasn_id=ctx.owner,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
    )
    after = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(GrowthProjectPlaybook)
        .where(GrowthProjectPlaybook.growth_project_id == ctx.growth.id)
    )
    assert any(item['playbook_id'] == ctx.builtin.id for item in recommendations)
    assert before == after == 0


async def test_adopt_repairs_legacy_migration_hash_when_definition_is_unchanged(
    ctx: SimpleNamespace,
) -> None:
    """迁移快照字段完全一致时可修正旧哈希算法，不能误报同版本定义被改。"""
    frozen = PlaybookVersion(
        playbook_id=ctx.builtin.id,
        version=ctx.builtin.version,
        name=ctx.builtin.name,
        goal=ctx.builtin.goal,
        target_profile=ctx.builtin.target_profile,
        cadence=ctx.builtin.cadence,
        tone_guide=ctx.builtin.tone_guide,
        exit_rule=ctx.builtin.exit_rule,
        definition_hash='0' * 64,
        created_by_kind='migration',
    )
    ctx.session.add(frozen)
    await ctx.session.flush()

    adopted = await playbook_service.adopt_for_project(
        ctx.session,
        owner_hasn_id=ctx.owner,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        playbook_id=ctx.builtin.id,
        expected_playbook_version=1,
        configuration={},
    )

    assert adopted['status'] == 'active'
    assert frozen.definition_hash != '0' * 64


async def test_adopt_rejects_legacy_migration_hash_when_definition_changed(
    ctx: SimpleNamespace,
) -> None:
    """迁移快照字段有差异时仍必须拒绝，不能借旧哈希修复掩盖未升版本。"""
    ctx.session.add(
        PlaybookVersion(
            playbook_id=ctx.builtin.id,
            version=ctx.builtin.version,
            name=ctx.builtin.name,
            goal='迁移时的旧目标',
            target_profile=ctx.builtin.target_profile,
            cadence=ctx.builtin.cadence,
            tone_guide=ctx.builtin.tone_guide,
            exit_rule=ctx.builtin.exit_rule,
            definition_hash='0' * 64,
            created_by_kind='migration',
        )
    )
    await ctx.session.flush()

    with pytest.raises(errors.ConflictError):
        await playbook_service.adopt_for_project(
            ctx.session,
            owner_hasn_id=ctx.owner,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth.id,
            playbook_id=ctx.builtin.id,
            expected_playbook_version=1,
            configuration={},
        )


async def test_project_playbook_rejects_cross_owner_and_version_conflict(
    ctx: SimpleNamespace,
) -> None:
    with pytest.raises(errors.NotFoundError):
        await playbook_service.adopt_for_project(
            ctx.session,
            owner_hasn_id=ctx.owner,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth.id,
            playbook_id=ctx.foreign.id,
            expected_playbook_version=1,
            configuration={},
        )
    with pytest.raises(errors.ConflictError):
        await playbook_service.adopt_for_project(
            ctx.session,
            owner_hasn_id=ctx.owner,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth.id,
            playbook_id=ctx.builtin.id,
            expected_playbook_version=99,
            configuration={},
        )
