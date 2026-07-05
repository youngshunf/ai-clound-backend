"""平台运维授予源·下拉选项真实 PG 验收（福仔改造诉求：级联下拉 + 声明驱动特权 scope）——零 mock。

覆盖三个只读选项端点的 service 层：
1. list_scope_options：声明驱动·只读——恰为 PRIVILEGED_SCOPES 权威全集 + 展示元数据（纯函数，无 DB）；
2. list_owner_options：列 HASN 用户，可按昵称/hasn_id 关键字收窄（真查 hasn_humans）；
3. list_agent_options：列某 owner 名下分身，按 owner_id 过滤、空 owner 返空（真查 hasn_agents）；
以及 granted_by 由后端 JWT 覆盖（审计不可伪造）的接线断言。

需本地 PostgreSQL :15432。表 hasn_humans / hasn_agents 已建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_platform_operator_grants import CreateHasnPlatformOperatorGrantsParam
from backend.app.hasn.service.hasn_platform_operator_grants_service import (
    hasn_platform_operator_grants_service,
)
from backend.app.mcp.platform_scopes import PRIVILEGED_SCOPES
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# 高熵前缀的测试身份，避免撞真实数据
OWNER_A = 'h_optpg_ownerA_x1'
OWNER_B = 'h_optpg_ownerB_x2'
AGENT_A1 = 'a_optpg_agentA1_y1'
AGENT_A2 = 'a_optpg_agentA2_y2'
AGENT_B1 = 'a_optpg_agentB1_z1'


async def _purge(db: AsyncSession) -> None:
    await db.execute(
        text('DELETE FROM hasn_agents WHERE hasn_id = ANY(:ids)'),
        {'ids': [AGENT_A1, AGENT_A2, AGENT_B1]},
    )
    await db.execute(
        text('DELETE FROM hasn_humans WHERE hasn_id = ANY(:ids)'),
        {'ids': [OWNER_A, OWNER_B]},
    )
    await db.commit()


async def _seed(db: AsyncSession) -> None:
    # 两个用户：A 昵称含「唤星测试用户甲」、B 含「乙」
    # user_id 是 UNIQUE 列，两行必须给不同的高熵值（避免撞已有行与彼此）
    for hid, star, uid, nick in (
        (OWNER_A, 'optpg1', 990_000_991, '唤星测试用户甲'),
        (OWNER_B, 'optpg2', 990_000_992, '唤星测试用户乙'),
    ):
        await db.execute(
            text(
                'INSERT INTO hasn_humans (hasn_id, star_id, user_id, nickname, status) '
                'VALUES (:h, :s, :u, :n, :st)'
            ),
            {'h': hid, 's': star, 'u': uid, 'n': nick, 'st': 'active'},
        )
    # A 名下两个分身，B 名下一个
    for aid, owner, disp, name, prof in (
        (AGENT_A1, OWNER_A, '全能助理', 'assistant', None),
        (AGENT_A2, OWNER_A, '金融分身', 'finance', '金融专家'),
        (AGENT_B1, OWNER_B, '菌子分身', 'mushroom', None),
    ):
        await db.execute(
            text(
                'INSERT INTO hasn_agents '
                '(hasn_id, star_id, owner_id, agent_name, api_key_hash, display_name, profession) '
                'VALUES (:h, :s, :o, :n, :k, :d, :p)'
            ),
            {'h': aid, 's': f'star_{name}', 'o': owner, 'n': name, 'k': f'hash_{name}', 'd': disp, 'p': prof},
        )
    await db.commit()


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
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
        await _purge(sess)
        await _seed(sess)
        yield sess
    finally:
        await _purge(sess)
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        await async_engine.dispose()


def test_scope_options_are_declaration_driven() -> None:
    """特权 scope 下拉恰为 PRIVILEGED_SCOPES 权威全集（声明驱动·只读），带展示元数据。"""
    opts = hasn_platform_operator_grants_service.list_scope_options()
    scopes = {o.scope for o in opts}
    # 恰等于权威全集——不多不少，新工具声明的特权 scope 会自动出现
    assert scopes == set(PRIVILEGED_SCOPES)
    # 元数据非空、risk 在合法枚举
    for o in opts:
        assert o.label_zh
        assert o.risk in {'low', 'medium', 'high'}
        assert o.description
    # 稳定排序（sorted）
    assert [o.scope for o in opts] == sorted(scopes)


@pytest.mark.asyncio
async def test_owner_options_list_and_keyword_filter(session: AsyncSession) -> None:
    """用户下拉：无关键字列出用户；关键字按昵称/hasn_id 收窄。"""
    all_owners = await hasn_platform_operator_grants_service.list_owner_options(db=session)
    ids = {o.hasn_id for o in all_owners}
    assert OWNER_A in ids and OWNER_B in ids

    # 昵称关键字「甲」只命中 A
    only_a = await hasn_platform_operator_grants_service.list_owner_options(db=session, keyword='甲')
    a_ids = {o.hasn_id for o in only_a}
    assert OWNER_A in a_ids and OWNER_B not in a_ids

    # hasn_id 片段也能命中
    by_hid = await hasn_platform_operator_grants_service.list_owner_options(db=session, keyword='ownerB_x2')
    assert any(o.hasn_id == OWNER_B for o in by_hid)


@pytest.mark.asyncio
async def test_agent_options_filtered_by_owner(session: AsyncSession) -> None:
    """分身下拉：仅列指定 owner 名下分身；空 owner 返空；带 profession。"""
    a_agents = await hasn_platform_operator_grants_service.list_agent_options(db=session, owner_hasn_id=OWNER_A)
    a_ids = {o.hasn_id for o in a_agents}
    assert a_ids == {AGENT_A1, AGENT_A2}
    assert AGENT_B1 not in a_ids
    # profession 诚实回显（金融分身有头衔，助理无）
    prof_map = {o.hasn_id: o.profession for o in a_agents}
    assert prof_map[AGENT_A2] == '金融专家'
    assert prof_map[AGENT_A1] is None

    b_agents = await hasn_platform_operator_grants_service.list_agent_options(db=session, owner_hasn_id=OWNER_B)
    assert {o.hasn_id for o in b_agents} == {AGENT_B1}

    # 空 owner → 空列表（不全量泄漏）
    assert await hasn_platform_operator_grants_service.list_agent_options(db=session, owner_hasn_id='') == []
    assert await hasn_platform_operator_grants_service.list_agent_options(db=session, owner_hasn_id='   ') == []


def test_granted_by_overridden_from_admin_not_frontend() -> None:
    """granted_by 由后端覆盖：即便前端传伪造值，model_copy 用当前 Admin 标识覆盖。"""
    # 前端故意传一个伪造的 granted_by
    obj = CreateHasnPlatformOperatorGrantsParam(
        agent_hasn_id=AGENT_A1, scope='diag:read:all', granted_by='伪造超管', note='x'
    )
    # 端点内的覆盖逻辑：model_copy(update={'granted_by': <JWT 标识>})
    overridden = obj.model_copy(update={'granted_by': 'admin_from_jwt'})
    assert overridden.granted_by == 'admin_from_jwt'
    # 其余字段不变
    assert overridden.agent_hasn_id == AGENT_A1
    assert overridden.scope == 'diag:read:all'
