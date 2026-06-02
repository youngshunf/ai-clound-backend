"""通知承载：卡片消息投影（§6.2）。

把一条权威通知投影成一条 type=notification, content_type=card(5) 的消息，落进
「服务号 ⇄ 主人」的 service 会话（每来源一个会话，微信服务号效果）。复用 message_router
的 get_or_create_conversation + persist_message 建块（绕开 social 权限矩阵——主人自己的
服务号通知对主人天然可见，符合"通信对主人透明"）。卡片体用既有 hasn.card/0.1 schema 校验。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.service import message_router

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.model.hasn_notifications import HasnNotifications
    from backend.app.notification.model.hasn_service_accounts import HasnServiceAccounts

# content_type=5 卡片
_CONTENT_TYPE_CARD = 5

# 通知接收方实体类型（用于会话 participant 类型）
_RECIPIENT_TYPE = {'h_': 'human', 'a_': 'agent'}


def _recipient_entity_type(hasn_id: str) -> str:
    for prefix, kind in _RECIPIENT_TYPE.items():
        if hasn_id.startswith(prefix):
            return kind
    return 'human'


def build_card_body(notif: HasnNotifications, account: HasnServiceAccounts) -> dict[str, Any]:
    """从权威通知行 + 服务号构造 hasn.card/0.1 卡片体（schema 校验）。"""
    data = notif.data or {}
    target = data.get('target') or {}
    fields: list[dict[str, str]] = []
    if data.get('preview'):
        fields.append({'label': '摘要', 'value': str(data['preview'])[:200]})

    actions: list[dict[str, Any]] = [
        {'label': '知道了', 'action_id': f'dismiss_{notif.id}', 'kind': 'dismiss', 'style': 'default'}
    ]
    link = data.get('link')
    primary_action = None
    if link:
        # 仅接受 hasn/http/https；相对路径包装成 hasn://app{link}
        uri = link if link.startswith(('hasn:', 'http:', 'https:')) else f'hasn://app{link}'
        primary_action = {
            'label': '查看',
            'action_id': f'open_{notif.id}',
            'kind': 'open_uri',
            'uri': uri,
            'style': 'primary',
        }

    body = {
        'schema_version': 'hasn.card/0.1',
        'title': notif.title or '新通知',
        'description': notif.body or None,
        'source': {
            'kind': account.kind,
            'id': account.sa_hasn_id,
            'display_name': account.display_name or account.sa_hasn_id,
            'icon_url': account.avatar or None,
            'verified': account.verified,
        },
        'resource': {
            'type': 'custom',
            'id': str(notif.id),
            'uri': f'hasn://notification/{notif.id}',
            'title': notif.title or None,
            'metadata': {'category': notif.category, 'notification_type': notif.type},
        },
        'fields': fields,
        'primary_action': primary_action,
        'actions': actions,
        'metadata': {
            'notification_id': notif.id,
            'category': notif.category,
            'priority': notif.priority,
        },
    }
    if target.get('id'):
        body['resource']['metadata']['target'] = target
    # 经既有 schema 校验，确保落库卡片契约有效（零 Mock）
    validate_card_message_body(body)
    return body


async def deliver_card_to_owner(
    db: AsyncSession,
    *,
    recipient_id: str,
    account: HasnServiceAccounts,
    notif: HasnNotifications,
) -> int:
    """把通知卡片投递到「服务号 ⇄ 接收方」的 service 会话，返回消息 id。"""
    conv = await message_router.get_or_create_conversation(
        db,
        recipient_id,
        _recipient_entity_type(recipient_id),
        account.sa_hasn_id,
        'service',
        relation_type='service',
    )
    card_body = build_card_body(notif, account)
    msg = await message_router.persist_message(
        db,
        conversation_id=str(conv.id),
        from_id=account.sa_hasn_id,
        to_id=recipient_id,
        content=card_body,
        content_type=_CONTENT_TYPE_CARD,
        msg_type='notification',
        priority=notif.priority if notif.priority in ('critical', 'high', 'normal', 'low') else 'normal',
        context={'notification_id': notif.id, 'conversation_type': 'service'},
    )
    return msg.id
