"""社区管理端只读查询服务。

从 `community_service.py` 的 god-class `CommunityService` 拆出的「§管理端（只读审核可见性）」
子域：管理员按全状态分页/详情查看帖子、文章、评论（Admin JWT，仅读）。无跨子域调用、纯独立切片。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.app.hasn_community.model import HasnArticles, HasnComments, HasnPosts
from backend.app.hasn_community.service._community_codec import _present_reference_cards
from backend.app.hasn_community.service.article_summary import effective_summary
from backend.app.hasn_core import identity
from backend.common.exception import errors

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession


async def _batch_display_names(
    db: AsyncSession, entries: list[tuple[str, str]]
) -> dict[str, str | None]:
    """按 (author_type, author_hasn_id) 批量取展示名，返回 {author_hasn_id: display_name}。

    管理端列表只展示名字（不含头像/主人），二段式批量投影足够；替代此前
    "JOIN 身份表回填 human_nickname/agent_display_name 列"的写法。
    """
    human_ids = [hid for t, hid in entries if t == 'human']
    agent_ids = [hid for t, hid in entries if t == 'agent']
    human_refs = await identity.refs_for_humans(db, hasn_ids=human_ids) if human_ids else {}
    agent_refs = await identity.refs_for_agents(db, hasn_ids=agent_ids) if agent_ids else {}
    names: dict[str, str | None] = {}
    for hid, human_ref in human_refs.items():
        names[hid] = human_ref.nickname
    for hid, agent_ref in agent_refs.items():
        names[hid] = agent_ref.display_name
    return names


class CommunityAdminService:
    """社区管理端只读查询（帖/文/评，全状态可见）。"""

    @staticmethod
    async def admin_list_posts(
        db: AsyncSession,
        *,
        status: str | None = None,
        author_hasn_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """管理端列出帖子（全状态，可按 status/author 过滤），用于审核可见性。"""
        stmt = select(HasnPosts)
        if status:
            stmt = stmt.where(HasnPosts.status == status)
        if author_hasn_id:
            stmt = stmt.where(HasnPosts.author_hasn_id == author_hasn_id)
        stmt = stmt.order_by(HasnPosts.created_time.desc()).limit(limit).offset(offset)
        posts = (await db.execute(stmt)).scalars().all()
        names = await _batch_display_names(db, [(p.author_type, p.author_hasn_id) for p in posts])
        items = [
            {
                'post_id': p.post_id,
                'author': {
                    'hasn_id': p.author_hasn_id,
                    'type': p.author_type,
                    'display_name': names.get(p.author_hasn_id) or p.author_hasn_id,
                },
                'owner_hasn_id': p.owner_hasn_id,
                'content': p.content,
                'tags': p.tags or [],
                'visibility': p.visibility,
                'status': p.status,
                'generation_type': p.generation_type,
                'like_count': p.like_count,
                'comment_count': p.comment_count,
                'created_time': p.created_time.isoformat() if p.created_time else None,
                'published_time': p.published_time.isoformat() if p.published_time else None,
            }
            for p in posts
        ]
        return {'items': items, 'limit': limit, 'offset': offset}

    @staticmethod
    async def admin_list_articles(
        db: AsyncSession,
        *,
        status: str | None = None,
        author_hasn_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """管理端列出文章（全状态，可按 status/author 过滤），用于审核可见性。"""
        stmt = select(HasnArticles)
        if status:
            stmt = stmt.where(HasnArticles.status == status)
        if author_hasn_id:
            stmt = stmt.where(HasnArticles.author_hasn_id == author_hasn_id)
        stmt = stmt.order_by(HasnArticles.created_time.desc()).limit(limit).offset(offset)
        articles = (await db.execute(stmt)).scalars().all()
        names = await _batch_display_names(db, [(a.author_type, a.author_hasn_id) for a in articles])
        items = [
            {
                'article_id': a.article_id,
                'title': a.title,
                'summary': a.summary,
                'author': {
                    'hasn_id': a.author_hasn_id,
                    'type': a.author_type,
                    'display_name': names.get(a.author_hasn_id) or a.author_hasn_id,
                },
                'owner_hasn_id': a.owner_hasn_id,
                'visibility': a.visibility,
                'status': a.status,
                'like_count': a.like_count,
                'comment_count': a.comment_count,
                'created_time': a.created_time.isoformat() if a.created_time else None,
                'published_time': a.published_time.isoformat() if a.published_time else None,
            }
            for a in articles
        ]
        return {'items': items, 'limit': limit, 'offset': offset}

    @staticmethod
    async def admin_list_comments(
        db: AsyncSession,
        *,
        status: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """管理端列出评论（全状态，可按 status/target 过滤），用于审核可见性。"""
        stmt = select(HasnComments)
        if status:
            stmt = stmt.where(HasnComments.status == status)
        if target_type:
            stmt = stmt.where(HasnComments.target_type == target_type)
        if target_id:
            stmt = stmt.where(HasnComments.target_id == target_id)
        stmt = stmt.order_by(HasnComments.created_time.desc()).limit(limit).offset(offset)
        comments = (await db.execute(stmt)).scalars().all()
        names = await _batch_display_names(db, [(c.author_type, c.author_hasn_id) for c in comments])
        items = [
            {
                'comment_id': c.comment_id,
                'target_type': c.target_type,
                'target_id': c.target_id,
                'author': {
                    'hasn_id': c.author_hasn_id,
                    'type': c.author_type,
                    'display_name': names.get(c.author_hasn_id) or c.author_hasn_id,
                },
                'owner_hasn_id': c.owner_hasn_id,
                'content': c.content,
                'is_auto_reply': c.is_auto_reply,
                'status': c.status,
                'like_count': c.like_count,
                'created_time': c.created_time.isoformat() if c.created_time else None,
            }
            for c in comments
        ]
        return {'items': items, 'limit': limit, 'offset': offset}

    @staticmethod
    async def admin_get_post(db: AsyncSession, *, post_id: str) -> dict[str, Any]:
        """管理端获取帖子详情（任意状态）。"""
        post = (await db.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))).scalar_one_or_none()
        if not post:
            raise errors.NotFoundError(msg='帖子不存在')
        return {
            'post_id': post.post_id,
            'author': {'hasn_id': post.author_hasn_id, 'type': post.author_type},
            'owner_hasn_id': post.owner_hasn_id,
            'content': post.content,
            'tags': post.tags or [],
            'visibility': post.visibility,
            'comment_policy': post.comment_policy,
            'generation_type': post.generation_type,
            'status': post.status,
            'like_count': post.like_count,
            'comment_count': post.comment_count,
            'collect_count': post.collect_count,
            'created_time': post.created_time.isoformat() if post.created_time else None,
            'published_time': post.published_time.isoformat() if post.published_time else None,
        }

    @staticmethod
    async def admin_get_article(db: AsyncSession, *, article_id: str) -> dict[str, Any]:
        """管理端获取文章详情（任意状态）。"""
        article = (
            await db.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))
        ).scalar_one_or_none()
        if not article:
            raise errors.NotFoundError(msg='文章不存在')
        return {
            'article_id': article.article_id,
            'title': article.title,
            'summary': effective_summary(article.summary, article.content),
            'cover_url': article.cover_url,
            'content': article.content,
            'author': {'hasn_id': article.author_hasn_id, 'type': article.author_type},
            'owner_hasn_id': article.owner_hasn_id,
            'tags': article.tags or [],
            'reference_cards': _present_reference_cards(article.reference_cards, None),
            'visibility': article.visibility,
            'status': article.status,
            'like_count': article.like_count,
            'comment_count': article.comment_count,
            'created_time': article.created_time.isoformat() if article.created_time else None,
            'published_time': article.published_time.isoformat() if article.published_time else None,
        }


community_admin_service = CommunityAdminService()
