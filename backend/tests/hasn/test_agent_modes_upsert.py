"""缺陷 1 真实 PG 回归：`update_agent_modes` 在 scopes 行缺失时**建行**而非静默丢弃。

线上根因（福仔报）：审批「总是允许」写回成功、云端回 200，但 `hasn_agent_scopes` 表里
**没有这条记录**——老 Agent 可能从未插入过 scopes 行（`create_default_agent_scopes` 在其创建
之后才引入，或迁移期遗漏），裸 `UPDATE` 在无行时影响 0 行、静默吞掉主人的权限更改。

修复（agent_jwt.update_agent_modes）：改 `INSERT...SELECT...ON CONFLICT DO UPDATE`，
owner_hasn_id 由 `hasn_agents` 现查派生（无需改调用签名）。本测试零 mock 打真实 dev PG，
显式清理自建行（update_agent_modes 内部 commit，不能靠会话 rollback）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.common.security import agent_jwt
from backend.database.db import SQLALCHEMY_DATABASE_URL


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _read_scopes_row(db: AsyncSession, agent_hasn_id: str) -> dict | None:
    result = await db.execute(
        sa.text(
            'SELECT owner_hasn_id, default_mode, capability_modes '
            'FROM hasn_agent_scopes WHERE agent_hasn_id = :aid'
        ),
        {'aid': agent_hasn_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


@pytest.mark.asyncio
async def test_update_agent_modes_creates_row_when_missing(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 scopes 行时，update_agent_modes 应据 hasn_agents 建行并写入三态（不再静默丢弃）。"""
    # redis 失效缓存与本测试无关，避免依赖 redis。
    async def _noop_invalidate(agent_hasn_id: str) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(agent_jwt, 'invalidate_agent_scopes_cache', _noop_invalidate)

    suffix = uuid.uuid4().hex[:12]
    agent_hasn_id = f'a_upsert_{suffix}'
    owner_hasn_id = f'h_upsert_{suffix}'

    try:
        # 1) 播种一个 agent，但**故意不**建 scopes 行（复现老 Agent 缺行场景）。
        #    star_id 有唯一约束、默认空串会撞既有空值 → 给唯一值。
        db.add(
            HasnAgents(
                hasn_id=agent_hasn_id,
                owner_id=owner_hasn_id,
                agent_name='upsert_test',
                star_id=f'{suffix}#star',
            )
        )
        await db.commit()
        assert await _read_scopes_row(db, agent_hasn_id) is None, '前置：scopes 行应不存在'

        # 2) 主人「总是允许」film:write（写回 capability_modes）。
        await agent_jwt.update_agent_modes(
            db,
            agent_hasn_id,
            default_mode='allow',
            capability_modes={'film:write': 'allow'},
        )

        # 3) 行被创建，owner 从 hasn_agents 派生，三态忠实落库（根因修复）。
        row = await _read_scopes_row(db, agent_hasn_id)
        assert row is not None, 'update_agent_modes 应建行而非静默丢弃'
        assert row['owner_hasn_id'] == owner_hasn_id
        assert row['default_mode'] == 'allow'
        assert row['capability_modes'] == {'film:write': 'allow'}

        # 4) 再次更新（已有行）→ ON CONFLICT DO UPDATE 覆盖，幂等。
        await agent_jwt.update_agent_modes(
            db,
            agent_hasn_id,
            default_mode='ask',
            capability_modes={'film:write': 'deny', 'plan:delegate': 'allow'},
        )
        row2 = await _read_scopes_row(db, agent_hasn_id)
        assert row2 is not None
        assert row2['default_mode'] == 'ask'
        assert row2['capability_modes'] == {'film:write': 'deny', 'plan:delegate': 'allow'}
    finally:
        # update_agent_modes 内部 commit，会话 rollback 救不回；显式清理自建行，不污染 dev DB。
        await db.execute(
            sa.text('DELETE FROM hasn_agent_scopes WHERE agent_hasn_id = :aid'),
            {'aid': agent_hasn_id},
        )
        await db.execute(
            sa.text('DELETE FROM hasn_agents WHERE hasn_id = :aid'),
            {'aid': agent_hasn_id},
        )
        await db.commit()


@pytest.mark.asyncio
async def test_update_agent_modes_noop_when_agent_absent(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent 不存在时 INSERT...SELECT 命中 0 行 → 不建孤儿 scopes 行（owner 无从派生）。"""
    async def _noop_invalidate(agent_hasn_id: str) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(agent_jwt, 'invalidate_agent_scopes_cache', _noop_invalidate)

    ghost = f'a_ghost_{uuid.uuid4().hex[:12]}'
    await agent_jwt.update_agent_modes(
        db, ghost, default_mode='allow', capability_modes={'film:write': 'allow'}
    )
    assert await _read_scopes_row(db, ghost) is None, '无 agent 时不应建孤儿 scopes 行'
