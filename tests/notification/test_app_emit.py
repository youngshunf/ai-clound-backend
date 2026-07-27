"""AI-Native App 发通知（§7 / P5）测试——manifest 白名单校验 + source.kind=app 卡片承载。

连真库（127.0.0.1:15432/huanxing），事务回滚隔离（零 Mock 零 Fake）。app_emit 依赖
builtin manifest（community/knowledge）的 notifications.emit 声明，经 ensure_builtin_published
在用例事务内发布。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.service.notification_service import notification_service
from backend.app.notification.service.service_account_service import service_account_service
from backend.common.exception import errors
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
async def test_app_emit_projects_card_into_app_service_conversation(
    db,
    drain_notification_outbox,
):
    """community App emit（白名单内 category=app + card）→ 主人收卡片，落 App 服务号会话。"""
    owner = await seed_human(db, nickname='主人')
    nid = await notification_service.app_emit(
        db,
        app_id='community',
        owner_hasn_id=owner['hasn_id'],
        category='app',
        type='post_featured',
        title='你的帖子被加精了',
        body='恭喜！',
        payload={'target': {'type': 'post', 'id': 'p1'}, 'preview': '上热门', 'link': '/community'},
        want_card=True,
    )

    # 权威行：source.kind=app + on_behalf_of=owner + 卡片投影回指
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert row.source['kind'] == 'app'
    assert row.source['id'] == 'community'
    assert row.source['on_behalf_of'] == owner['hasn_id']
    command_id = row.delivery.get('card_command_id')
    assert command_id
    assert row.delivery.get('card_delivery_state') == 'pending'
    sv_id = row.delivery.get('service_account')
    assert sv_id and sv_id.startswith('sv_')

    stats = await drain_notification_outbox()
    assert stats.completed == 1

    # 卡片落进 App 服务号 ⇄ 主人 的 service 会话，source.kind=app，展示名取 manifest
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 1
    card = cards[0]
    assert await notification_outbox_result(db, command_id) == ('completed', card.id)
    assert card.from_id == sv_id
    assert card.content['schema_version'] == 'hasn.card/0.1'
    assert card.content['source']['kind'] == 'app'
    assert card.content['source']['display_name'] == '社区'

    conv = (
        await db.execute(select(HasnConversations).where(HasnConversations.id == card.conversation_id))
    ).scalar_one()
    assert conv.relation_type == 'service'

    # 服务号可被列出（供 WebUI 渲染会话名称/头像）
    accounts = await service_account_service.list_for_owner(db, owner_id=owner['hasn_id'])
    assert any(a['sa_hasn_id'] == sv_id and a['display_name'] == '社区' for a in accounts)


@pytest.mark.asyncio
async def test_app_emit_without_card_only_center(db):
    """want_card=False → 仅落 center 权威行，不产卡片（category=app 默认无 card_message）。"""
    owner = await seed_human(db, nickname='主人')
    nid = await notification_service.app_emit(
        db,
        app_id='community',
        owner_hasn_id=owner['hasn_id'],
        category='app',
        type='quiet_notice',
        title='静默通知',
        want_card=False,
    )
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert row.source['kind'] == 'app'
    assert 'card_command_id' not in (row.delivery or {})
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_app_emit_category_not_whitelisted_forbidden(db):
    """category 不在 App manifest 白名单（community 白名单无 'message'）→ ForbiddenError。"""
    owner = await seed_human(db, nickname='主人')
    with pytest.raises(errors.ForbiddenError):
        await notification_service.app_emit(
            db,
            app_id='community',
            owner_hasn_id=owner['hasn_id'],
            category='message',  # 不在 ['app','commerce','reminder']
            type='x',
            title='越权 category',
        )


@pytest.mark.asyncio
async def test_app_emit_undeclared_app_forbidden(db):
    """App 未声明 notifications.emit（不存在/未发布）→ ForbiddenError，不落任何行。"""
    owner = await seed_human(db, nickname='主人')
    with pytest.raises(errors.ForbiddenError):
        await notification_service.app_emit(
            db,
            app_id='nonexistent_app',
            owner_hasn_id=owner['hasn_id'],
            category='app',
            type='x',
            title='未声明 App',
        )


@pytest.mark.asyncio
async def test_app_emit_real_agent_jwt_endpoint_e2e(
    db,
    drain_notification_outbox,
):
    """真机 E2E（App→主人收卡片）：真 Agent JWT 签发(写 redis)→真 verify(读 redis)→真端点
    handler→主人 DB 收卡片。覆盖凭证路径 + AppEmitRequest schema + handler + service + 服务号
    卡片承载（HTTP 传输/路由层由 test_routes_contract 锁定）。零 Mock：真库 + 真 redis。"""
    from backend.app.notification.api.v1.agent.notification import app_emit as app_emit_endpoint
    from backend.app.notification.schema.notification import AppEmitRequest
    from backend.common.security.agent_jwt import (
        create_agent_access_token,
        revoke_agent_token,
        verify_agent_token,
    )

    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='我的分身')

    issued = await create_agent_access_token(
        agent_hasn_id=agent['hasn_id'],
        agent_name='我的分身',
        owner_hasn_id=owner['hasn_id'],
        owner_user_id=owner['user_id'],
    )
    try:
        # 真凭证校验：解码 + 命中 redis session → AgentTokenPayload（身份恒取自 JWT）
        token_payload = await verify_agent_token(issued.access_token)
        assert token_payload.owner_hasn_id == owner['hasn_id']

        body = AppEmitRequest(
            app_id='community',
            category='app',
            type='post_featured',
            title='你的帖子被加精了',
            body='恭喜！',
            payload={'target': {'type': 'post', 'id': 'p1'}, 'link': '/community'},
            card=True,
        )
        resp = await app_emit_endpoint(db, token_payload, body)
        nid = resp.data['notification_id']

        # 权威行：source.kind=app + on_behalf_of=主人（服务端按 JWT 补全，不读请求体身份）
        row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
        assert row.source['kind'] == 'app'
        assert row.source['on_behalf_of'] == owner['hasn_id']

        stats = await drain_notification_outbox()
        assert stats.completed == 1

        # 主人 DB 收到卡片，落 App 服务号会话
        cards = await _card_messages_to(db, owner['hasn_id'])
        assert len(cards) == 1
        assert cards[0].content['source']['kind'] == 'app'
        assert cards[0].content['title'] == '你的帖子被加精了'
    finally:
        await revoke_agent_token(agent['hasn_id'], issued.session_uuid)


@pytest.mark.asyncio
async def test_app_emit_card_respects_owner_pref_off(db):
    """主人显式关闭 card_message → App 即便 want_card 也不强开（偏好收敛优先于 manifest）。"""
    from tests.notification.conftest import seed_preference

    owner = await seed_human(db, nickname='主人')
    await seed_preference(db, owner_id=owner['hasn_id'], category='app', channels={'card_message': False})
    nid = await notification_service.app_emit(
        db,
        app_id='community',
        owner_hasn_id=owner['hasn_id'],
        category='app',
        type='post_featured',
        title='被加精',
        want_card=True,
    )
    row = (await db.execute(select(HasnNotifications).where(HasnNotifications.id == nid))).scalar_one()
    assert row.delivery['channels']['card_message'] is False
    assert 'card_command_id' not in (row.delivery or {})
    cards = await _card_messages_to(db, owner['hasn_id'])
    assert len(cards) == 0
