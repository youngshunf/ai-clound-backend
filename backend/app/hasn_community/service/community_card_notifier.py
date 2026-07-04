"""分身发完帖子/文章后，给主人投一张「可点进详情」的卡片消息。

落点：「主人↔分身」IM 会话（不是社区通知 feed——那由 notification_service.notify_draft_pending 负责）。
出口复用 message_router.route_message（同 hasn.message.send 的卡片路径），from=分身、to=主人。
卡片 schema 对齐 hasn-node `card_messages::build_community_card`，webui CardMessage 渲染、点击
primary_action 的 `hasn://community/{posts|articles}/{id}` 进详情页。

卡片正文做产品化表达：「{分身名}发布了一篇社区{帖子|文章}」+ 状态 + 是否需审核 + 内容预览。

best-effort：投递失败只告警，绝不影响发帖/发文本身（发帖事务已独立提交）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.service import message_router
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# content_type 整数码：5 = 卡片（对齐 mcp/tools/message.py 的 _CT_CARD / ws_node 映射）。
_CT_CARD = 5

_ARTICLE = 'article'

# 状态中文标签（与社区帖子/文章 status 字段取值对齐）。
_STATUS_LABELS = {
    'pending_review': '待主人审核',
    'published': '已发布',
    'draft': '草稿',
    'rejected': '已退回',
}


def _status_label(status: str | None) -> str:
    return _STATUS_LABELS.get(status or '', status or '未知')


async def _resolve_agent_display_name(db: AsyncSession, agent_hasn_id: str) -> str | None:
    """取 Agent 权威对外展示名 `display_name`（卡片作者名一律用它，不用 JWT `agent_name`）。

    JWT 里的 `agent_name` 在不同铸票路径可能是技术标识名/slug（如 `assistant`），不是对外
    展示名（如「全能助理」）——故此处以 `hasn_agents.display_name` 为权威来源。查不到/异常则
    返回 None，调用方回落到入参 author_name（best-effort，绝不因取名失败而不发卡）。
    """
    try:
        value = (
            await db.execute(select(HasnAgents.display_name).where(HasnAgents.hasn_id == agent_hasn_id))
        ).scalar_one_or_none()
        return (value or '').strip() or None
    except Exception as exc:
        log.debug(f'社区卡取 Agent display_name 失败（回落入参名）：{exc!r}')
        return None


def _preview(text: str | None, limit: int) -> str:
    value = ' '.join((text or '').split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + '…'


def build_community_resource_card(
    resource_type: str,
    resource_id: str,
    *,
    author_name: str | None,
    status: str | None,
    preview: str,
    resource_title: str | None = None,
) -> dict[str, Any]:
    """构造社区帖子/文章卡片体（过 validate_card_message_body 自检后返回 dict）。

    标题=「{分身名}发布了一篇社区{帖子|文章}」；fields 携状态+（待审时）审核提示；
    点击 primary_action 进 hasn://community/... 详情。
    """
    is_article = resource_type == _ARTICLE
    plural = 'articles' if is_article else 'posts'
    resource_kind = 'community.article' if is_article else 'community.post'
    id_field = 'article_id' if is_article else 'post_id'
    noun = '文章' if is_article else '帖子'
    uri = f'hasn://community/{plural}/{resource_id}'
    author = author_name or '你的分身'

    headline = f'{author}发布了一篇社区{noun}'
    fields: list[dict[str, str]] = [{'label': '状态', 'value': _status_label(status)}]
    if status == 'pending_review':
        fields.append({'label': '提示', 'value': '需你确认后才会公开发布'})

    body = {
        'schema_version': 'hasn.card/0.1',
        'title': headline,
        'description': preview or None,
        'source': {'kind': 'app', 'id': 'community', 'display_name': '社区', 'verified': True},
        'resource': {
            'type': resource_kind,
            'id': resource_id,
            'app_id': 'community',
            'uri': uri,
            'title': resource_title or headline,
            'summary': preview or None,
            'access': {
                'visibility': 'conversation',
                'readable_by': ['human', 'agent'],
                'required_scopes': ['community.read'],
            },
        },
        'fields': fields,
        'primary_action': {
            'label': f'查看{noun}',
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
    author_name: str | None,
    status: str | None,
    preview: str,
    resource_title: str | None = None,
) -> None:
    """给主人投社区发布知情卡（best-effort）。用独立 db 会话，发帖事务不受影响。"""
    if not agent_hasn_id or not owner_hasn_id or not resource_id:
        return
    try:
        async with async_db_session() as db:
            # 作者名取 Agent 权威 display_name（对外展示名）；查不到才回落入参名（agent.agent_name）。
            resolved_author = await _resolve_agent_display_name(db, agent_hasn_id) or author_name
            card = build_community_resource_card(
                resource_type,
                resource_id,
                author_name=resolved_author,
                status=status,
                preview=preview,
                resource_title=resource_title,
            )
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
    except Exception as exc:
        log.warning(f'社区发布知情卡投递异常（best-effort，不影响发帖）：{exc!r}')


async def notify_owner_post_card(
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    author_name: str | None,
    post_id: str,
    content: str,
    status: str | None,
) -> None:
    """帖子（无标题）：正文预览作为内容描述。"""
    await notify_owner_resource_card(
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        resource_type='post',
        resource_id=post_id,
        author_name=author_name,
        status=status,
        preview=_preview(content, 90),
    )


async def notify_owner_article_card(
    *,
    agent_hasn_id: str,
    owner_hasn_id: str,
    author_name: str | None,
    article_id: str,
    title: str,
    summary: str | None,
    content: str,
    status: str | None,
) -> None:
    """文章：标题做内容描述（缺则回落摘要/正文预览），resource.title 保留真实标题。"""
    preview = (title or '').strip() or _preview(summary, 90) or _preview(content, 90)
    await notify_owner_resource_card(
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        resource_type='article',
        resource_id=article_id,
        author_name=author_name,
        status=status,
        preview=preview,
        resource_title=title or None,
    )
