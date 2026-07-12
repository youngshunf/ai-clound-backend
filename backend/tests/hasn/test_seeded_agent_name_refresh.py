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
    compute_user_md_owner_refresh,
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
    """已是目标名（旧 `·` 格式，无 profession 回退路径）→ 返回 None（避免无谓 churn）。"""
    assert compute_seeded_name_refresh('星创·杨大宝', new_nickname='杨大宝', previous_nickname='杨大宝') is None


# ── 专家名感知（新格式 `{昵称}的{专家名}`）+ 遗留 `·` 存量迁移（issue②③） ──


def test_refresh_new_format_from_placeholder() -> None:
    """纯专家名占位（主人未设昵称时）+ 主人设昵称 → 刷成 `{昵称}的{专家名}`（issue③核心）。"""
    assert (
        compute_seeded_name_refresh('全能助理', profession='全能助理', new_nickname='小智', previous_nickname=None)
        == '小智的全能助理'
    )


def test_refresh_new_format_from_uniquified_placeholder() -> None:
    """带唯一化数字尾的占位（全能助理2，并发新用户）→ 同样识别并刷成新格式。"""
    assert (
        compute_seeded_name_refresh('全能助理2', profession='全能助理', new_nickname='小智', previous_nickname=None)
        == '小智的全能助理'
    )


def test_refresh_new_format_rewrites_previous_nickname() -> None:
    """新格式 `{旧昵称}的{专家名}` + 主人改名 → 刷成 `{新昵称}的{专家名}`。"""
    assert (
        compute_seeded_name_refresh('小福的全能助理', profession='全能助理', new_nickname='福仔', previous_nickname='小福')
        == '福仔的全能助理'
    )


def test_refresh_migrates_legacy_dot_form_to_new_format() -> None:
    """遗留 `{基名}·{手机号掩码}` 存量分身 + 有 profession → 迁移到新格式 `{昵称}的{专家名}`（issue②统一）。"""
    assert (
        compute_seeded_name_refresh('星创·186****2019', profession='内容运营官', new_nickname='杨大宝', previous_nickname=None)
        == '杨大宝的内容运营官'
    )


def test_refresh_new_format_skips_user_named() -> None:
    """含专家名但主人标识片段是用户手取（我的全能助理）→ 不动，绝不 clobber。"""
    assert (
        compute_seeded_name_refresh('我的全能助理', profession='全能助理', new_nickname='小智', previous_nickname=None) is None
    )


def test_refresh_new_format_noop_when_already_target() -> None:
    """已是目标名 `{昵称}的{专家名}` → 返回 None（避免无谓 churn）。"""
    assert (
        compute_seeded_name_refresh('小智的全能助理', profession='全能助理', new_nickname='小智', previous_nickname=None) is None
    )


# ─────────── USER.md 称呼刷新纯逻辑（分身把主人叫成手机号掩码的 bug） ───────────

# 模拟建档渲染后的 USER.md（hub templates/USER.md 把 {{owner_nickname}} 渲染成当时昵称）。
_USER_MD_PHONE = '称呼: 186****2019\n§\nOwner HASN ID: h_abc\n§\n关于主人: 待补充。'


def test_user_md_refresh_rewrites_phone_mask() -> None:
    """称呼行是手机号掩码 → 刷成真实昵称（核心 bug：分身按掩码称呼主人）。"""
    out = compute_user_md_owner_refresh(_USER_MD_PHONE, new_nickname='杨大宝', previous_nickname=None)
    assert out is not None
    assert '称呼: 杨大宝' in out
    assert '186****2019' not in out
    # 正文其余部分原样保留（只动称呼行）
    assert 'Owner HASN ID: h_abc' in out
    assert '关于主人: 待补充。' in out


def test_user_md_refresh_rewrites_previous_nickname() -> None:
    """称呼行是旧昵称 + 提供 previous_nickname → 刷成新昵称（改名场景）。"""
    user_md = '称呼: 小福\n§\n关于主人: 喜欢喝咖啡。'
    out = compute_user_md_owner_refresh(user_md, new_nickname='福仔', previous_nickname='小福')
    assert out is not None
    assert '称呼: 福仔' in out
    # 正文里的「小福」不属于称呼行 → 不被全局替换误伤（这里正文无小福，仅验称呼行已换）
    assert out.count('小福') == 0


