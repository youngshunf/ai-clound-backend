"""创作运营进化闭环 M5 真实 PG 验收（设计 §7，零 mock，事务末尾回滚）。

insight.log 据 proposed_action 原子回写三处：profile.pillar_weights（累加+下界0）/
viral_pattern（入库可被 pattern.search 命中）/ playbook（patch 自有；内置如实跳过）+
action_taken 留痕（零 fake）。需要本地 PostgreSQL :15432（DATABASE_PORT）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_creator.model.playbook import Playbook
from backend.app.hasn_creator.service.creator_service import creator_service
from backend.app.hasn_creator.service.scope_context import CreatorScope
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_UID = 922001
_HASN = 'hasn:owner:evo-a'


def _scope() -> CreatorScope:
    return CreatorScope(user_id=_UID, owner_hasn_id=_HASN)


@pytest_asyncio.fixture
async def session():
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


async def test_insight_writeback_full(session) -> None:
    """proposed_action 三处回写全跑通 + action_taken 留痕。"""
    scope = _scope()
    # 自有 playbook（非内置）→ 可被 patch
    pb = Playbook(user_id=_UID, name='我的打法', is_builtin=False, goal='涨粉', tone_guide='温暖')
    session.add(pb)
    await session.flush()

    proj = await creator_service.create_project(session, user_id=_UID, scope=scope, name='美食号', playbook_id=pb.id)
    pid = proj['id']
    # 画像设支柱（pillar_weights 初始为空，由进化管理）
    await creator_service.set_profile(
        session, user_id=_UID, scope=scope, project_id=pid, fields={'content_pillars': ['教程', '探店']}
    )

    insight = await creator_service.log_insight(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        insight_type='pillar_performance',
        summary='教程类互动率是探店 2.3 倍',
        period='2026-W24',
        proposed_action={
            'pillar_weight_delta': {'教程': 0.3, '探店': -0.5},  # 探店应被钳到 0
            'new_viral_pattern': {
                'name': '3步搞定X',
                'pattern_type': 'title',
                'template': '3步搞定{X}',
                'success_rate': 88,
            },
            'playbook_patch': {'tone_guide': '温暖治愈', 'goal': '深耕教程'},
        },
        confidence=0.7,
        created_by_agent_id='hasn:agent:evo',
    )

    # ① pillar_weights 累加 + 钳零
    prof = await creator_service.get_profile(session, user_id=_UID, scope=scope, project_id=pid)
    assert prof['pillar_weights'] == {'教程': 0.3, '探店': 0.0}
    assert prof['pillar_weights_updated_at'] is not None
    assert insight['action_taken']['pillar_weights'] == {'教程': 0.3, '探店': 0.0}

    # ② viral_pattern 入库且 pattern.search 命中（source=ai_extracted）
    vp_id = insight['action_taken']['viral_pattern_id']
    assert vp_id
    patterns = await creator_service.search_patterns(
        session, user_id=_UID, scope=scope, project_id=pid, pattern_type='title'
    )
    hit = [p for p in patterns if p['id'] == vp_id]
    assert hit and hit[0]['name'] == '3步搞定X'
    assert hit[0]['source'] == 'ai_extracted'
    assert hit[0]['is_builtin'] is False

    # ③ playbook patch（自有）
    assert set(insight['action_taken']['playbook_patched']) == {'tone_guide', 'goal'}
    refreshed = (await session.execute(select(Playbook).where(Playbook.id == pb.id))).scalars().first()
    assert refreshed.tone_guide == '温暖治愈'
    assert refreshed.goal == '深耕教程'

    # 留痕：evidence 存原文
    assert insight['evidence_json']['proposed_action']['pillar_weight_delta'] == {'教程': 0.3, '探店': -0.5}


async def test_insight_writeback_delta_accumulates(session) -> None:
    """同支柱多轮 delta 累加（不是覆盖）。"""
    scope = _scope()
    proj = await creator_service.create_project(session, user_id=_UID, scope=scope, name='号')
    pid = proj['id']
    await creator_service.log_insight(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        insight_type='pillar_performance',
        summary='r1',
        proposed_action={'pillar_weight_delta': {'教程': 0.2}},
    )
    await creator_service.log_insight(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        insight_type='pillar_performance',
        summary='r2',
        proposed_action={'pillar_weight_delta': {'教程': 0.15}},
    )
    prof = await creator_service.get_profile(session, user_id=_UID, scope=scope, project_id=pid)
    assert prof['pillar_weights'] == {'教程': 0.35}


async def test_insight_skips_builtin_playbook(session) -> None:
    """内置 playbook 不可改 → 如实跳过（零 fake，记 skip 原因）。"""
    scope = _scope()
    builtin = Playbook(user_id=None, name='内置打法', is_builtin=True, goal='通用')
    session.add(builtin)
    await session.flush()
    proj = await creator_service.create_project(session, user_id=_UID, scope=scope, name='号', playbook_id=builtin.id)
    insight = await creator_service.log_insight(
        session,
        user_id=_UID,
        scope=scope,
        project_id=proj['id'],
        insight_type='lesson',
        summary='想改内置',
        proposed_action={'playbook_patch': {'goal': '篡改'}},
    )
    assert 'playbook_patched' not in insight['action_taken']
    assert insight['action_taken']['playbook_skipped']
    refreshed = (await session.execute(select(Playbook).where(Playbook.id == builtin.id))).scalars().first()
    assert refreshed.goal == '通用'  # 未被篡改


async def test_insight_no_action_just_records(session) -> None:
    """无 proposed_action → 仅落库洞察，action_taken 空（不假装回写）。"""
    scope = _scope()
    proj = await creator_service.create_project(session, user_id=_UID, scope=scope, name='号')
    insight = await creator_service.log_insight(
        session, user_id=_UID, scope=scope, project_id=proj['id'], insight_type='audience', summary='纯记录'
    )
    assert insight['action_taken'] == {}
    assert insight['insight_type'] == 'audience'
