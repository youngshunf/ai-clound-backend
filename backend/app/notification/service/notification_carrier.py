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
from backend.common.exception import errors

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


def build_card_body(
    notif: HasnNotifications,
    *,
    source_kind: str,
    source_id: str,
    source_name: str,
    source_icon: str | None,
    source_verified: bool,
) -> dict[str, Any]:
    """从权威通知行 + 来源信息构造 hasn.card/0.1 卡片体（schema 校验）。

    来源既可能是服务号（app/system/external），也可能是 Agent 本身（agent 源不建服务号，
    卡片落「主人 ⇄ agent」会话）。source_kind 必须是 CardSourceKind 合法值。
    """
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
        # 仅接受 hasn/http/https；相对路径 `/<域>/...` 提升为 canonical `hasn://<域>/...`
        # （首段即资源域，客户端无关；`hasn:/` + `/foo/bar` → `hasn://foo/bar`）。
        uri = link if link.startswith(('hasn:', 'http:', 'https:')) else f'hasn:/{link}'
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
            'kind': source_kind,
            'id': source_id,
            'display_name': source_name or source_id,
            'icon_url': source_icon or None,
            'verified': source_verified,
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


async def _persist_card(
    db: AsyncSession,
    *,
    recipient_id: str,
    from_id: str,
    peer_type: str,
    relation_type: str,
    conversation_type: str,
    card_body: dict[str, Any],
    notif: HasnNotifications,
) -> int:
    """落卡片消息到「from ⇄ 接收方」会话，返回消息 id（绕开 social 权限矩阵：主人自见）。"""
    conv = await message_router.get_or_create_conversation(
        db,
        recipient_id,
        _recipient_entity_type(recipient_id),
        from_id,
        peer_type,
        relation_type=relation_type,
    )
    msg = await message_router.persist_message(
        db,
        conversation_id=str(conv.id),
        from_id=from_id,
        to_id=recipient_id,
        content=card_body,
        content_type=_CONTENT_TYPE_CARD,
        msg_type='notification',
        priority=notif.priority if notif.priority in ('critical', 'high', 'normal', 'low') else 'normal',
        context={'notification_id': notif.id, 'conversation_type': conversation_type},
    )

    # 写 message.received sync_event：让接收方（主人）节点经 sync/pull 镜像这条卡片消息。
    # persist_message 直写（绕开 route_message）不产生同步事件，缺这步则云端落库但 daemon
    # 永不镜像 —— 卡片不会出现在本地消息列表。owner 即接收方（recipient_id 恒为 h_ 主人）。
    from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

    sync_gw = SqlAlchemySyncGateway()
    await sync_gw._append_sync_event(
        db,
        owner_id=recipient_id,
        hasn_id=recipient_id,
        event_type='message.received',
        aggregate_type='message',
        aggregate_id=str(msg.id),
        payload={
            'message_id': str(msg.id),
            'conversation_id': str(conv.id),
            'owner_id': recipient_id,
            'hasn_id': recipient_id,
            'sender_hasn_id': from_id,
            'recipient_hasn_id': recipient_id,
            'direction': 'inbound',
            'content_type': 'application/x.card+json',
            'content_body': card_body,
            'local_id': None,
            'created_at': int(msg.created_time.timestamp()) if msg.created_time else 0,
        },
    )
    return msg.id


async def deliver_card_to_owner(
    db: AsyncSession,
    *,
    recipient_id: str,
    account: HasnServiceAccounts,
    notif: HasnNotifications,
) -> int:
    """把通知卡片投递到「服务号 ⇄ 接收方」的 service 会话，返回消息 id。"""
    card_body = build_card_body(
        notif,
        source_kind=account.kind,
        source_id=account.sa_hasn_id,
        source_name=account.display_name or account.sa_hasn_id,
        source_icon=account.avatar or None,
        source_verified=account.verified,
    )
    return await _persist_card(
        db,
        recipient_id=recipient_id,
        from_id=account.sa_hasn_id,
        peer_type='service',
        relation_type='service',
        conversation_type='service',
        card_body=card_body,
        notif=notif,
    )


async def deliver_agent_card_to_owner(
    db: AsyncSession,
    *,
    recipient_id: str,
    source: dict[str, Any],
    notif: HasnNotifications,
) -> int:
    """Agent 源通知卡片投递到「主人 ⇄ agent」既有 social 会话（§4.5：agent 本身即会话身份）。

    relation_type 用 'social'（与 message_router 对 h_/a_ 收件方的默认一致），确保落进主人
    与该分身既有的会话，而非新建一个服务号会话。
    """
    agent_id = str(source.get('id'))
    card_body = build_card_body(
        notif,
        source_kind='agent',
        source_id=agent_id,
        source_name=source.get('display_name') or agent_id,
        source_icon=source.get('avatar') or None,
        source_verified=False,
    )
    return await _persist_card(
        db,
        recipient_id=recipient_id,
        from_id=agent_id,
        peer_type='agent',
        relation_type='social',
        conversation_type='agent',
        card_body=card_body,
        notif=notif,
    )


async def deliver_card_to_agent(
    db: AsyncSession,
    *,
    agent_id: str,
    account: HasnServiceAccounts,
    notif: HasnNotifications,
) -> int:
    """收件方是 Agent 的通知卡片（§4.5 发 Agent）：经
    `route_message(from=服务号, to=agent, msg_type=notification)` 投递到「服务号 ⇄ Agent」
    direct 会话，返回消息 id。

    与 `deliver_card_to_owner`（直写 `_persist_card` 绕权限矩阵）的本质区别：发给 Agent
    必须走 route_message 才能复用「既有 agent dispatch（投 runtime，让分身去处理这条通知）
    + 既有 owner_copy（message.received sync_event owner_id=Agent 的主人，主人旁观透明）」。
    服务号 → Agent 经 permission_engine 默认 ALLOW（iron_laws 全过：service 源 entity_type
    非 agent、非承诺行为、卡片无敏感字段、relation 非 commerce、未超滑窗限频），无需改权限矩阵。
    relation_type='service' 使会话落「服务号 ⇄ Agent」service 会话（与「服务号 ⇄ 主人」同源
    异会话，对应 D6 一个服务号下多条子会话）。
    """
    card_body = build_card_body(
        notif,
        source_kind=account.kind,
        source_id=account.sa_hasn_id,
        source_name=account.display_name or account.sa_hasn_id,
        source_icon=account.avatar or None,
        source_verified=account.verified,
    )
    result = await message_router.route_message(
        db,
        account.sa_hasn_id,
        agent_id,
        card_body,
        content_type=_CONTENT_TYPE_CARD,
        msg_type='notification',
        priority=notif.priority if notif.priority in ('critical', 'high', 'normal', 'low') else 'normal',
        context={'relation_type': 'service', 'notification_id': notif.id, 'conversation_type': 'service'},
    )
    if result.get('error'):
        raise errors.ServerError(msg=f"通知卡片投递 Agent 失败: {result.get('message')}")
    return int(result['msg_id'])
