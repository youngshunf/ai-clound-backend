"""分身发完帖子/文章后，给主人投一张「可点进详情」的卡片消息。

落点：「主人↔分身」IM 会话（不是社区通知 feed——那由 notification_service.notify_draft_pending 负责）。
出口复用 message_router.route_message（同 hasn.message.send 的卡片路径），from=分身、to=主人。
卡片 schema 对齐 hasn-node `card_messages::build_community_card`，webui CardMessage 渲染、点击
primary_action 的 `hasn://community/{posts|articles}/{id}` 进详情页。

best-effort：投递失败只告警，绝不影响发帖/发文本身（发帖事务已独立提交）。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.service import message_router
from backend.common.log import log
from backend.database.db import async_db_session

# content_type 整数码：5 = 卡片（对齐 mcp/tools/message.py 的 _CT_CARD / ws_node 映射）。
_CT_CARD = 5

_ARTICLE = 'article'


def _preview(text: str | None, limit: int) -> str:
    value = ' '.join((text or '').split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + '…'


def build_community_resource_card(
    resource_type: str,
    resource_id: str,
    *,
    title: str,
    summary: str,
    author_name: str | None = None,
) -> dict[str, Any]:
    """构造社区帖子/文章卡片体（过 validate_card_message_body 自检后返回 dict）。"""
    is_article = resource_type == _ARTICLE
    plural = 'articles' if is_article else 'posts'
    resource_kind = 'community.article' if is_article else 'community.post'
    id_field = 'article_id' if is_article else 'post_id'
    noun = '文章' if is_article else '帖子'
    uri = f'hasn://community/{plural}/{resource_id}'

    fields: list[dict[str, str]] = []
    if author_name:
        fields.append({'label': '作者', 'value': author_name})
    fields.append({'label': '类型', 'value': noun})

    body = {
        'schema_version': 'hasn.card/0.1',
        'title': title,
        'description': summary or None,
        'source': {'kind': 'app', 'id': 'community', 'display_name': '社区', 'verified': True},
        'resource': {
            'type': resource_kind,
            'id': resource_id,
            'app_id': 'community',
            'uri': uri,
            'title': title,
            'summary': summary or None,
            'access': {
                'visibility': 'conversation',
                'readable_by': ['human', 'agent'],
                'required_scopes': ['community.read'],
            },
        },
        'fields': fields,
        'primary_action': {
            'label': f'打开{noun}',
            'action_id': f'open_community_{resource_type}',
            'kind': 'open_uri',
            'uri': uri,
            'event': {
                'event_type': f'{resource_kind}.opened',
                'payload': {id_field: resource_id},
            },
            'style': 'primary',
        },
    }
    return validate_card_message_body(body).model_dump()


async def notify_owner_resource_card(
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    resource_type: str,
    resource_id: str,
    title: str,
    summary: str,
    author_name: str | None = None,
) -> None:
    """给主人投社区发布知情卡（best-effort）。用独立 db 会话，发帖事务不受影响。"""
    if not agent_hasn_id or not owner_hasn_id or not resource_id:
        return
    try:
        card = build_community_resource_card(
            resource_type,
            resource_id,
            title=title,
            summary=summary,
            author_name=author_name,
        )
        async with async_db_session() as db:
            result = await message_router.route_message(
                db=db,
                from_id=agent_hasn_id,
                to_target=owner_hasn_id,
                content=card,
                content_type=_CT_CARD,
                msg_type='message',
                # local_id 幂等：同一资源重复触发不二次落库/投递（resource_id 短，未超 64 上限）。
                local_id=f'community-card-{resource_id}'[:64],
            )
        if result.get('error'):
            log.warning(f'社区发布知情卡投递失败（best-effort）：{result.get("message")}')
    except Exception as exc:  # noqa: BLE001
        log.warning(f'社区发布知情卡投递异常（best-effort，不影响发帖）：{exc!r}')


async def notify_owner_post_card(
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    post_id: str,
    content: str,
) -> None:
    """帖子（无标题）：标题取正文首段预览、摘要取较长预览。"""
    await notify_owner_resource_card(
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        resource_type='post',
        resource_id=post_id,
        title=_preview(content, 30) or '社区帖子',
        summary=_preview(content, 80),
    )


async def notify_owner_article_card(
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    article_id: str,
    title: str,
    summary: str | None,
    content: str,
) -> None:
    """文章：标题用真实标题，摘要优先用 summary、缺省回落正文预览。"""
    await notify_owner_resource_card(
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        resource_type='article',
        resource_id=article_id,
        title=title or '社区文章',
        summary=_preview(summary, 80) or _preview(content, 80),
    )
