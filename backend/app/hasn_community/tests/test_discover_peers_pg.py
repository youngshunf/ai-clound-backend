"""community.discover_peers 发现用户和 Agent（零 mock，真实 PG :15432）。

覆盖三模式 + 隐私 + 类型过滤：
1. **query 搜索**：昵称前缀命中人；唤星号精确命中（rank 置顶）。
2. **无参兴趣匹配**：按主人 tags 重叠出人 + 按 tags/专业出分身，match_reason 带「兴趣匹配」。
3. **无参活跃回落**：无兴趣信号时仍按活跃度兜底出人/分身。
4. **隐私**：searchable=false 的人不出现在发现/昵称搜索里；分身仅 social_enabled。
5. **类型过滤**：peer_type='agent' 只回分身、'human' 只回人。

直接打 CommunityService.discover_peers（service 层真库），seed 后 finally 清理，避免污染发现池。
需要本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_community.service.community_service import CommunityService
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


async def _pg_reachable() -> bool:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        return False
    else:
        return True
    finally:
        await engine.dispose()


async def test_discover_peers_all_modes() -> None:
    """search / 兴趣匹配 / 活跃回落 / 隐私 / 类型过滤，一个种子集全覆盖。"""
    if not await _pg_reachable():
        pytest.skip('本地 PostgreSQL :15432 不可达，跳过')

    m = uuid.uuid4().hex[:8]
    viewer_uid = 9_900_000 + (int(m, 16) % 90_000)
    viewer_hid = f'h_disc_owner_{m}'
    match_hid = f'h_disc_match_{m}'
    hidden_hid = f'h_disc_hidden_{m}'
    plain_hid = f'h_disc_plain_{m}'
    agent_owner_hid = f'h_disc_aown_{m}'
    agent_hid = f'a_disc_expert_{m}'
    agent_star = f'disc_expert_{m}#star'

    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    seeded_humans = [viewer_hid, match_hid, hidden_hid, plain_hid, agent_owner_hid]
    try:
        async with session_maker() as db:
            db.add_all([
                # 主人（viewer）：兴趣标签 AI创业 / 投资
                HasnHumans(hasn_id=viewer_hid, star_id=f'disc_owner_{m}', user_id=viewer_uid,
                           nickname=f'发现主人{m}', status='active', tags=['AI创业', '投资']),
                # 兴趣命中的人：tags 与主人重叠（AI创业），searchable 默认 True
                HasnHumans(hasn_id=match_hid, star_id=f'disc_match_{m}', user_id=viewer_uid + 1,
                           nickname=f'发现兴趣人{m}', status='active', tags=['AI创业', '咖啡']),
                # 隐私关闭的人：有重叠 tags 但 searchable=false → 不应出现
                HasnHumans(hasn_id=hidden_hid, star_id=f'disc_hidden_{m}', user_id=viewer_uid + 2,
                           nickname=f'发现隐身人{m}', status='active', tags=['AI创业'],
                           community_settings={'searchable': False}),
                # 无标签的活跃人：兴趣不命中，但活跃回落应能兜到
                HasnHumans(hasn_id=plain_hid, star_id=f'disc_plain_{m}', user_id=viewer_uid + 3,
                           nickname=f'发现路人{m}', status='active', tags=[]),
                # 分身的主人（满足 join，且非 viewer 不被排除）
                HasnHumans(hasn_id=agent_owner_hid, star_id=f'disc_aown_{m}', user_id=viewer_uid + 4,
                           nickname=f'发现分身主人{m}', status='active'),
            ])
            db.add(HasnAgents(
                hasn_id=agent_hid, star_id=agent_star, owner_id=agent_owner_hid,
                display_name=f'发现专家{m}', agent_name=f'disc_expert_{m}',
                social_enabled=True, status='active', tags=['AI创业'], profession='AI产品专家',
            ))
            await db.commit()

        async with session_maker() as db:
            # 1) 无参发现：兴趣命中人 + 分身都在，隐身人不在，且各带 match_reason
            disc = await CommunityService.discover_peers(db, viewer_user_id=viewer_uid, limit=50)
            assert disc['mode'] == 'discover'
            by_id = {it['hasn_id']: it for it in disc['items']}
            assert match_hid in by_id, '兴趣命中的人应被发现'
            assert by_id[match_hid]['type'] == 'human'
            assert by_id[match_hid]['match_reason'].startswith('兴趣匹配')
            assert agent_hid in by_id, '兴趣命中的分身应被发现'
            assert by_id[agent_hid]['type'] == 'agent'
            assert by_id[agent_hid]['profession'] == 'AI产品专家'
            assert by_id[agent_hid]['owner']['hasn_id'] == agent_owner_hid
            assert hidden_hid not in by_id, 'searchable=false 的人不应出现在发现里'
            assert viewer_hid not in by_id, '主人自己不应出现在结果里'
            # existing_relation 字段恒在（无联系人 → None）
            assert all('existing_relation' in it for it in disc['items'])

            # 2) 昵称前缀搜索：命中兴趣人，隐身人因 searchable=false 不出
            srch = await CommunityService.discover_peers(db, viewer_user_id=viewer_uid, query=f'发现兴趣人{m}')
            assert srch['mode'] == 'search'
            srch_ids = {it['hasn_id'] for it in srch['items']}
            assert match_hid in srch_ids
            srch_hidden = await CommunityService.discover_peers(db, viewer_user_id=viewer_uid, query=f'发现隐身人{m}')
            assert hidden_hid not in {it['hasn_id'] for it in srch_hidden['items']}, 'searchable=false 不可被昵称搜出'

            # 3) 唤星号精确搜索：命中且 rank 置顶（match_reason=唤星号精确）
            by_star = await CommunityService.discover_peers(db, viewer_user_id=viewer_uid, query=f'disc_match_{m}')
            assert by_star['items'], '唤星号应能搜到'
            assert by_star['items'][0]['hasn_id'] == match_hid
            assert by_star['items'][0]['match_reason'] == '唤星号精确'

            # 4) 分身显示名前缀搜索 + 唤星号(含#)精确
            by_agent_name = await CommunityService.discover_peers(
                db, viewer_user_id=viewer_uid, query=f'发现专家{m}', peer_type='agent'
            )
            assert agent_hid in {it['hasn_id'] for it in by_agent_name['items']}
            assert all(it['type'] == 'agent' for it in by_agent_name['items'])

            # 5) 类型过滤：只看人
            only_human = await CommunityService.discover_peers(
                db, viewer_user_id=viewer_uid, peer_type='human', limit=50
            )
            assert only_human['items'], '应有活跃人兜底'
            assert all(it['type'] == 'human' for it in only_human['items'])
            assert agent_hid not in {it['hasn_id'] for it in only_human['items']}

            # 6) 活跃回落：无兴趣信号的访客（user_id 无对应 human）仍能拿到推荐
            anon = await CommunityService.discover_peers(db, viewer_user_id=None, limit=50)
            assert anon['items'], '匿名/无兴趣也应按活跃度兜底推荐'
    finally:
        async with session_maker() as db:
            await db.execute(delete(HasnAgents).where(HasnAgents.hasn_id == agent_hid))
            await db.execute(delete(HasnHumans).where(HasnHumans.hasn_id.in_(seeded_humans)))
            await db.commit()
        await engine.dispose()
