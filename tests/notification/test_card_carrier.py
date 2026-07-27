"""卡片消息承载测试（§6.2）——emit 投影成服务号会话里的通知卡片。连真库，回滚隔离。"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.service.notification_service import notification_service
from backend.database.schema_names import SCHEMA_NAMES
from tests.notification.conftest import (
    notification_outbox_result,
    seed_agent,
    seed_human,
)


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
async def test_system_emit_projects_card_into_service_conversation(
    db,
    drain_notification_outbox,
):
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

    # 权威行记录发送命令；业务事务不伪装成消息已投递。
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    command_id = row.delivery.get('card_command_id')
    assert command_id
    assert row.delivery.get('card_delivery_state') == 'pending'
    sv_id = row.delivery.get('service_account')
    assert sv_id and sv_id.startswith('sv_')

    stats = await drain_notification_outbox()
    assert stats.completed == 1

    # 卡片消息落进 服务号 ⇄ 主人 的 service 会话
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 1
    card = cards[0]
    assert await notification_outbox_result(db, command_id) == ('completed', card.id)
    assert card.from_id == sv_id
    assert card.from_type == 5  # service（D8）
    assert card.content['schema_version'] == 'hasn.card/0.1'
    assert card.content['title'] == '系统维护通知'

    conv = (await db.execute(select(HasnConversations).where(HasnConversations.id == card.conversation_id))).scalar_one()
    assert conv.relation_type == 'service'
    assert 'service' in (conv.participant_a_type, conv.participant_b_type)


@pytest.mark.asyncio
async def test_same_source_reuses_one_service_conversation(
    db,
    drain_notification_outbox,
):
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
    stats = await drain_notification_outbox()
    assert stats.completed == 2
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
    assert 'card_command_id' not in (row.delivery or {})
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_owned_agent_source_routes_to_report_conversation(
    db,
    drain_notification_outbox,
):
    """自有 Agent 汇报只进主人主会话，不污染通知中心。"""
    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(
        db,
        owner_hasn_id=owner['hasn_id'],
        display_name='我的分身',
    )
    agent_id = agent['hasn_id']
    command_id = await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'agent', 'id': agent_id, 'display_name': '我的分身'},
        category='agent',  # card_message 默认 True
        type='community_draft_pending',
        title='你的分身 我的分身 有一篇帖子待确认',
        payload={'target': {'type': 'post', 'id': 'd1'}, 'preview': '副业', 'link': '/community?tab=drafts'},
    )
    assert command_id
    notification_count = (
        await db.execute(
            select(HasnNotifications.id).where(
                HasnNotifications.target_id == owner['hasn_id'],
            )
        )
    ).scalars().all()
    assert notification_count == []

    stats = await drain_notification_outbox()
    assert stats.completed == 1

    # 汇报卡 from=agent，schema 合法，source.kind=agent。
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 1
    card = cards[0]
    assert await notification_outbox_result(db, command_id) == ('completed', card.id)
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

    # 消息事务只写唯一集成事件；Sync 投影由 durable consumer 后置完成。
    committed = (
        await db.execute(
            text(
                f'SELECT count(*) FROM {SCHEMA_NAMES.im_event_table("integration_events")} '
                "WHERE aggregate_id = :conversation_id "
                "AND event_type = 'im.message.committed.v1' "
                "AND payload->>'message_id' = :message_id"
            ),
            {
                'conversation_id': str(card.conversation_id),
                'message_id': str(card.id),
            },
        )
    ).scalar()
    assert committed == 1


@pytest.mark.asyncio
async def test_list_service_accounts_for_owner(db):
    """主人名下服务号可列出（供 WebUI 解析服务号会话名称/头像，§4.5）。"""
    from backend.app.notification.service.service_account_service import service_account_service

    owner = await seed_human(db, nickname='主人')
    await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'system', 'id': 'announcement', 'display_name': '唤星官方'},
        category='system',
        type='announcement',
        title='通知',
        payload={'target': {'type': 'sys', 'id': 'm1'}},
    )
    accounts = await service_account_service.list_for_owner(db, owner_id=owner['hasn_id'])
    assert len(accounts) == 1
    assert accounts[0]['sa_hasn_id'].startswith('sv_')
    assert accounts[0]['display_name'] == '唤星官方'
    assert accounts[0]['kind'] == 'system'


@pytest.mark.asyncio
async def test_dnd_suppresses_does_not_block_card(
    db,
    drain_notification_outbox,
):
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
    assert row.delivery.get('card_command_id')
    stats = await drain_notification_outbox()
    assert stats.completed == 1
    assert len(await _card_messages_to(db, owner['hasn_id'])) == 1