def test_user_md_refresh_full_width_colon() -> None:
    """称呼行用全角冒号也能识别。"""
    out = compute_user_md_owner_refresh('称呼：186****2019', new_nickname='杨大宝', previous_nickname=None)
    assert out == '称呼：杨大宝'


def test_user_md_refresh_skips_user_chosen_label() -> None:
    """称呼行已是别的内容（既非掩码也非旧昵称）→ 不动（主人手改过 / LLM 合并写过）。"""
    user_md = '称呼: 老板\n§\n关于主人: ...'
    assert compute_user_md_owner_refresh(user_md, new_nickname='杨大宝', previous_nickname=None) is None


def test_user_md_refresh_does_not_touch_body_occurrences() -> None:
    """旧昵称在正文里也出现时，只换称呼行、绝不全局替换正文（避免误伤）。"""
    user_md = '称呼: 小福\n§\n关于主人: 同事也叫他小福老师。'
    out = compute_user_md_owner_refresh(user_md, new_nickname='福仔', previous_nickname='小福')
    assert out is not None
    assert out.startswith('称呼: 福仔')
    assert '同事也叫他小福老师。' in out  # 正文原样保留


def test_user_md_refresh_skips_blank_or_masked_new_nickname() -> None:
    """新昵称为空 / 仍是手机号掩码 → 不动（新用户首登短路）。"""
    assert compute_user_md_owner_refresh(_USER_MD_PHONE, new_nickname='', previous_nickname=None) is None
    assert compute_user_md_owner_refresh(_USER_MD_PHONE, new_nickname='186****2019', previous_nickname=None) is None


def test_user_md_refresh_noop_on_empty_or_already_target() -> None:
    """空 user_md / 称呼行已是新昵称 → 返回 None（避免无谓 churn）。"""
    assert compute_user_md_owner_refresh(None, new_nickname='杨大宝', previous_nickname=None) is None
    assert compute_user_md_owner_refresh('称呼: 杨大宝', new_nickname='杨大宝', previous_nickname=None) is None


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


async def _register_builtin_agent(
    session, owner: str, *, key: str, display_name: str, role: str, profession: str | None = None
) -> HasnAgents:
    result = await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name=key,
        display_name=display_name,
        role=role,
        builtin_agent_key=key,
        profession=profession,
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
async def test_refresh_new_format_and_legacy_migration_in_db(session) -> None:
    """有 profession 的默认分身：占位/遗留 `·` 形态 → 刷成新格式 `{昵称}的{专家名}`（issue②③）。"""
    # 昵称唯一化（hasn_humans.nickname 有唯一约束，避开存量真实账号如「小智」）；专家名也带 tag 避免
    # display_name 撞存量库。断言据此动态推导，逻辑等价于「小智的全能助理」。
    tag = _uid()
    nick = f'测试用户{tag}'
    prof_a, prof_b = f'全能助理{tag}', f'内容运营官{tag}'
    owner = await _make_owner(session, nickname=nick)
    # 占位形态（主人未设昵称时建的分身，专家名占位）
    placeholder = await _register_builtin_agent(
        session, owner, key='assistant', display_name=prof_a, role='primary', profession=prof_a
    )
    # 遗留 `·` 形态（存量分身，手机号后缀）
    legacy = await _register_builtin_agent(
        session, owner, key='content_operator', display_name='星创·186****2019', role='specialist', profession=prof_b
    )
    rev_before = legacy.profile_revision or 1

    renamed = await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname=nick
    )

    assert set(renamed) == {f'{nick}的{prof_a}', f'{nick}的{prof_b}'}
    by_key = await _reload(session, owner)
    assert by_key['assistant'].display_name == f'{nick}的{prof_a}'  # 占位 → 新格式
    assert by_key['content_operator'].display_name == f'{nick}的{prof_b}'  # 遗留 `·` → 迁移到新格式
    assert (by_key['content_operator'].profile_revision or 1) == rev_before + 1
    _ = placeholder


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


