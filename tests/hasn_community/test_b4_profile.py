"""
B-4 回归：get_profile 真实化（human / agent 主页），doc-13 §2.2。
连真实 PG，事务回滚隔离。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.service.community_service import community_service
from tests.hasn_community.conftest import seed_agent, seed_human, seed_post


@pytest.mark.asyncio
async def test_human_profile_real_fields_and_counts(db):
    user = await seed_human(db, nickname='福仔')
    fan = await seed_human(db, nickname='粉丝')

    await seed_post(db, author_hasn_id=user['hasn_id'], content='帖子一')
    await seed_post(db, author_hasn_id=user['hasn_id'], content='帖子二')

    # 粉丝关注 user
    await community_service.create_follow(
        db, user_id=fan['user_id'], hasn_id=fan['hasn_id'],
        target_type='human', target_hasn_id=user['hasn_id'],
    )

    profile = await community_service.get_profile(
        db, hasn_id=user['hasn_id'], viewer_user_id=fan['user_id']
    )
    assert profile['type'] == 'human'
    assert profile['display_name'] == user['nickname']  # 真实昵称，非写死 'User'
    assert profile['post_count'] == 2
    assert profile['follower_count'] == 1
    assert profile['is_following'] is True
    assert profile['is_self'] is False


@pytest.mark.asyncio
async def test_self_profile_is_self_true(db):
    user = await seed_human(db, nickname='本人')
    profile = await community_service.get_profile(
        db, hasn_id=user['hasn_id'], viewer_user_id=user['user_id']
    )
    assert profile['is_self'] is True
    assert profile['is_following'] is False


@pytest.mark.asyncio
async def test_agent_profile_capabilities_owner_and_called_count(db):
    owner = await seed_human(db, nickname='星主')
    agent = await seed_agent(
        db,
        owner_hasn_id=owner['hasn_id'],
        display_name='星二哥',
        capability_summary_json={'strengths': ['复杂系统设计'], 'skills': ['代码生成']},
        profile_json={
            'community': {
                'boundaries': ['不承接法律咨询'],
                'content_statement': '本 Agent 内容均经主人授权',
            }
        },
    )

    profile = await community_service.get_profile(
        db, hasn_id=agent['hasn_id'], viewer_user_id=owner['user_id']
    )
    assert profile['type'] == 'agent'
    assert profile['display_name'] == '星二哥'  # 真实，非写死
    assert profile['capability_summary'] == {'strengths': ['复杂系统设计'], 'skills': ['代码生成']}
    assert profile['boundaries'] == ['不承接法律咨询']
    assert profile['content_statement'] == '本 Agent 内容均经主人授权'
    # 主人信息条真实
    assert profile['owner'] is not None
    assert profile['owner']['display_name'] == owner['nickname']
    # 无调用审计时为 0（真实统计，不写死 88）
    assert profile['called_count'] == 0


@pytest.mark.asyncio
async def test_profile_not_found_raises(db):
    from backend.common.exception import errors

    with pytest.raises(errors.NotFoundError):
        await community_service.get_profile(db, hasn_id='h_not_exist_xxx', viewer_user_id=None)


@pytest.mark.asyncio
async def test_get_profile_agents_owner_display_name_real(db):
    owner = await seed_human(db, nickname='李俊龙')
    await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='市场分身')

    agents = await community_service.get_profile_agents(
        db, hasn_id=owner['hasn_id'], viewer_user_id=owner['user_id']
    )
    assert len(agents) == 1
    # owner display_name 不再写死为 hasn_id
    assert agents[0]['owner']['display_name'] == owner['nickname']
    assert agents[0]['owner']['hasn_id'] == owner['hasn_id']
