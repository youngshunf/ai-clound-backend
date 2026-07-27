"""话题 trending / search 视角化 is_following 回填（service 层，真实 PG，事务回滚隔离）。

修复点：登录浏览者请求 trending/search 时，每条话题应带 is_following，使「刷新后
（跨会话）关注态」正确，而不是仅靠前端会话内乐观更新。匿名请求不带该字段。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.app.hasn_community.service.topic_service import topic_service
from backend.database.db import uuid4_str
from tests.hasn_community.conftest import seed_human


async def _new_topic(db, owner_hasn_id: str, name: str) -> str:
    t = await topic_service.create_topic(
        db, name=name, description=None, cover_url=None, created_by_hasn_id=owner_hasn_id
    )
    return t['topic_id']


@pytest.mark.asyncio
async def test_search_backfills_is_following_for_viewer(db):
    owner = await seed_human(db, nickname='关注者')
    token = 'zzt' + uuid4_str().replace('-', '')[:8]  # 唯一前缀，只命中本用例新建话题
    t1 = await _new_topic(db, owner['hasn_id'], f'{token}甲')
    t2 = await _new_topic(db, owner['hasn_id'], f'{token}乙')
    await topic_service.follow_topic(db, follower_hasn_id=owner['hasn_id'], topic_id=t1, following=True)

    # 带 viewer：已关注 True / 未关注 False
    items = await topic_service.search_topics(db, token, limit=20, viewer_hasn_id=owner['hasn_id'])
    by_id = {i['topic_id']: i for i in items}
    assert by_id[t1]['is_following'] is True
    assert by_id[t2]['is_following'] is False

    # 匿名（无 viewer）：不带 is_following 字段
    anon = await topic_service.search_topics(db, token, limit=20)
    assert anon, '应能搜到刚建的话题'
    assert all('is_following' not in i for i in anon)


@pytest.mark.asyncio
async def test_trending_backfills_is_following_for_viewer(db):
    owner = await seed_human(db, nickname='关注者')
    token = 'zzt' + uuid4_str().replace('-', '')[:8]
    topic_id = await _new_topic(db, owner['hasn_id'], f'{token}热')

    # 灌入近期内容关联，把该话题顶进 trending 前列（窗口内 recent_cnt 高）
    for _ in range(15):
        await db.execute(
            text(
                'INSERT INTO hasn_community.hasn_content_topics '
                '(topic_id, content_type, content_id, owner_hasn_id, '
                "created_time, updated_time) VALUES (:tid, 'post', :cid, :owner, now(), now())"
            ),
            {'tid': topic_id, 'cid': f'p_{uuid4_str()[:12]}', 'owner': owner['hasn_id']},
        )
    await db.flush()
    await topic_service.follow_topic(db, follower_hasn_id=owner['hasn_id'], topic_id=topic_id, following=True)

    # 带 viewer：每条都应回填 is_following(bool)，本话题为 True
    rows = await topic_service.get_trending(db, limit=50, viewer_hasn_id=owner['hasn_id'])
    assert rows, 'trending 应非空'
    assert all(isinstance(r.get('is_following'), bool) for r in rows), '带 viewer 时每条都应有 is_following'
    mine = next((r for r in rows if r['topic_id'] == topic_id), None)
    assert mine is not None, '近期热度高的话题应进入 trending 前 50'
    assert mine['is_following'] is True

    # 匿名（无 viewer）：不带 is_following 字段
    anon = await topic_service.get_trending(db, limit=50)
    assert all('is_following' not in r for r in anon)
