"""卡片消息承载测试（§6.2）——emit 投影成服务号会话里的通知卡片。连真库，回滚隔离。"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.service.notification_service import notification_service
from tests.notification.conftest import seed_human


async def _card_messages_to(db, recipient_id: str) -> list[HasnMessages]:
    return list(
        (
            await db.execute(
                select(HasnMessages).where(
                    HasnMessages.to_id == recipient_id,
                    HasnMessages.msg_type == 'notification',
                    HasnMessages.content_type == 5,
                )
            )
        ).scalars().all()
    )


@pytest.mark.asyncio
async def test_system_emit_projects_card_into_service_conversation(db):
    owner = await seed_human(db, nickname='主人')
    nid = await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'system', 'id': 'announcement', 'display_name': '唤星官方'},
        category='system',
        type='announcement',
        title='系统维护通知',
        body='今晚 22:00 维护',
        payload={'target': {'type': 'sys', 'id': 'm1'}, 'preview': '维护 30 分钟', 'link': '/settings'},
    )

    # 权威行回写了 card_message_id（投影回指 D1）
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert row.delivery.get('card_message_id')
    sv_id = row.delivery.get('service_account')
    assert sv_id and sv_id.startswith('sv_')

    # 卡片消息落进 服务号 ⇄ 主人 的 service 会话
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 1
    card = cards[0]
    assert card.from_id == sv_id
    assert card.from_type == 5  # service（D8）
    assert card.content['schema_version'] == 'hasn.card/0.1'
    assert card.content['title'] == '系统维护通知'

    conv = (await db.execute(select(HasnConversations).where(HasnConversations.id == card.conversation_id))).scalar_one()
    assert conv.relation_type == 'service'
    assert 'service' in (conv.participant_a_type, conv.participant_b_type)


@pytest.mark.asyncio
async def test_same_source_reuses_one_service_conversation(db):
    owner = await seed_human(db, nickname='主人')
    for i in range(2):
        await notification_service.emit(
            db,
            recipient_id=owner['hasn_id'],
            source={'kind': 'app', 'id': 'translator', 'display_name': '翻译星'},
            category='contact',  # contact 默认 card_message=True
            type='app_msg',
            title=f'消息{i}',
            payload={'target': {'type': 'm', 'id': f'm{i}'}},
        )
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 2  # 两条卡片
    assert len({c.conversation_id for c in cards}) == 1  # 同一个服务号会话（微信服务号效果）


@pytest.mark.asyncio
async def test_social_emit_makes_no_card(db):
    """社区社交通知（kind=user，social 默认 card_message=False）不产生卡片承载。"""
    owner = await seed_human(db, nickname='主人')
    nid = await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'user', 'id': 'h_liker', 'display_name': '路人'},
        category='social',
        type='community_like',
        title='路人赞了你的帖子',
        payload={'target': {'type': 'post', 'id': 'p1'}},
    )
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert 'card_message_id' not in (row.delivery or {})
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_agent_source_projects_card_into_owner_agent_conversation(db):
    """agent 源卡片落「主人 ⇄ agent」既有 social 会话（§4.5：agent 本身即会话身份，不建服务号）。"""
    owner = await seed_human(db, nickname='主人')
    agent_id = 'a_mine'
    nid = await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'agent', 'id': agent_id, 'display_name': '我的分身'},
        category='agent',  # card_message 默认 True
        type='community_draft_pending',
        title='你的分身 我的分身 有一篇帖子待确认',
        payload={'target': {'type': 'post', 'id': 'd1'}, 'preview': '副业', 'link': '/community?tab=drafts'},
    )
    # 权威行回写 card_message_id + card_peer（指向 agent，不建 sv_ 服务号）
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert row.delivery.get('card_message_id')
    assert row.delivery.get('card_peer') == agent_id
    assert 'service_account' not in (row.delivery or {})

    # 卡片 from=agent，schema 合法，source.kind=agent
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 1
    card = cards[0]
    assert card.from_id == agent_id
    assert card.content['schema_version'] == 'hasn.card/0.1'
    assert card.content['source']['kind'] == 'agent'

    # 落进「主人 ⇄ agent」的 direct/social 会话（不是 service 会话），不新建重复会话
    conv = (
        await db.execute(select(HasnConversations).where(HasnConversations.id == card.conversation_id))
    ).scalar_one()
    assert conv.type == 'direct'
    assert conv.relation_type == 'social'
    assert {conv.participant_a_id, conv.participant_b_id} == {owner['hasn_id'], agent_id}

    # 写了 message.received sync_event（否则 daemon 永不镜像这条卡片，本地列表看不到）
    received = (
        await db.execute(
            text(
                "SELECT count(*) FROM hasn_sync_events "
                "WHERE owner_id = :o AND event_type = 'message.received' AND aggregate_id = :mid"
            ),
            {'o': owner['hasn_id'], 'mid': str(card.id)},
        )
    ).scalar()
    assert received == 1


@pytest.mark.asyncio
async def test_dnd_suppresses_does_not_block_card(db):
    """免打扰只压 toast/push（吵），不影响 card_message/center —— 卡片仍投递。"""
    owner = await seed_human(db, nickname='主人')
    await notification_service.upsert_preference(
        db, owner_id=owner['hasn_id'], category='*',
        dnd={'enabled': True, 'start': '00:00', 'end': '23:59', 'allow_critical': True},
    )
    nid = await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'system', 'id': 'sec', 'display_name': '安全中心'},
        category='system',
        type='security',
        title='异地登录',
        payload={'target': {'type': 'sec', 'id': 's1'}},
    )
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert row.delivery['dnd_suppressed'] is True
    assert row.delivery['channels']['toast'] is False
    assert row.delivery['channels']['card_message'] is True  # 卡片不被 DND 压
    assert row.delivery.get('card_message_id')
