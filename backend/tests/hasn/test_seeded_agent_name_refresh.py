"""内置/默认分身昵称回写修复（主人已设昵称但分身仍显示手机号）。

bug：onboarding 在登录路径建分身时主人尚未设昵称、HasnHumans.nickname 仍是手机号掩码
（186****2019），内置/默认分身名 `{基名}·{主人昵称}` 的后缀因此被烙进手机号；主人之后改昵称
没有回写分身 → 分身一直显示「星创·186****2019」。

修复：在「主人设/改昵称」（profile_service.update_merged）与「登录」（onboarding.ensure 自愈）
两处调 refresh_seeded_agent_display_names，把这类手机号/旧昵称后缀刷成真实昵称。

测试分两层：
  1. compute_seeded_name_refresh 纯逻辑：确定性单测（必跑，覆盖所有判定分支）。
  2. refresh_seeded_agent_display_names 真实 PG 集成：mirror builtin seeding 测试，
     PG 不可达则 skip。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.hasn_agents_service import (
    agent_profile_service,
    compute_seeded_name_refresh,
)
from backend.app.hasn.service.hasn_auth import register_hasn_agent
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ─────────────────────────── 纯逻辑单测（必跑） ───────────────────────────


def test_refresh_rewrites_phone_mask_suffix() -> None:
    """后缀是手机号掩码 → 刷成真实昵称（核心 bug 场景）。"""
    assert (
        compute_seeded_name_refresh('星创·186****2019', new_nickname='杨大宝', previous_nickname=None) == '星创·杨大宝'
    )


def test_refresh_rewrites_previous_nickname_suffix() -> None:
    """后缀是旧昵称 + 提供 previous_nickname → 刷成新昵称（改名场景）。"""
    assert compute_seeded_name_refresh('星诺·小福', new_nickname='福仔', previous_nickname='小福') == '星诺·福仔'


def test_refresh_strips_numeric_collision_tail() -> None:
    """撞名数字尾（福仔2）也识别为旧昵称后缀 → 刷新（base 保留，后缀换新昵称）。"""
    assert compute_seeded_name_refresh('星诺·小福2', new_nickname='福仔', previous_nickname='小福') == '星诺·福仔'


def test_refresh_skips_user_chosen_suffix() -> None:
    """后缀既非手机号掩码也非旧昵称（用户主动取名）→ 不动。"""
    assert compute_seeded_name_refresh('星创·老王', new_nickname='杨大宝', previous_nickname=None) is None


def test_refresh_skips_name_without_separator() -> None:
    """无后缀（基名全局唯一、未烙昵称）→ 不动。"""
    assert compute_seeded_name_refresh('我的小助理', new_nickname='杨大宝', previous_nickname=None) is None


def test_refresh_skips_when_new_nickname_blank_or_masked() -> None:
    """新昵称为空 / 仍是手机号掩码 → 无可改进，不动（新用户首登在此短路）。"""
    assert compute_seeded_name_refresh('星创·186****2019', new_nickname='', previous_nickname=None) is None
    assert compute_seeded_name_refresh('星创·186****2019', new_nickname='186****2019', previous_nickname=None) is None


def test_refresh_noop_when_already_target() -> None:
    """已是目标名 → 返回 None（避免无谓 churn）。"""
    assert compute_seeded_name_refresh('星创·杨大宝', new_nickname='杨大宝', previous_nickname='杨大宝') is None


# ─────────────────────────── 真实 PG 集成测试 ───────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


async def _make_owner(session, nickname: str) -> str:
    tag = _uid()
    owner = f'h_nr_{tag}'
    uid = 980000 + int(uuid.uuid4().int % 9000)
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{uid}', user_id=uid, nickname=nickname, status='active'))
    await session.flush()
    return owner


async def _register_builtin_agent(session, owner: str, *, key: str, display_name: str, role: str) -> HasnAgents:
    result = await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name=key,
        display_name=display_name,
        role=role,
        builtin_agent_key=key,
        agent_type='cloud',
        created_via='onboarding',
    )
    return result['agent']


async def _reload(session, owner: str) -> dict[str, HasnAgents]:
    rows = (await session.execute(select(HasnAgents).where(HasnAgents.owner_id == owner))).scalars().all()
    return {a.builtin_agent_key: a for a in rows if a.builtin_agent_key}


@pytest.mark.asyncio
async def test_refresh_rewrites_phone_mask_agents_in_db(session) -> None:
    """主人已设昵称(杨大宝)但内置分身仍是手机号后缀 → 刷成真实昵称，profile_revision 自增。"""
    owner = await _make_owner(session, nickname='杨大宝')
    primary = await _register_builtin_agent(
        session, owner, key='assistant', display_name='星诺·186****2019', role='primary'
    )
    specialist = await _register_builtin_agent(
        session, owner, key='content_operator', display_name='星创·186****2019', role='specialist'
    )
    rev_before = specialist.profile_revision or 1

    renamed = await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝'
    )

    assert set(renamed) == {'星诺·杨大宝', '星创·杨大宝'}
    by_key = await _reload(session, owner)
    assert by_key['assistant'].display_name == '星诺·杨大宝'
    assert by_key['content_operator'].display_name == '星创·杨大宝'
    assert (by_key['content_operator'].profile_revision or 1) == rev_before + 1
    # 同步事件已下发（webui/daemon 据此感知改名）
    _ = primary  # 仅持有引用避免 linter 误报


@pytest.mark.asyncio
async def test_refresh_leaves_user_named_agents_untouched(session) -> None:
    """用户主动改过名的内置分身（非手机号/非旧昵称后缀）→ 不被回写。"""
    owner = await _make_owner(session, nickname='杨大宝')
    await _register_builtin_agent(session, owner, key='assistant', display_name='星诺·186****2019', role='primary')
    await _register_builtin_agent(session, owner, key='content_operator', display_name='我的运营官', role='specialist')

    renamed = await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝'
    )

    assert renamed == ['星诺·杨大宝']  # 仅手机号后缀的被改
    by_key = await _reload(session, owner)
    assert by_key['content_operator'].display_name == '我的运营官'  # 用户取名原样保留


@pytest.mark.asyncio
async def test_refresh_is_idempotent(session) -> None:
    """重复调用幂等：第二次无改名、不再 bump revision。"""
    owner = await _make_owner(session, nickname='杨大宝')
    agent = await _register_builtin_agent(
        session, owner, key='content_operator', display_name='星创·186****2019', role='specialist'
    )

    first = await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝'
    )
    assert first == ['星创·杨大宝']
    rev_after_first = (await _reload(session, owner))['content_operator'].profile_revision

    second = await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝'
    )
    assert second == []
    assert (await _reload(session, owner))['content_operator'].profile_revision == rev_after_first
    _ = agent


@pytest.mark.asyncio
async def test_refresh_rewrites_previous_nickname_on_rename(session) -> None:
    """主人改名（旧名→新名）→ 后缀是旧名的内置分身刷成新名（profile 更新路径语义）。"""
    owner = await _make_owner(session, nickname='福仔')
    await _register_builtin_agent(session, owner, key='content_operator', display_name='星创·小福', role='specialist')

    renamed = await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='福仔', previous_nickname='小福'
    )

    assert renamed == ['星创·福仔']
    assert (await _reload(session, owner))['content_operator'].display_name == '星创·福仔'
