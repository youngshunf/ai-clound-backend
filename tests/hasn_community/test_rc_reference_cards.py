"""
RC：引用卡片（reference_cards）写入 + 序列化 access 控制。

覆盖：
- create_article/create_post 写入 reference_cards，URI 由服务端派生（忽略客户端 uri）
- get_article/get_post 序列化：作者得可跳转 action，他人只得静态卡且 uri 不下发
- 非法类型 / 缺必要字段被拒；update_article 整体替换
连真实 PG，事务回滚隔离（零 Mock 零 Fake）。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.service.community_service import community_service
from backend.common.exception import errors
from tests.hasn_community.conftest import seed_human


@pytest.mark.asyncio
async def test_create_article_stores_reference_cards_and_derives_uri(db):
    author = await seed_human(db, nickname='引用作者')

    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='带引用的文章',
        content='正文',
        reference_cards=[
            # 客户端恶意 uri 应被忽略，由服务端按 (type,id) 派生
            {'type': 'task_result', 'id': 'sess_42', 'title': '季度复盘', 'uri': 'javascript:alert(1)'},
        ],
    )
    article_id = created['article_id']

    detail = await community_service.get_article(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], article_id=article_id
    )
    cards = detail['reference_cards']
    assert len(cards) == 1
    assert cards[0]['type'] == 'task_result'
    assert cards[0]['title'] == '季度复盘'
    # 作者视角：有可跳转 action，URI 服务端派生
    assert cards[0]['action'] == {'kind': 'open_uri', 'uri': 'hasn://tasks/sessions/sess_42'}


@pytest.mark.asyncio
async def test_reference_card_action_is_author_only(db):
    author = await seed_human(db, nickname='RC作者')
    other = await seed_human(db, nickname='RC路人')

    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='access 校验',
        content='正文',
        reference_cards=[{'type': 'chat_summary', 'id': 'conv_7', 'title': '聊天摘要'}],
    )
    article_id = created['article_id']

    # 他人视角：卡片可见，但无 action，且不泄露 uri
    detail_other = await community_service.get_article(
        db, user_id=other['user_id'], hasn_id=other['hasn_id'], article_id=article_id
    )
    card = detail_other['reference_cards'][0]
    assert card['title'] == '聊天摘要'
    assert 'action' not in card
    assert 'uri' not in card

    # 作者视角：有 action
    detail_author = await community_service.get_article(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], article_id=article_id
    )
    assert detail_author['reference_cards'][0]['action']['uri'] == 'hasn://messages/c/conv_7'


@pytest.mark.asyncio
async def test_reference_card_agent_skill_requires_agent_hasn_id(db):
    author = await seed_human(db, nickname='技能作者')

    # 缺 metadata.agent_hasn_id → 拒绝
    with pytest.raises(errors.RequestError):
        await community_service.create_article(
            db,
            user_id=author['user_id'],
            hasn_id=author['hasn_id'],
            title='缺字段',
            content='正文',
            reference_cards=[{'type': 'agent_skill', 'id': 'skill_x'}],
        )

    # 带 agent_hasn_id → URI 含 skill 锚点
    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='技能引用',
        content='正文',
        reference_cards=[{'type': 'agent_skill', 'id': 'skill_x', 'metadata': {'agent_hasn_id': 'a_bob'}}],
    )
    detail = await community_service.get_article(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], article_id=created['article_id']
    )
    assert detail['reference_cards'][0]['action']['uri'] == 'hasn://agents/a_bob/skills?skill=skill_x'


@pytest.mark.asyncio
async def test_reference_card_rejects_invalid_type(db):
    author = await seed_human(db, nickname='非法类型作者')
    with pytest.raises(errors.RequestError):
        await community_service.create_article(
            db,
            user_id=author['user_id'],
            hasn_id=author['hasn_id'],
            title='非法',
            content='正文',
            reference_cards=[{'type': 'arbitrary_evil', 'id': 'x'}],
        )


@pytest.mark.asyncio
async def test_post_reference_cards_author_can_jump(db):
    author = await seed_human(db, nickname='帖子引用作者')
    created = await community_service.create_post(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        content='带引用的帖子',
        reference_cards=[{'type': 'task_result', 'id': 'sess_post', 'title': '任务'}],
    )
    detail = await community_service.get_post(
        db, post_id=created['post_id'], user_id=author['user_id']
    )
    assert detail['reference_cards'][0]['action']['uri'] == 'hasn://tasks/sessions/sess_post'


@pytest.mark.asyncio
async def test_create_article_persists_generation_type(db):
    author = await seed_human(db, nickname='生成声明作者')
    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='人机协作文章',
        content='正文',
        generation_type='co_creation',
    )
    detail = await community_service.get_article(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], article_id=created['article_id']
    )
    assert detail['generation_type'] == 'co_creation'


@pytest.mark.asyncio
async def test_create_article_rejects_unknown_generation_type_falls_back_human(db):
    author = await seed_human(db, nickname='非法声明作者')
    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='非法声明',
        content='正文',
        generation_type='evil_value',
    )
    detail = await community_service.get_article(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], article_id=created['article_id']
    )
    assert detail['generation_type'] == 'human'


@pytest.mark.asyncio
async def test_update_article_replaces_reference_cards(db):
    author = await seed_human(db, nickname='更新作者')
    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='待更新',
        content='正文',
        reference_cards=[{'type': 'task_result', 'id': 'old'}],
    )
    article_id = created['article_id']

    await community_service.update_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        article_id=article_id,
        reference_cards=[{'type': 'chat_summary', 'id': 'new'}],
    )
    detail = await community_service.get_article(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], article_id=article_id
    )
    cards = detail['reference_cards']
    assert len(cards) == 1
    assert cards[0]['type'] == 'chat_summary'
    assert cards[0]['action']['uri'] == 'hasn://messages/c/new'
