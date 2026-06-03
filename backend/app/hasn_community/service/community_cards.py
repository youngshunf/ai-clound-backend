"""聚合流卡片构造（话题流 / 圈子流共用）。

卡片形态与 community_service.get_feed 的 item 保持一致（content_type + 嵌套 author
+ 计数），webui 复用同一张 PostCard/ArticleCard 渲染，避免缺字段白屏。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import aliased

from backend.app.hasn.model import HasnAgents, HasnHumans
from backend.app.hasn_community.model import HasnArticles, HasnPosts
from backend.app.hasn_community.service.article_summary import effective_summary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _author_info(author_type: str, author_hasn_id: str, human_nick, human_avatar, agent_name, agent_avatar, owner_hasn_id, owner_nick) -> dict[str, Any]:
    info: dict[str, Any] = {'hasn_id': author_hasn_id, 'type': author_type}
    if author_type == 'human':
        info['display_name'] = human_nick or author_hasn_id
        info['avatar'] = human_avatar
    else:
        info['display_name'] = agent_name or author_hasn_id
        info['avatar'] = agent_avatar
        if owner_hasn_id:
            info['owner'] = {'hasn_id': owner_hasn_id, 'display_name': owner_nick or owner_hasn_id}
    return info


async def fetch_post_cards(db: AsyncSession, post_ids: list[str]) -> dict[str, dict[str, Any]]:
    """按 post_id 批量取帖子卡片（含作者/主人信息），返回 {post_id: card}。"""
    if not post_ids:
        return {}
    AuthorHuman = aliased(HasnHumans)
    AuthorAgent = aliased(HasnAgents)
    OwnerHuman = aliased(HasnHumans)
    stmt = (
        select(
            HasnPosts,
            AuthorHuman.nickname.label('human_nickname'),
            AuthorHuman.avatar.label('human_avatar'),
            AuthorAgent.display_name.label('agent_display_name'),
            AuthorAgent.avatar.label('agent_avatar'),
            OwnerHuman.hasn_id.label('owner_hasn_id'),
            OwnerHuman.nickname.label('owner_nickname'),
        )
        .outerjoin(AuthorHuman, (HasnPosts.author_type == 'human') & (HasnPosts.author_hasn_id == AuthorHuman.hasn_id))
        .outerjoin(AuthorAgent, (HasnPosts.author_type == 'agent') & (HasnPosts.author_hasn_id == AuthorAgent.hasn_id))
        .outerjoin(OwnerHuman, (HasnPosts.author_type == 'agent') & (AuthorAgent.owner_id == OwnerHuman.hasn_id))
        .where(HasnPosts.post_id.in_(post_ids))
    )
    cards: dict[str, dict[str, Any]] = {}
    for row in (await db.execute(stmt)).all():
        p = row.HasnPosts
        cards[p.post_id] = {
            'content_type': 'post',
            'post_id': p.post_id,
            'circle_id': p.circle_id,
            'author': _author_info('agent' if p.author_type == 'agent' else 'human', p.author_hasn_id,
                                    row.human_nickname, row.human_avatar, row.agent_display_name, row.agent_avatar,
                                    row.owner_hasn_id, row.owner_nickname),
            'content': p.content,
            'tags': p.tags or [],
            'like_count': p.like_count,
            'comment_count': p.comment_count,
            'published_time': p.published_time.isoformat() if p.published_time else None,
        }
    return cards


async def fetch_article_cards(db: AsyncSession, article_ids: list[str]) -> dict[str, dict[str, Any]]:
    """按 article_id 批量取文章卡片（含作者/主人信息），返回 {article_id: card}。"""
    if not article_ids:
        return {}
    AuthorHuman = aliased(HasnHumans)
    AuthorAgent = aliased(HasnAgents)
    OwnerHuman = aliased(HasnHumans)
    stmt = (
        select(
            HasnArticles,
            AuthorHuman.nickname.label('human_nickname'),
            AuthorHuman.avatar.label('human_avatar'),
            AuthorAgent.display_name.label('agent_display_name'),
            AuthorAgent.avatar.label('agent_avatar'),
            OwnerHuman.hasn_id.label('owner_hasn_id'),
            OwnerHuman.nickname.label('owner_nickname'),
        )
        .outerjoin(AuthorHuman, (HasnArticles.author_type == 'human') & (HasnArticles.author_hasn_id == AuthorHuman.hasn_id))
        .outerjoin(AuthorAgent, (HasnArticles.author_type == 'agent') & (HasnArticles.author_hasn_id == AuthorAgent.hasn_id))
        .outerjoin(OwnerHuman, (HasnArticles.author_type == 'agent') & (AuthorAgent.owner_id == OwnerHuman.hasn_id))
        .where(HasnArticles.article_id.in_(article_ids))
    )
    cards: dict[str, dict[str, Any]] = {}
    for row in (await db.execute(stmt)).all():
        a = row.HasnArticles
        cards[a.article_id] = {
            'content_type': 'article',
            'article_id': a.article_id,
            'circle_id': a.circle_id,
            'author': _author_info('agent' if a.author_type == 'agent' else 'human', a.author_hasn_id,
                                    row.human_nickname, row.human_avatar, row.agent_display_name, row.agent_avatar,
                                    row.owner_hasn_id, row.owner_nickname),
            'title': a.title,
            'summary': effective_summary(a.summary, a.content),
            'cover_url': a.cover_url,
            'tags': a.tags or [],
            'like_count': a.like_count,
            'comment_count': a.comment_count,
            'published_time': a.published_time.isoformat() if a.published_time else None,
        }
    return cards
