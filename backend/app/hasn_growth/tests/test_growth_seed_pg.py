"""获客 M5 内置 seed 真实 PG 测试（零 mock，回滚）。

覆盖（设计 07 §7.4/§7.5，实施 91 M5）：
- hasn_growth.playbook 内置 3 套（user_id IS NULL + is_builtin）；
- hasn_task.builtin_catalog 内置 4 个获客任务，**全 enabled=FALSE**（opt-in，不自动推给所有主脑）；
- seed 幂等（同一事务内连跑两遍，计数不变、不重复）；
- 关键不变式：builtin_task_service.list_enabled() **绝不包含** growth_* 键
  （否则 daemon 会把获客任务无条件播种给每个主脑——破坏「获客是 opt-in 能力」）。

需要 export DATABASE_PORT=15432。本测试在事务内 apply seed 后回滚，不污染库。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.service.builtin_task_service import workbench_builtin_task_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_PLAYBOOK_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-06-12-seed-builtin-playbooks.sql'
_TASKS_SQL = _REPO / 'backend/sql/hasn_task/migrations/2026-06-12-seed-builtin-growth-tasks.sql'

_BUILTIN_PLAYBOOK_NAMES = {'标准 B2B 获客', '高意向快跟', '长周期培育'}
_GROWTH_TASK_KEYS = {
    'growth_daily_briefing',
    'growth_weekly_pipeline',
    'growth_silent_revive',
    'growth_opportunity_reminder',
}


async def _apply_seed(session) -> None:
    """经底层 asyncpg 连接跑多语句 seed SQL（simple query 协议，避开 text() 的 `:` 绑定解析）。"""
    raw = await (await session.connection()).get_raw_connection()
    asyncpg_conn = raw.driver_connection
    await asyncpg_conn.execute(_PLAYBOOK_SQL.read_text(encoding='utf-8'))
    await asyncpg_conn.execute(_TASKS_SQL.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
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


async def _count(session, sql: str) -> int:
    return int((await session.execute(text(sql))).scalar() or 0)


async def test_builtin_seed_applies_and_is_idempotent(session) -> None:
    # 跑两遍 seed（幂等）：内置 playbook 与获客任务计数应稳定，不重复
    await _apply_seed(session)
    pb1 = await _count(session, "SELECT count(*) FROM hasn_growth.playbook WHERE is_builtin AND user_id IS NULL")
    gt1 = await _count(session, "SELECT count(*) FROM hasn_task.builtin_catalog WHERE builtin_key LIKE 'growth\\_%'")

    await _apply_seed(session)
    pb2 = await _count(session, "SELECT count(*) FROM hasn_growth.playbook WHERE is_builtin AND user_id IS NULL")
    gt2 = await _count(session, "SELECT count(*) FROM hasn_task.builtin_catalog WHERE builtin_key LIKE 'growth\\_%'")

    assert pb1 == pb2, '内置 playbook 幂等：再跑一遍数量不应变'
    assert gt1 == gt2, '获客内置任务幂等：再跑一遍数量不应变'
    assert pb1 >= 3, f'应至少有 3 套内置 playbook，实得 {pb1}'
    assert gt1 == 4, f'应有 4 个获客内置任务，实得 {gt1}'

    # 内置 playbook 名称齐全
    names = set(
        (await session.execute(text("SELECT name FROM hasn_growth.playbook WHERE is_builtin AND user_id IS NULL"))).scalars().all()
    )
    assert names >= _BUILTIN_PLAYBOOK_NAMES, f'缺内置 playbook：{_BUILTIN_PLAYBOOK_NAMES - names}'


async def test_growth_tasks_are_all_disabled(session) -> None:
    """获客内置任务必须全 enabled=FALSE（opt-in），且 skill_bundle 指向 growth-playbook。"""
    await _apply_seed(session)
    rows = (
        await session.execute(
            text(
                "SELECT builtin_key, enabled, skill_bundle FROM hasn_task.builtin_catalog "
                "WHERE builtin_key LIKE 'growth\\_%'"
            )
        )
    ).all()
    assert {r[0] for r in rows} == _GROWTH_TASK_KEYS
    assert all(r[1] is False for r in rows), '获客内置任务必须 enabled=FALSE，绝不自动推给所有主脑'
    assert all(r[2] == 'huanxing/growth-playbook' for r in rows), 'skill_bundle 应指向 growth-playbook'


async def test_list_enabled_excludes_growth_tasks(session) -> None:
    """关键不变式：list_enabled（daemon 据此播种到每个主脑）绝不含 growth_* 键。"""
    await _apply_seed(session)
    catalog = await workbench_builtin_task_service.list_enabled(session)
    enabled_keys = {item.builtin_key for item in catalog.items}
    assert not (enabled_keys & _GROWTH_TASK_KEYS), (
        f'获客任务泄漏进 list_enabled，会被推给所有主脑：{enabled_keys & _GROWTH_TASK_KEYS}'
    )
