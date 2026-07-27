"""通知承载：卡片消息投影（§6.2）。

把一条权威通知投影成一条 type=notification, content_type=card(5) 的消息，落进
「服务号 ⇄ 主人」的 service 会话（每来源一个会话，微信服务号效果）。复用 message_router
的 get_or_create_conversation + persist_message 建块（绕开 social 权限矩阵——主人自己的
服务号通知对主人天然可见，符合"通信对主人透明"）。卡片体用既有 hasn.card/0.1 schema 校验。
"""
from __future__ import annotations

import hashlib
import json

from typing import TYPE_CHECKING, Any

from backend.app.hasn_im.adapters.sqlalchemy_producer_outbox import (
    enqueue_send_message,
)
from backend.app.hasn_im.application.errors import ImSendRejected
from backend.app.hasn_im.application.provider import get_im_gateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    EnsureDirectConversationCommand,
    SendMessageCommand,
    ServicePrincipal,
)
from backend.app.hasn_im.ports.im_gateway import ImGateway
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.notification.service.notification_im_outbox import (
    NOTIFICATION_IM_OUTBOX,
)
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

    body: dict[str, Any] = {
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


# CardSourceKind 合法值（与 hasn_card_message.py 对齐）——非此集合的来源无法投影为卡片。
_CARD_SOURCE_KINDS = ('app', 'task', 'agent', 'system', 'user', 'external')


def project_notification_card(notif: HasnNotifications) -> dict[str, Any] | None:
    """把一条权威通知行投影成 hasn.card/0.1 卡片体（doc `通知系统统一设计/01` §3.4）。

    通知面折叠进消息列表后，每条通知直接用 `CardMessage` 渲染——本函数产出的即那份 card
    payload，由 cloud 权威投影（前端零业务拼装、URI 白名单校验、来源图标权威取值都在云端做最稳）。

    - `source` 取权威行 `notif.source`（emit 时已落 kind/id/display_name/avatar，见社区
      `_actor_source` / AI-Native emit）；
    - `primary_action` 经 `build_card_body` 指向**目标资源**深链（`hasn://<域>/{云端id}`），
      **不指向通知自身**（`hasn://notification/{id}` 仅作 resource 体标识，非打开入口）；
    - 任一字段不合法（source 缺失/link scheme 非法）→ 返回 None，前端回退扁平字段渲染
      （韧性：单条坏行不拖垮整列，也不在 GET 读路径抛异常）。

    纯读、无副作用（不建服务号、不写库）——安全用于 `GET /notifications` 序列化。
    """
    src = notif.source or {}
    kind = src.get('kind')
    if kind not in _CARD_SOURCE_KINDS:
        return None
    try:
        return build_card_body(
            notif,
            source_kind=kind,
            source_id=str(src.get('id') or kind),
            source_name=src.get('display_name') or str(src.get('id') or kind),
            source_icon=src.get('avatar') or None,
            source_verified=bool(src.get('verified', kind == 'system')),
        )
    except Exception:
        # 投影失败（畸形来源/链接）不阻断列表——诚实降级为无 card，前端走扁平字段兜底。
        return None


async def _persist_card(
    db: AsyncSession,
    *,
    recipient_id: str,
    from_id: str,
    peer_type: str,
    relation_type: str,
    conversation_type: str,
    card_body: dict[str, Any],
    priority: str,
    notif_id: int | None = None,
    msg_type: str = 'notification',
    gateway: ImGateway | None = None,
) -> str:
    """ensure 会话并在调用方事务登记卡片发送命令，返回 command_id。

    notif_id 为 None 时是「汇报面」卡片（分身→主人主会话，非通知投影，无权威通知行）。

    ensure 允许先于业务事务产生空会话；发送命令与通知/完成状态同事务写入通知生产方自有
    outbox。提交后由统一 relay 以稳定幂等键调用 ImGateway，消除业务提交后的崩溃窗口。
    """
    del peer_type
    if from_id.startswith('a_'):
        actor_kind = ActorKind.AGENT
    elif from_id.startswith('h_'):
        actor_kind = ActorKind.HUMAN
    else:
        actor_kind = ActorKind.SYSTEM_SERVICE
    principal = ServicePrincipal(
        canonical_sender=from_id,
        actor_kind=actor_kind,
    )
    try:
        conversation = await (gateway or get_im_gateway()).ensure_direct_conversation(
            EnsureDirectConversationCommand(
                peer_hasn_id=recipient_id,
                relation_type=relation_type,
            ),
            principal,
        )
    except ImSendRejected as exc:
        raise errors.ServerError(msg=f'通知卡片建立会话失败: {exc}') from exc
    except Exception as exc:
        raise errors.ServerError(msg=f'通知卡片建立会话失败: {exc}') from exc

    if notif_id is not None:
        idempotency_key = (
            f'notification:{notif_id}:{from_id}:{recipient_id}:card'
        )
        causation_id = f'notification:{notif_id}'
    else:
        cause = json.dumps(
            {
                'from_id': from_id,
                'recipient_id': recipient_id,
                'resource': card_body.get('resource'),
                'title': card_body.get('title'),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        digest = hashlib.sha256(cause.encode()).hexdigest()
        idempotency_key = f'notification:report:{digest}'
        causation_id = f'report:{digest}'
    return await enqueue_send_message(
        db,
        table=NOTIFICATION_IM_OUTBOX,
        command=SendMessageCommand(
            conversation_id=conversation.conversation_id,
            content=card_body,
            content_type=_CONTENT_TYPE_CARD,
            idempotency_key=idempotency_key,
            msg_type=msg_type,
            priority=(
                priority
                if priority in ('critical', 'high', 'normal', 'low')
                else 'normal'
            ),
            context={
                'relation_type': relation_type,
                'notification_id': notif_id,
                'conversation_type': conversation_type,
            },
        ),
        principal=principal,
        causation_id=causation_id,
    )


async def deliver_card_to_owner(
    db: AsyncSession,
    *,
    recipient_id: str,
    account: HasnServiceAccounts,
    notif: HasnNotifications,
    gateway: ImGateway | None = None,
) -> str:
    """登记「服务号 ⇄ 接收方」通知卡片命令，返回 command_id。"""
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
        priority=notif.priority,
        notif_id=notif.id,
        gateway=gateway,
    )


async def deliver_agent_card_to_owner(
    db: AsyncSession,
    *,
    recipient_id: str,
    source: dict[str, Any],
    notif: HasnNotifications,
    gateway: ImGateway | None = None,
) -> str:
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
        priority=notif.priority,
        notif_id=notif.id,
        gateway=gateway,
    )


async def deliver_card_to_agent(
    db: AsyncSession,
    *,
    agent_id: str,
    account: HasnServiceAccounts,
    notif: HasnNotifications,
    gateway: ImGateway | None = None,
) -> str:
    """登记「服务号 ⇄ Agent」通知卡片命令，返回 command_id。"""
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
        recipient_id=agent_id,
        from_id=account.sa_hasn_id,
        peer_type='agent',
        relation_type='service',
        conversation_type='service',
        card_body=card_body,
        priority=notif.priority,
        notif_id=notif.id,
        gateway=gateway,
    )


