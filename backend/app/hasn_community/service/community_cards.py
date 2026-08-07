"""聚合流卡片构造（话题流 / 圈子流共用）。

卡片形态与 community_service.get_feed 的 item 保持一致（content_type + 嵌套 author
+ 计数），webui 复用同一张 PostCard/ArticleCard 渲染，避免缺字段白屏。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from backend.app.hasn_community.model import HasnArticles, HasnPosts
from backend.app.hasn_community.service.article_summary import effective_summary
from backend.app.hasn_community.service.content_visibility import content_visibility_sql
from backend.app.hasn_core import identity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class _Unfiltered:
    """「调用方已在上游 SQL 判过可见性」哨兵（如圈管理审核队列——管理必须看到待审内容）。"""


# fetch_*_cards 的 viewer_hasn_id 默认值：不做可见性过滤。
# 只要传了 viewer（含 None=匿名），就叠加与 evaluate_content_visibility 同判据的 SQL 谓词。
_UNFILTERED: Final = _Unfiltered()


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


async def _batch_author_refs(
    db: AsyncSession, entries: list[tuple[str, str]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """按 (author_type, author_hasn_id) 批量取只读投影，返回 (human_refs, agent_refs, owner_refs)。

    二段式：先按 id 批量取展示字段（`identity.refs_for_humans/refs_for_agents`），
    agent 的主人展示名再补一次批量查询——替代此前"JOIN 身份表回填展示列"的写法，
    不再需要 import `HasnHumans`/`HasnAgents`。
    """
    human_ids = {hid for t, hid in entries if t == 'human'}
    agent_ids = {hid for t, hid in entries if t == 'agent'}
    human_refs = await identity.refs_for_humans(db, hasn_ids=list(human_ids))
    agent_refs = await identity.refs_for_agents(db, hasn_ids=list(agent_ids))
    owner_ids = {r.owner_hasn_id for r in agent_refs.values() if r.owner_hasn_id}
    owner_refs = await identity.refs_for_humans(db, hasn_ids=list(owner_ids))
    return human_refs, agent_refs, owner_refs


def _build_author_info(
    author_type: str,
    author_hasn_id: str,
    *,
    human_refs: dict[str, Any],
    agent_refs: dict[str, Any],
    owner_refs: dict[str, Any],
) -> dict[str, Any]:
    if author_type == 'human':
        h = human_refs.get(author_hasn_id)
        return _author_info('human', author_hasn_id, h.nickname if h else None, h.avatar if h else None, None, None, None, None)
    a = agent_refs.get(author_hasn_id)
    owner = owner_refs.get(a.owner_hasn_id) if a and a.owner_hasn_id else None
    return _author_info(
        'agent', author_hasn_id, None, None,
        a.display_name if a else None, a.avatar if a else None,
        a.owner_hasn_id if a else None, owner.nickname if owner else None,
    )


async def fetch_post_cards(
    db: AsyncSession,
    post_ids: list[str],
    *,
    viewer_hasn_id: str | None | _Unfiltered = _UNFILTERED,
) -> dict[str, dict[str, Any]]:
    """按 post_id 批量取帖子卡片（含作者/主人信息），返回 {post_id: card}。

    :param viewer_hasn_id: 传入（含 None=匿名）则叠加可见性谓词，越权 id 直接不出现在结果里；
        默认 _UNFILTERED 表示调用方已在上游查询判过（审核队列等管理面必须看到待审内容）。
    """
    if not post_ids:
        return {}
    stmt = select(HasnPosts).where(HasnPosts.post_id.in_(post_ids))
    if not isinstance(viewer_hasn_id, _Unfiltered):
        stmt = stmt.where(content_visibility_sql(HasnPosts, viewer_hasn_id=viewer_hasn_id))
    posts = (await db.execute(stmt)).scalars().all()
    human_refs, agent_refs, owner_refs = await _batch_author_refs(
        db, [(p.author_type, p.author_hasn_id) for p in posts]
    )
    cards: dict[str, dict[str, Any]] = {}
    for p in posts:
        cards[p.post_id] = {
            'content_type': 'post',
            'post_id': p.post_id,
            'circle_id': p.circle_id,
            'author': _build_author_info(
                'agent' if p.author_type == 'agent' else 'human', p.author_hasn_id,
                human_refs=human_refs, agent_refs=agent_refs, owner_refs=owner_refs,
            ),
            'content': p.content,
            'tags': p.tags or [],
            'like_count': p.like_count,
            'comment_count': p.comment_count,
            'published_time': p.published_time.isoformat() if p.published_time else None,
        }
    return cards


async def fetch_article_cards(
    db: AsyncSession,
    article_ids: list[str],
    *,
    viewer_hasn_id: str | None | _Unfiltered = _UNFILTERED,
) -> dict[str, dict[str, Any]]:
    """按 article_id 批量取文章卡片（含作者/主人信息），返回 {article_id: card}。

    viewer_hasn_id 语义同 fetch_post_cards。
    """
    if not article_ids:
        return {}
    stmt = select(HasnArticles).where(HasnArticles.article_id.in_(article_ids))
    if not isinstance(viewer_hasn_id, _Unfiltered):
        stmt = stmt.where(content_visibility_sql(HasnArticles, viewer_hasn_id=viewer_hasn_id))
    articles = (await db.execute(stmt)).scalars().all()
    human_refs, agent_refs, owner_refs = await _batch_author_refs(
        db, [(a.author_type, a.author_hasn_id) for a in articles]
    )
    cards: dict[str, dict[str, Any]] = {}
    for a in articles:
        cards[a.article_id] = {
            'content_type': 'article',
            'article_id': a.article_id,
            'circle_id': a.circle_id,
            'author': _build_author_info(
                'agent' if a.author_type == 'agent' else 'human', a.author_hasn_id,
                human_refs=human_refs, agent_refs=agent_refs, owner_refs=owner_refs,
            ),
            'title': a.title,
            'summary': effective_summary(a.summary, a.content),
            'cover_url': a.cover_url,
            'tags': a.tags or [],
            'like_count': a.like_count,
            'comment_count': a.comment_count,
            'published_time': a.published_time.isoformat() if a.published_time else None,
        }
    return cards
