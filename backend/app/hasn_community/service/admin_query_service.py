"""社区管理端只读查询服务。

从 `community_service.py` 的 god-class `CommunityService` 拆出的「§管理端（只读审核可见性）」
子域：管理员按全状态分页/详情查看帖子、文章、评论（Admin JWT，仅读）。无跨子域调用、纯独立切片。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import aliased

from backend.app.hasn_community.model import HasnArticles, HasnComments, HasnPosts
from backend.app.hasn_community.service._community_codec import _present_reference_cards
from backend.app.hasn_community.service.article_summary import effective_summary
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.common.exception import errors

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession


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
        AuthorHuman = aliased(HasnHumans)
        AuthorAgent = aliased(HasnAgents)
        stmt = (
            select(
                HasnPosts,
                AuthorHuman.nickname.label('human_nickname'),
                AuthorAgent.display_name.label('agent_display_name'),
            )
            .outerjoin(AuthorHuman, (HasnPosts.author_type == 'human') & (HasnPosts.author_hasn_id == AuthorHuman.hasn_id))
            .outerjoin(AuthorAgent, (HasnPosts.author_type == 'agent') & (HasnPosts.author_hasn_id == AuthorAgent.hasn_id))
        )
        if status:
            stmt = stmt.where(HasnPosts.status == status)
        if author_hasn_id:
            stmt = stmt.where(HasnPosts.author_hasn_id == author_hasn_id)
        stmt = stmt.order_by(HasnPosts.created_time.desc()).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).all()
        items = [
            {
                'post_id': r.HasnPosts.post_id,
                'author': {
                    'hasn_id': r.HasnPosts.author_hasn_id,
                    'type': r.HasnPosts.author_type,
                    'display_name': (r.human_nickname if r.HasnPosts.author_type == 'human' else r.agent_display_name)
                    or r.HasnPosts.author_hasn_id,
                },
                'owner_hasn_id': r.HasnPosts.owner_hasn_id,
                'content': r.HasnPosts.content,
                'tags': r.HasnPosts.tags or [],
                'visibility': r.HasnPosts.visibility,
                'status': r.HasnPosts.status,
                'generation_type': r.HasnPosts.generation_type,
                'like_count': r.HasnPosts.like_count,
                'comment_count': r.HasnPosts.comment_count,
                'created_time': r.HasnPosts.created_time.isoformat() if r.HasnPosts.created_time else None,
                'published_time': r.HasnPosts.published_time.isoformat() if r.HasnPosts.published_time else None,
            }
            for r in rows
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
        AuthorHuman = aliased(HasnHumans)
        AuthorAgent = aliased(HasnAgents)
        stmt = (
            select(
                HasnArticles,
                AuthorHuman.nickname.label('human_nickname'),
                AuthorAgent.display_name.label('agent_display_name'),
            )
            .outerjoin(AuthorHuman, (HasnArticles.author_type == 'human') & (HasnArticles.author_hasn_id == AuthorHuman.hasn_id))
            .outerjoin(AuthorAgent, (HasnArticles.author_type == 'agent') & (HasnArticles.author_hasn_id == AuthorAgent.hasn_id))
        )
        if status:
            stmt = stmt.where(HasnArticles.status == status)
        if author_hasn_id:
            stmt = stmt.where(HasnArticles.author_hasn_id == author_hasn_id)
        stmt = stmt.order_by(HasnArticles.created_time.desc()).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).all()
        items = [
            {
                'article_id': r.HasnArticles.article_id,
                'title': r.HasnArticles.title,
                'summary': r.HasnArticles.summary,
                'author': {
                    'hasn_id': r.HasnArticles.author_hasn_id,
                    'type': r.HasnArticles.author_type,
                    'display_name': (r.human_nickname if r.HasnArticles.author_type == 'human' else r.agent_display_name)
                    or r.HasnArticles.author_hasn_id,
                },
                'owner_hasn_id': r.HasnArticles.owner_hasn_id,
                'visibility': r.HasnArticles.visibility,
                'status': r.HasnArticles.status,
                'like_count': r.HasnArticles.like_count,
                'comment_count': r.HasnArticles.comment_count,
                'created_time': r.HasnArticles.created_time.isoformat() if r.HasnArticles.created_time else None,
                'published_time': r.HasnArticles.published_time.isoformat() if r.HasnArticles.published_time else None,
            }
            for r in rows
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
        AuthorHuman = aliased(HasnHumans)
        AuthorAgent = aliased(HasnAgents)
        stmt = (
            select(
                HasnComments,
                AuthorHuman.nickname.label('human_nickname'),
                AuthorAgent.display_name.label('agent_display_name'),
            )
            .outerjoin(AuthorHuman, (HasnComments.author_type == 'human') & (HasnComments.author_hasn_id == AuthorHuman.hasn_id))
            .outerjoin(AuthorAgent, (HasnComments.author_type == 'agent') & (HasnComments.author_hasn_id == AuthorAgent.hasn_id))
        )
        if status:
            stmt = stmt.where(HasnComments.status == status)
        if target_type:
            stmt = stmt.where(HasnComments.target_type == target_type)
        if target_id:
            stmt = stmt.where(HasnComments.target_id == target_id)
        stmt = stmt.order_by(HasnComments.created_time.desc()).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).all()
        items = [
            {
                'comment_id': r.HasnComments.comment_id,
                'target_type': r.HasnComments.target_type,
                'target_id': r.HasnComments.target_id,
                'author': {
                    'hasn_id': r.HasnComments.author_hasn_id,
                    'type': r.HasnComments.author_type,
                    'display_name': (r.human_nickname if r.HasnComments.author_type == 'human' else r.agent_display_name)
                    or r.HasnComments.author_hasn_id,
                },
                'owner_hasn_id': r.HasnComments.owner_hasn_id,
                'content': r.HasnComments.content,
                'is_auto_reply': r.HasnComments.is_auto_reply,
                'status': r.HasnComments.status,
                'like_count': r.HasnComments.like_count,
                'created_time': r.HasnComments.created_time.isoformat() if r.HasnComments.created_time else None,
            }
            for r in rows
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