# ==================== 汇报面（分身 → 主人主会话，非通知投影） ====================


def build_report_card_body(
    *,
    source: dict[str, Any],
    title: str,
    body: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """构造「分身汇报卡」hasn.card/0.1 卡片体（doc `01` R2 汇报面）。

    与 `build_card_body`（通知投影卡）的区别：这是分身**主动汇报**给主人的普通会话消息，
    不是通知的承载——所以 `primary_action` 直指**真实资源**（deck/designsystem/task…），
    `resource` 也指真实资源，且**无 dismiss 动作**（普通消息，不是可关闭的通知条）、
    `metadata` 无 notification_id。深链 `{id}` 恒云端权威 id（由 payload.link/deep_link 承载）。
    """
    data = payload or {}
    target = data.get('target') or {}

    fields: list[dict[str, str]] = []
    if data.get('preview'):
        fields.append({'label': '摘要', 'value': str(data['preview'])[:200]})

    # 深链：payload.link 优先，回退 deep_link；相对 `/<域>/...` 提升为 canonical `hasn://<域>/...`
    link = data.get('link') or data.get('deep_link')
    primary_action = None
    resource_uri = None
    if link:
        uri = link if link.startswith(('hasn:', 'http:', 'https:')) else f'hasn:/{link}'
        resource_uri = uri
        primary_action = {
            'label': '查看',
            'action_id': f'open_report_{target.get("kind", "resource")}',
            'kind': 'open_uri',
            'uri': uri,
            'style': 'primary',
        }

    agent_id = str(source.get('id') or '')
    card_body = {
        'schema_version': 'hasn.card/0.1',
        'title': title or '完成',
        'description': body or None,
        'source': {
            'kind': 'agent',
            'id': agent_id,
            'display_name': source.get('display_name') or agent_id,
            'icon_url': source.get('avatar') or None,
            'verified': False,
        },
        # resource 必填（CardMessageBody）——汇报卡指向真实资源本身
        'resource': {
            'type': 'custom',
            'id': str(target.get('id') or agent_id),
            'uri': resource_uri or f'hasn://agent/{agent_id}',
            'title': title or None,
            'metadata': {'report': True, 'target_kind': target.get('kind')},
        },
        'fields': fields,
        'primary_action': primary_action,
        # 汇报卡是普通会话消息，无「知道了/dismiss」——不是可关闭的通知条
        'actions': [],
        'metadata': {'report': True},
    }
    validate_card_message_body(card_body)
    return card_body


async def deliver_report_card_to_owner(
    db: AsyncSession,
    *,
    recipient_id: str,
    source: dict[str, Any],
    title: str,
    body: str | None,
    payload: dict[str, Any],
    priority: str = 'normal',
    gateway: ImGateway | None = None,
) -> str:
    """汇报面：登记分身完成卡发送命令，返回 command_id。

    R2：分身自己发起的操作（完成/汇报）**不落 hasn_notifications 权威行**——它是分身向主人的
    普通会话消息（agent 本身即会话身份），未读自然挂在「与该分身的会话」上。relation_type='social'
    使卡片落进主人与该分身既有的 1:1 主会话（agent_copy），而非新建服务号会话。
    """
    agent_id = str(source.get('id') or '')
    card_body = build_report_card_body(source=source, title=title, body=body, payload=payload)
    return await _persist_card(
        db,
        recipient_id=recipient_id,
        from_id=agent_id,
        peer_type='agent',
        relation_type='social',
        conversation_type='agent',
        card_body=card_body,
        priority=priority,
        notif_id=None,
        gateway=gateway,
    )
