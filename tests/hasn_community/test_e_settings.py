"""
E-backend 回归：个人社区设置 + 黑名单 + Agent 广场筛选。
doc-13 §2.3/§3.3/§3.4。连真实 PG，事务回滚隔离。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.app.hasn_im.application.provider import get_transactional_relation_gateway
from tests.hasn_community.conftest import seed_agent, seed_human, seed_post


@pytest.mark.asyncio
async def test_settings_defaults_and_patch(db):
    user = await seed_human(db, nickname='设置用户')

    # 默认值
    s0 = await community_settings_service.get_community_settings(db, hasn_id=user['hasn_id'])
    assert s0['show_profile'] is True
    assert s0['default_comment_policy'] == 'all'
    assert s0['notify']['like'] is True

    # 部分 patch
    s1 = await community_settings_service.update_community_settings(
        db, hasn_id=user['hasn_id'],
        patch={'searchable': False, 'notify': {'like': False}},
    )
    assert s1['searchable'] is False
    assert s1['notify']['like'] is False
    assert s1['notify']['comment'] is True  # 未改的保留默认
    assert s1['show_profile'] is True  # 未改的保留

    # 持久化
    s2 = await community_settings_service.get_community_settings(db, hasn_id=user['hasn_id'])
    assert s2['searchable'] is False
    assert s2['notify']['like'] is False


@pytest.mark.asyncio
async def test_blocks_add_list_remove(db):
    user = await seed_human(db, nickname='本人')
    target = await seed_human(db, nickname='讨厌的人')

    await community_settings_service.add_block(
        db, blocker_hasn_id=user['hasn_id'], blocked_hasn_id=target['hasn_id'], reason='spam'
    )
    blocks = await community_settings_service.list_blocks(db, blocker_hasn_id=user['hasn_id'])
    assert len(blocks['items']) == 1
    assert blocks['items'][0]['blocked_hasn_id'] == target['hasn_id']
    assert blocks['items'][0]['reason'] == 'spam'

    # 幂等
    await community_settings_service.add_block(
        db, blocker_hasn_id=user['hasn_id'], blocked_hasn_id=target['hasn_id']
    )
    blocks2 = await community_settings_service.list_blocks(db, blocker_hasn_id=user['hasn_id'])
    assert len(blocks2['items']) == 1

    await community_settings_service.remove_block(
        db, blocker_hasn_id=user['hasn_id'], blocked_hasn_id=target['hasn_id']
    )
    blocks3 = await community_settings_service.list_blocks(db, blocker_hasn_id=user['hasn_id'])
    assert len(blocks3['items']) == 0


@pytest.mark.asyncio
async def test_cannot_block_self(db):
    user = await seed_human(db, nickname='本人')
    from backend.common.exception import errors

    with pytest.raises(errors.RequestError):
        await community_settings_service.add_block(
            db, blocker_hasn_id=user['hasn_id'], blocked_hasn_id=user['hasn_id']
        )


@pytest.mark.asyncio
async def test_recommended_agents_capability_filter(db):
    owner = await seed_human(db, nickname='星主')
    await seed_agent(
        db, owner_hasn_id=owner['hasn_id'], display_name='代码分身',
        capability_summary_json={'skills': ['代码生成', 'Python']},
    )
    await seed_agent(
        db, owner_hasn_id=owner['hasn_id'], display_name='市场分身',
        capability_summary_json={'skills': ['市场营销']},
    )

    # 按能力过滤
    res = await community_service.get_recommended_agents(
        db,
        viewer_user_id=owner['user_id'],
        capability='代码生成',
        limit=10,
        relation_gateway=get_transactional_relation_gateway(db),
    )
    names = {a['display_name'] for a in res['items']}
    assert '代码分身' in names
    assert '市场分身' not in names
    code_agent = next(a for a in res['items'] if a['display_name'] == '代码分身')
    assert code_agent['capabilities'] == ['代码生成', 'Python']
    assert code_agent['friend_count'] == 0
    assert code_agent['friendship_status'] == 'owned'
    assert code_agent['add_friend_needs_approval'] is True
    assert code_agent['last_heartbeat_at'] is None


@pytest.mark.asyncio
async def test_recommended_agents_sort_and_pagination(db):
    owner = await seed_human(db, nickname='星主')
    for i in range(3):
        await seed_agent(
            db, owner_hasn_id=owner['hasn_id'], display_name=f'分身{i}',
            capability_summary_json={'skills': ['通用']},
        )

    page1 = await community_service.get_recommended_agents(
        db,
        viewer_user_id=owner['user_id'],
        capability='通用',
        sort='relevance',
        limit=2,
        relation_gateway=get_transactional_relation_gateway(db),
    )
    assert len(page1['items']) == 2
    assert page1['next_cursor'] is not None

    page2 = await community_service.get_recommended_agents(
        db, viewer_user_id=owner['user_id'], capability='通用', sort='relevance',
        limit=2, cursor=page1['next_cursor'],
        relation_gateway=get_transactional_relation_gateway(db),
    )
    ids1 = {a['hasn_id'] for a in page1['items']}
    ids2 = {a['hasn_id'] for a in page2['items']}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_recommended_agents_excludes_social_disabled(db):
    owner = await seed_human(db, nickname='星主')
    # 社交开关的权威事实已迁入 IM 域；身份表旧字段不再参与判定。
    hidden = await seed_agent(
        db,
        owner_hasn_id=owner['hasn_id'],
        display_name='隐藏分身',
    )
    hidden_id = hidden['hasn_id']
    relation_gateway = get_transactional_relation_gateway(db)
    await relation_gateway.update_agent_communication_settings(
        owner_hasn_id=owner['hasn_id'],
        agent_hasn_id=hidden_id,
        social_enabled=False,
    )

    res = await community_service.get_recommended_agents(
        db,
        viewer_user_id=owner['user_id'],
        limit=50,
        relation_gateway=relation_gateway,
    )
    ids = {a['hasn_id'] for a in res['items']}
    assert hidden_id not in ids
