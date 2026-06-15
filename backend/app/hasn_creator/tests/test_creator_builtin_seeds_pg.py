"""创作运营 M9 内置 seed 真实 PG 验收（内置任务目录 + 内置 playbook，零 mock，回滚）。

覆盖（设计 00 §7/§8，实施 91 M9）：
- 内置任务目录 seed：creator_operate/creator_reflect/creator_topic_radar 三条，全 enabled=FALSE
  （opt-in，不污染存量用户任务集）、skill_bundle=huanxing/creator-playbook、revision=1。
- 内置 playbook seed：种草号/科普号/品牌号三条，is_builtin=TRUE、user_id/enterprise_id 均 NULL、
  content_strategy 含 pillars + pillar_weights、partial unique index 去重（每 name 仅 1 条）。
- 企业开通自播种采用真实内置模板：ensure_creator_enterprise_seeded 复制 ≥3 条企业副本，重复幂等。

前置：两份 seed 迁移已应用到本地 :15432（部署制品）：
  backend/sql/hasn_task/migrations/2026-06-15-seed-builtin-creator-tasks.sql
  backend/sql/hasn_creator/migrations/2026-06-15-seed-builtin-playbooks.sql
SQL 自身幂等由 ON CONFLICT + partial unique index 保证（已双跑验证）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_creator.model.playbook import Playbook
from backend.app.hasn_creator.service.enterprise_seed_service import ensure_creator_enterprise_seeded
from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_BUILTIN_TASK_KEYS = {'creator_operate', 'creator_reflect', 'creator_topic_radar'}
_BUILTIN_PLAYBOOK_NAMES = {'种草号', '科普号', '品牌号'}
_ENT = 924077


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


async def test_builtin_creator_tasks_seeded(session) -> None:
    """3 条内置创作任务 enabled=FALSE（opt-in）、技能包=creator-playbook、revision=1。"""
    rows = (
        (
            await session.execute(
                select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key.in_(_BUILTIN_TASK_KEYS))
            )
        )
        .scalars()
        .all()
    )
    assert {r.builtin_key for r in rows} == _BUILTIN_TASK_KEYS, '内置创作任务未全部 seed（迁移未应用？）'
    for r in rows:
        assert r.enabled is False, f'{r.builtin_key} 必须 enabled=FALSE（opt-in，零存量 churn）'
        assert r.skill_bundle == 'huanxing/creator-playbook'
        assert r.revision == 1
        assert r.system_prompt and 'hasn.creator.' in r.system_prompt
    # 调度类型对齐设计 §8.2：operate=interval，reflect/radar=cron
    by_key = {r.builtin_key: r for r in rows}
    assert by_key['creator_operate'].schedule_type == 'interval'
    assert by_key['creator_reflect'].schedule_type == 'cron'
    assert by_key['creator_topic_radar'].schedule_type == 'cron'


async def test_builtin_playbooks_seeded(session) -> None:
    """3 条内置 playbook：is_builtin、user/enterprise NULL、content_strategy 含 pillars+pillar_weights，去重。"""
    rows = (
        (
            await session.execute(
                select(Playbook).where(
                    Playbook.is_builtin.is_(True),
                    Playbook.user_id.is_(None),
                    Playbook.enterprise_id.is_(None),
                    Playbook.name.in_(_BUILTIN_PLAYBOOK_NAMES),
                )
            )
        )
        .scalars()
        .all()
    )
    assert {r.name for r in rows} == _BUILTIN_PLAYBOOK_NAMES, '内置 playbook 未全部 seed（迁移未应用？）'
    for r in rows:
        assert isinstance(r.content_strategy, dict)
        assert r.content_strategy.get('pillars'), f'{r.name} 缺 content_strategy.pillars'
        assert r.content_strategy.get('pillar_weights'), f'{r.name} 缺 content_strategy.pillar_weights'
        assert isinstance(r.red_lines, list) and r.red_lines, f'{r.name} 缺合规红线'
        assert isinstance(r.cadence, dict) and r.cadence.get('frequency')


async def test_builtin_playbook_partial_unique_dedup(session) -> None:
    """partial unique index 保证每个内置 playbook 名只 1 条（双跑迁移不重复）。"""
    dup = (
        await session.execute(
            select(Playbook.name, func.count())
            .where(
                Playbook.is_builtin.is_(True),
                Playbook.user_id.is_(None),
                Playbook.enterprise_id.is_(None),
                Playbook.name.in_(_BUILTIN_PLAYBOOK_NAMES),
            )
            .group_by(Playbook.name)
            .having(func.count() > 1)
        )
    ).all()
    assert dup == [], f'内置 playbook 存在重复（partial unique 失效）: {dup}'


async def test_enterprise_seed_uses_builtin_templates(session) -> None:
    """企业开通采用真实内置 playbook 模板：复制 ≥3 条企业副本（is_builtin=False），重复幂等为 0。"""
    added = await ensure_creator_enterprise_seeded(session, enterprise_id=_ENT)
    assert added >= 3, f'企业播种应复制 ≥3 条内置模板，实得 {added}（内置模板未 seed？）'
    copies = (
        (
            await session.execute(
                select(Playbook).where(
                    Playbook.enterprise_id == _ENT,
                    Playbook.name.in_(_BUILTIN_PLAYBOOK_NAMES),
                )
            )
        )
        .scalars()
        .all()
    )
    assert {c.name for c in copies} == _BUILTIN_PLAYBOOK_NAMES
    for c in copies:
        assert c.is_builtin is False, '企业副本可被主编改，非全局内置'
        assert c.user_id is None
        assert c.content_strategy.get('pillar_weights')
    # 重复开通幂等
    again = await ensure_creator_enterprise_seeded(session, enterprise_id=_ENT)
    assert again == 0