@pytest.mark.asyncio
async def test_refresh_rewrites_user_md_owner_label_in_db(session: AsyncSession) -> None:
    """端到端 bug 场景：建档时主人未设昵称→USER.md 烙进手机号掩码；主人改昵称后 refresh
    把 user_md 称呼行刷成真实昵称，且非内置分身的 user_md 也一并刷新（owner 维度）。"""
    from sqlalchemy import update

    # 1) 建档时主人 nickname 仍是手机号掩码 → {{owner_nickname}} 渲染成掩码烙进 user_md。
    owner = await _make_owner(session, nickname='186****2019')
    user_md_tpl = '称呼: {{owner_nickname}}\n§\nOwner HASN ID: {{owner_id}}\n§\n关于主人: 待补充。'
    builtin = await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name='assistant',
        display_name='星诺·186****2019',
        role='primary',
        builtin_agent_key='assistant',
        agent_type='cloud',
        created_via='onboarding',
        user_md=user_md_tpl,
    )
    # 非内置分身（主人手建）同样在掩码窗口期烙进掩码 → user_md 也应被刷新（display_name 不动）。
    custom = await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name='my_helper',
        display_name='我的小助理',
        role='specialist',
        agent_type='cloud',
        created_via='client',
        user_md=user_md_tpl,
    )
    assert '称呼: 186****2019' in builtin['agent'].user_md  # 渲染确实烙进了掩码
    assert '称呼: 186****2019' in custom['agent'].user_md
    rev_before = custom['agent'].profile_revision or 1

    # 2) 主人填写真实昵称（HasnHumans.nickname 改为杨大宝），再走 refresh（profile 更新路径）。
    await session.execute(update(HasnHumans).where(HasnHumans.hasn_id == owner).values(nickname='杨大宝'))
    await session.flush()
    await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝', previous_nickname='186****2019'
    )

    # 3) 断言：两个分身的 USER.md 称呼都刷成真实昵称（分身不再按手机号掩码称呼主人）。
    agents = (await session.execute(select(HasnAgents).where(HasnAgents.owner_id == owner))).scalars().all()
    rows = {a.agent_name: a for a in agents}
    assert '称呼: 杨大宝' in rows['assistant'].user_md
    assert '186****2019' not in rows['assistant'].user_md
    assert '称呼: 杨大宝' in rows['my_helper'].user_md  # 非内置分身 user_md 也刷新（owner 维度）
    assert '186****2019' not in rows['my_helper'].user_md
    # 非内置分身 display_name 不被改（用户手建的名字原样保留）
    assert rows['my_helper'].display_name == '我的小助理'
    # 改了 user_md → profile_revision 自增（Runtime 据此重拉 USER.md）
    assert (rows['my_helper'].profile_revision or 1) == rev_before + 1


@pytest.mark.asyncio
async def test_refresh_user_md_idempotent_in_db(session: AsyncSession) -> None:
    """重复 refresh 幂等：第二次 user_md 已是真实昵称 → 不再改、不再 bump revision。"""
    from sqlalchemy import update

    owner = await _make_owner(session, nickname='186****2019')
    agent = await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name='assistant',
        display_name='星诺·186****2019',
        role='primary',
        builtin_agent_key='assistant',
        agent_type='cloud',
        created_via='onboarding',
        user_md='称呼: {{owner_nickname}}\n§\n关于主人: 待补充。',
    )
    await session.execute(update(HasnHumans).where(HasnHumans.hasn_id == owner).values(nickname='杨大宝'))
    await session.flush()

    await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝', previous_nickname='186****2019'
    )
    rev_after_first = (await _reload(session, owner))['assistant'].profile_revision

    await agent_profile_service.refresh_seeded_agent_display_names(
        session, owner_id=owner, current_nickname='杨大宝', previous_nickname='186****2019'
    )
    after = (await _reload(session, owner))['assistant']
    assert '称呼: 杨大宝' in after.user_md
    assert after.profile_revision == rev_after_first  # 第二次无改动 → revision 不再涨
    _ = agent
