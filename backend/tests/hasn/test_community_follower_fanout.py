"""社区关注者实时 fan-out 真实 PG 集成测试（COMMRESIL-2，零 mock）。

本地优先架构下，daemon 侧社区读走 local_first_or_cloud（读本地镜像立即返回，云端
刷新只在后台做）。若云端写点（发帖/评论/点赞）不主动 bump_owner(KIND_COMMUNITY)，
关注者只能等下次冷刷新才看到新内容，达不到「后端可达即实时看到最新数据」。

验证 CommunityService._fanout_to_followers 按 hasn_follows 解析出正确的 owner 集合
（human 关注者直接是 owner；agent 关注者归并到其主人 owner_id）后调用 bump_owner，
并在 create_post/create_comment/create_like/create_article 的正确时机被调用（仅对
外可见内容才 fan-out，pending_review 待审内容不 fan-out）。

真实 PG :15432，独立 session + 末尾回滚不留脏数据；spy 替换 bump_owner 避免依赖
真实在线 WS 节点。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service import sync_invalidate_service as siv
from backend.app.hasn_community.model import HasnFollows
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_core import HasnAgents
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _spy_bump_owner(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """monkeypatch sync_invalidate_service.bump_owner，记录 (kind, owner_id) 调用。"""
    calls: list[tuple[str, str]] = []

    async def _fake(kind: str, db, owner_id: str) -> str:
        calls.append((kind, owner_id))
        return 'stub-rev'

    monkeypatch.setattr(siv, 'bump_owner', _fake)
    return calls


async def test_fanout_bumps_human_follower_owner_on_publish(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """human 关注者：发帖成功 → fan-out 直接以其 hasn_id 为 owner 调用 bump_owner。"""
    author = f'h_fanout_author_{_uid()}'
    follower_owner = f'h_fanout_follower_{_uid()}'

    pg.add(HasnFollows(follower_hasn_id=follower_owner, target_type='human', target_hasn_id=author))
    await pg.flush()

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_post(pg, user_id=1, hasn_id=author, content='fanout probe', tags=[])

    assert (siv.KIND_COMMUNITY, follower_owner) in calls


async def test_fanout_bumps_agent_follower_owner_not_agent_itself(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 关注者：发帖成功 → fan-out 解析出其主人 owner_id，而不是 agent 自身 hasn_id。"""
    author = f'h_fanout_author2_{_uid()}'
    agent_owner = f'h_fanout_agent_owner_{_uid()}'
    agent_hasn_id = f'a_fanout_follower_{_uid()}'

    pg.add(
        HasnAgents(
            hasn_id=agent_hasn_id,
            owner_id=agent_owner,
            star_id=f'{_uid()}#star',
            display_name='探针分身',
        )
    )
    pg.add(HasnFollows(follower_hasn_id=agent_hasn_id, target_type='human', target_hasn_id=author))
    await pg.flush()

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_post(pg, user_id=1, hasn_id=author, content='fanout probe agent', tags=[])

    assert (siv.KIND_COMMUNITY, agent_owner) in calls
    assert not any(owner == agent_hasn_id for _, owner in calls), 'fan-out 不应把 agent 自身 hasn_id 当 owner 推送'


async def test_fanout_dedups_multiple_agents_sharing_one_owner(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一主人名下多个分身都关注同一作者 → 只应 bump 一次该主人 owner（去重）。"""
    author = f'h_fanout_author3_{_uid()}'
    shared_owner = f'h_fanout_shared_owner_{_uid()}'
    agent_a = f'a_fanout_a_{_uid()}'
    agent_b = f'a_fanout_b_{_uid()}'

    pg.add(HasnAgents(hasn_id=agent_a, owner_id=shared_owner, star_id=f'{_uid()}#star', display_name='探针分身A'))
    pg.add(HasnAgents(hasn_id=agent_b, owner_id=shared_owner, star_id=f'{_uid()}#star', display_name='探针分身B'))
    pg.add(HasnFollows(follower_hasn_id=agent_a, target_type='human', target_hasn_id=author))
    pg.add(HasnFollows(follower_hasn_id=agent_b, target_type='human', target_hasn_id=author))
    await pg.flush()

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_post(pg, user_id=1, hasn_id=author, content='fanout probe dedup', tags=[])

    owner_hits = [owner for _, owner in calls if owner == shared_owner]
    assert len(owner_hits) == 1, '同主人多分身关注同一作者，只应 bump 一次'


async def test_fanout_skipped_for_pending_review_content(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """待审（pending_review）评论尚未对外可见 → 不触发 fan-out。"""
    commenter = f'h_fanout_commenter_{_uid()}'
    follower_owner = f'h_fanout_follower2_{_uid()}'

    pg.add(HasnFollows(follower_hasn_id=follower_owner, target_type='human', target_hasn_id=commenter))
    await pg.flush()

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_comment(
        pg,
        target_type='post',
        target_id=f'p_nonexistent_{_uid()}',
        hasn_id=commenter,
        content='pending review probe',
        status='pending_review',
    )

    assert calls == [], '待审内容不应触发关注者 fan-out'


async def test_fanout_bumps_on_visible_comment_and_like(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """可见评论 + 点赞：都以动作发起人（评论者/点赞者）为 fan-out 起点通知其关注者。"""
    actor = f'h_fanout_actor_{_uid()}'
    follower_owner = f'h_fanout_follower3_{_uid()}'

    pg.add(HasnFollows(follower_hasn_id=follower_owner, target_type='human', target_hasn_id=actor))
    await pg.flush()

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_comment(
        pg,
        target_type='post',
        target_id=f'p_nonexistent_{_uid()}',
        hasn_id=actor,
        content='visible comment probe',
        status='visible',
    )
    assert (siv.KIND_COMMUNITY, follower_owner) in calls, '可见评论应触发关注者 fan-out'

    calls.clear()
    await community_service.create_like(
        pg,
        user_id=1,
        hasn_id=actor,
        target_type='post',
        target_id=f'p_nonexistent_like_{_uid()}',
    )
    assert (siv.KIND_COMMUNITY, follower_owner) in calls, '点赞应触发关注者 fan-out'


async def test_fanout_bumps_on_publish_article(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """发布文章（非圈子、直接 published）→ 触发关注者 fan-out。"""
    author = f'h_fanout_author4_{_uid()}'
    follower_owner = f'h_fanout_follower4_{_uid()}'

    pg.add(HasnFollows(follower_hasn_id=follower_owner, target_type='human', target_hasn_id=author))
    await pg.flush()

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_article(
        pg, user_id=1, hasn_id=author, title='fanout 探针文章', content='正文', tags=[]
    )

    assert (siv.KIND_COMMUNITY, follower_owner) in calls


async def test_fanout_noop_when_no_followers(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """无关注者 → fan-out 不做任何 bump（不误 bump 作者自己）。"""
    author = f'h_fanout_lonely_{_uid()}'

    calls = _spy_bump_owner(monkeypatch)

    await community_service.create_post(pg, user_id=1, hasn_id=author, content='no followers probe', tags=[])

    assert calls == []
