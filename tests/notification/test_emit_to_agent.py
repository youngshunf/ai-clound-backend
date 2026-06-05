"""§4.5 发 Agent（#1130 P2-余 b）：收件方是 Agent 的通知 → 卡片落「服务号 ⇄ Agent」
service 会话，经 route_message 复用「既有 agent dispatch（投 runtime，让分身去处理）+
既有 owner_copy（message.received sync_event owner_id=Agent 的主人，主人旁观透明）」。

连真库（127.0.0.1:15432/huanxing）+ 真 redis（route_message 限频滑窗 + WS 在线表），
事务回滚隔离：conftest `db` fixture 用 `join_transaction_mode='create_savepoint'`，使
route_message 内部 `db.commit()` 仅释放 savepoint，外层 `trans.rollback()` 仍整体回滚。
零 Mock：真 emit → 真 route_message → 真 permission_engine（iron_laws 全过 → 默认 ALLOW，
sv_→a_ 无需改权限矩阵）→ 真 message.received sync_event。

speculative infra 说明：当前无真实「收件方是 Agent」的通知产者，本组以合成
emit(recipient_id=agent) 验证派发链路 + owner_copy 接线正确（同时为 D6 服务号子会话解锁
「分身 ⇄ 服务号」会话）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_sync_events import HasnSyncEvents
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.service.notification_service import notification_service
from tests.notification.conftest import seed_agent, seed_human


async def _agent_cards(db, agent_id: str) -> list[HasnMessages]:
    return list(
        (
            await db.execute(
                select(HasnMessages).where(
                    HasnMessages.to_id == agent_id,
                    HasnMessages.msg_type == 'notification',
                    HasnMessages.content_type == 5,
                )
            )
        ).scalars().all()
    )


@pytest.mark.asyncio
async def test_emit_to_agent_routes_card_into_sv_agent_service_conversation(db):
    """system 源通知发给 Agent（system 默认含 card_message）→ 卡片落「服务号 ⇄ Agent」
    service 会话；message.received sync_event owner_id=主人（owner_copy）/hasn_id=Agent（dispatch）。"""
    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='我的分身')

    nid = await notification_service.emit(
        db,
        recipient_id=agent['hasn_id'],  # 收件方是 Agent（不是主人）
        source={'kind': 'system', 'id': 'sys_alerts', 'display_name': '系统通知'},
        category='system',  # CATEGORY_DEFAULTS[system] 含 card_message
        type='maintenance',
        title='系统维护通知',
        body='今晚 02:00 维护',
        payload={'link': '/settings'},
    )
    assert nid

    # 1) 卡片落「服务号 ⇄ Agent」service 会话，from 是 sv_ 服务号（from_type=5 service，D8）
    cards = await _agent_cards(db, agent['hasn_id'])
    assert len(cards) == 1
    card = cards[0]
    assert card.from_id.startswith('sv_')
    assert card.from_type == 5  # service（D8：不误判成 2=agent）
    assert card.content['schema_version'] == 'hasn.card/0.1'
    assert card.content['source']['display_name'] == '系统通知'

    conv = (
        await db.execute(select(HasnConversations).where(HasnConversations.id == card.conversation_id))
    ).scalar_one()
    assert conv.relation_type == 'service'
    # 服务号参与方落 'service' 类型（D8），Agent 参与方落 'agent'
    part_types = {
        conv.participant_a_id: conv.participant_a_type,
        conv.participant_b_id: conv.participant_b_type,
    }
    assert part_types[card.from_id] == 'service'
    assert part_types[agent['hasn_id']] == 'agent'

    # 2) owner_copy + dispatch：唯一 message.received sync_event
    #    owner_id=主人（主人节点 sync/pull 旁观该 Agent 会话）、hasn_id=Agent（daemon 据此派发 runtime）
    events = list(
        (
            await db.execute(
                select(HasnSyncEvents).where(
                    HasnSyncEvents.aggregate_id == str(card.id),
                    HasnSyncEvents.event_type == 'message.received',
                )
            )
        ).scalars().all()
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.owner_id == owner['hasn_id']  # owner_copy：事件落主人维度
    assert ev.hasn_id == agent['hasn_id']  # dispatch：投 Agent runtime

    # 3) 权威行记账：service_account + card_recipient=agent + 投影回指
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    delivery = row.delivery or {}
    assert delivery.get('card_recipient') == 'agent'
    assert str(delivery.get('service_account', '')).startswith('sv_')
    assert delivery.get('card_message_id') == card.id


@pytest.mark.asyncio
async def test_emit_to_agent_app_source_uses_owner_service_account(db):
    """app 源通知发给 Agent（delivery_hint 开 card_message）→ 服务号属 Agent 的主人；
    「服务号 ⇄ Agent」会话的服务号与「服务号 ⇄ 主人」同源同一个 sv_（D6 子会话基础）。"""
    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='我的分身')

    # 先给主人发一条同源（app/community）通知，建立「服务号 ⇄ 主人」会话与 sv_
    await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'app', 'id': 'community', 'display_name': '社区'},
        category='app',
        type='post_featured',
        title='主人的帖子被加精',
        delivery_hint={'channels': {'card_message': True}},
    )
    owner_cards = list(
        (
            await db.execute(
                select(HasnMessages).where(
                    HasnMessages.to_id == owner['hasn_id'],
                    HasnMessages.content_type == 5,
                )
            )
        ).scalars().all()
    )
    assert len(owner_cards) == 1
    sv_id = owner_cards[0].from_id
    assert sv_id.startswith('sv_')

    # 再给 Agent 发同源通知 → 复用同一个 sv_（per (owner, kind, ref_id) 唯一），但落不同会话
    await notification_service.emit(
        db,
        recipient_id=agent['hasn_id'],
        source={'kind': 'app', 'id': 'community', 'display_name': '社区'},
        category='app',
        type='mentioned',
        title='分身被 @ 了',
        delivery_hint={'channels': {'card_message': True}},
    )
    agent_cards = await _agent_cards(db, agent['hasn_id'])
    assert len(agent_cards) == 1
    assert agent_cards[0].from_id == sv_id  # 同一个服务号身份

    # 「服务号 ⇄ Agent」与「服务号 ⇄ 主人」是两条不同 service 会话（D6：一个服务号下多条子会话）
    assert agent_cards[0].conversation_id != owner_cards[0].conversation_id
