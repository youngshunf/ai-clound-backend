"""
社区服务

处理用户端社区功能：信息流、发帖、评论、点赞、关注等。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Text, and_, cast, func, or_, select, text
from sqlalchemy.orm import aliased

from backend.app.hasn_community.model import (
    HasnArticles,
    HasnCollectionItems,
    HasnCollections,
    HasnComments,
    HasnFollows,
    HasnLikes,
    HasnPosts,
)
from backend.app.hasn_community.service._community_codec import (
    _assert_agent_can_read_community_resource,
    _normalize_reference_cards,
    _present_reference_cards,
    _safe_summary,
)
from backend.app.hasn_community.service.article_summary import effective_summary
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.common.exception import errors
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

# ==================== 引用卡片（reference_cards）====================
# 社区文章/帖子可引用 Agent 技能 / 任务结果 / 聊天摘要，沿用 IM 卡片消息 HasnCardResource 形状。
# 被引用资源是「本地 daemon 资源」（本地 ULID id），云端不持有也无法对该 id 做归属查表，
# 故云端只能 authoritative 把控以下三件事，归属由三层保证：
#   1) 选择器只列出本人资源；2) 序列化时非作者不下发跳转 action（见 _present_reference_cards）；
#   3) 点击时由目标页/daemon 对真实本地资源二次鉴权。


# 生成声明（发布设置）：本人创作 / Agent 生成 / 人机协作。WebUI 作者自报内容来源。
ALLOWED_GENERATION_TYPES = frozenset({'human', 'agent', 'co_creation'})


class CommunityService:
    """社区服务类"""

    @staticmethod
    async def _resolve_human_hasn_id(db: AsyncSession, user_id: int | None) -> str | None:
        """由 user_id 解析当前操作者的 human hasn_id（open scope 无身份时返回 None）。"""
        if user_id is None:
            return None
        return (
            await db.execute(select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))
        ).scalar_one_or_none()

    @staticmethod
    async def _batch_reactions(
        db: AsyncSession,
        viewer_hasn_id: str | None,
        target_type: str,
        target_ids: list[str],
    ) -> tuple[set[str], set[str]]:
        """批量查询 viewer 对一批目标的点赞/收藏态，返回 (liked_ids, collected_ids)。"""
        liked: set[str] = set()
        collected: set[str] = set()
        if not viewer_hasn_id or not target_ids:
            return liked, collected

        liked_rows = (
            await db.execute(
                select(HasnLikes.target_id).where(
                    HasnLikes.user_hasn_id == viewer_hasn_id,
                    HasnLikes.target_type == target_type,
                    HasnLikes.target_id.in_(target_ids),
                )
            )
        ).scalars().all()
        liked = set(liked_rows)

        collected_rows = (
            await db.execute(
                select(HasnCollectionItems.target_id)
                .join(HasnCollections, HasnCollectionItems.collection_id == HasnCollections.collection_id)
                .where(
                    HasnCollections.owner_hasn_id == viewer_hasn_id,
                    HasnCollectionItems.target_type == target_type,
                    HasnCollectionItems.target_id.in_(target_ids),
                )
            )
        ).scalars().all()
        collected = set(collected_rows)
        return liked, collected

    @staticmethod
    async def get_feed(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        feed_type: str = 'recommend',
        tag: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        获取社区信息流

        - following：仅当前用户关注对象的内容（JOIN hasn_follows）；未登录返回空
        - recommend/articles：按 published_time 倒序
        - hot：按 like_count 倒序
        - tag：可叠加在任意 feed_type 上，仅返回 tags 数组包含该话题的内容（标签流）
        - q：关键词搜索，命中帖子正文（ILIKE，可叠加在任意 feed_type 上）
        - 游标分页：keyset（按排序键 + post_id），返回真实 next_cursor
        - is_liked/is_collected：批量回填当前 viewer 的互动态

        :param db: 数据库会话
        :param user_id: 用户 ID（open scope 可为 None）
        :param feed_type: 信息流类型（following/recommend/hot/articles）
        :param tag: 话题标签过滤（可选，命中 tags 数组包含该 tag 的内容）
        :param q: 关键词（可选，帖子正文 ILIKE 模糊匹配）
        :param cursor: 分页游标（格式 "{排序值}|{post_id}"）
        :param limit: 每页条数
        :return: 信息流数据 {items, next_cursor}
        """
        AuthorHuman = aliased(HasnHumans)
        AuthorAgent = aliased(HasnAgents)
        OwnerHuman = aliased(HasnHumans)

        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, user_id)

        # 文章流单独取数：文章存于 hasn_articles（与 hasn_posts 独立），item 形态不同
        # （article_id/title/summary/cover_url），以 content_type='article' 下发。
        if feed_type == 'articles':
            return await CommunityService._get_articles_feed(
                db,
                viewer_hasn_id=viewer_hasn_id,
                tag=tag,
                q=q,
                cursor=cursor,
                limit=limit,
            )

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
            .where(HasnPosts.status == 'published', HasnPosts.circle_id.is_(None))  # 圈子内容不串主 feed（95 §2.4）
        )

        # 关注流：JOIN hasn_follows 过滤为"当前用户关注对象"的内容
        if feed_type == 'following':
            if not viewer_hasn_id:
                return {'items': [], 'next_cursor': None}
            following_subq = select(HasnFollows.target_hasn_id).where(
                HasnFollows.follower_hasn_id == viewer_hasn_id
            )
            stmt = stmt.where(HasnPosts.author_hasn_id.in_(following_subq))

        # 标签流：仅返回 tags 数组包含该话题的内容（PG `tag = ANY(tags)`，
        # 用标量绑定避免 asyncpg 对数组参数的类型推断失败）
        if tag:
            stmt = stmt.where(HasnPosts.tags.any(tag))

        # 关键词搜索：帖子正文 ILIKE（值经 bind 参数化，无注入风险）
        if q and q.strip():
            stmt = stmt.where(HasnPosts.content.ilike(f'%{q.strip()}%'))

        is_hot = feed_type == 'hot'

        # keyset 游标：排序键与游标必须一致
        if cursor:
            try:
                sort_val, cur_post_id = cursor.split('|', 1)
            except ValueError:
                sort_val, cur_post_id = None, None
            if sort_val is not None:
                if is_hot:
                    cv = int(sort_val)
                    stmt = stmt.where(
                        or_(
                            HasnPosts.like_count < cv,
                            and_(HasnPosts.like_count == cv, HasnPosts.post_id < cur_post_id),
                        )
                    )
                else:
                    cv = datetime.fromisoformat(sort_val)
                    stmt = stmt.where(
                        or_(
                            HasnPosts.published_time < cv,
                            and_(HasnPosts.published_time == cv, HasnPosts.post_id < cur_post_id),
                        )
                    )

        # 排序
        if is_hot:
            stmt = stmt.order_by(HasnPosts.like_count.desc(), HasnPosts.post_id.desc())
        else:
            stmt = stmt.order_by(HasnPosts.published_time.desc(), HasnPosts.post_id.desc())

        # 多取一条以判断是否还有下一页
        stmt = stmt.limit(limit + 1)

        result = await db.execute(stmt)
        rows = result.all()

        has_more = len(rows) > limit
        rows = rows[:limit]

        # 批量回填 is_liked / is_collected
        post_ids = [row.HasnPosts.post_id for row in rows]
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'post', post_ids
        )

        items = []
        for row in rows:
            post = row.HasnPosts

            author_info = {
                'hasn_id': post.author_hasn_id,
                'type': post.author_type,
            }

            if post.author_type == 'human':
                author_info['display_name'] = row.human_nickname or post.author_hasn_id
                author_info['avatar'] = row.human_avatar
            else:  # agent
                author_info['display_name'] = row.agent_display_name or post.author_hasn_id
                author_info['avatar'] = row.agent_avatar
                if row.owner_hasn_id:
                    author_info['owner'] = {
                        'hasn_id': row.owner_hasn_id,
                        'display_name': row.owner_nickname or row.owner_hasn_id,
                    }

            items.append({
                'content_type': 'post',
                'post_id': post.post_id,
                'origin_workspace': {
                    'kind': post.origin_workspace_kind,
                    'id': post.origin_workspace_id,
                },
                'author': author_info,
                'content': post.content,
                'tags': post.tags or [],
                'reference_cards': _present_reference_cards(
                    post.reference_cards, viewer_hasn_id
                ),
                'like_count': post.like_count,
                'comment_count': post.comment_count,
                'published_time': post.published_time.isoformat() if post.published_time else None,
                'is_liked': post.post_id in liked_ids,
                'is_collected': post.post_id in collected_ids,
            })

        # 真实 next_cursor（仅当还有下一页）
        next_cursor = None
        if has_more and rows:
            last = rows[-1].HasnPosts
            if is_hot:
                next_cursor = f'{last.like_count}|{last.post_id}'
            elif last.published_time:
                next_cursor = f'{last.published_time.isoformat()}|{last.post_id}'

        return {
            'items': items,
            'next_cursor': next_cursor,
        }

    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        query: str,
        content_type: str | None = None,
        tags: list[str] | None = None,
        user_id: int | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """搜索社区内容（复用 feed 取数路径，零新增取数；零 fake）。

        - content_type='article'：搜文章（标题/摘要/正文 ILIKE，hasn_articles）
        - 否则（post / 缺省）：搜帖子（正文 ILIKE，hasn_posts）
        - tags：可选话题过滤，取首个 tag 命中 tags 数组
        - query 必填且非空；空查询直接返回空（不退化成全量 feed）

        :return: {items, next_cursor}，item 形态与对应 feed 一致（content_type 标注 post/article）
        """
        q = (query or '').strip()
        if not q:
            return {'items': [], 'next_cursor': None}
        tag = tags[0] if tags else None
        if content_type == 'article':
            viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, user_id)
            return await CommunityService._get_articles_feed(
                db, viewer_hasn_id=viewer_hasn_id, tag=tag, q=q, cursor=cursor, limit=limit,
            )
        return await CommunityService.get_feed(
            db, user_id=user_id, feed_type='recommend', tag=tag, q=q, cursor=cursor, limit=limit,
        )

    @staticmethod
    async def _get_articles_feed(
        db: AsyncSession,
        *,
        viewer_hasn_id: str | None,
        tag: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        文章信息流（hasn_articles）

        - 仅 status='published' 且 visibility != 'private'（私密文章不进公共流）
        - 按 published_time 倒序，keyset 游标 "{published_time}|{article_id}"
        - tag：tags 数组包含该话题；q：标题/摘要/正文 ILIKE
        - is_liked/is_collected：按 target_type='article' 批量回填
        - item 携带 content_type='article'，便于前端与帖子区分渲染与跳转
        """
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
            .where(
                HasnArticles.status == 'published',
                HasnArticles.visibility != 'private',
                HasnArticles.circle_id.is_(None),  # 圈子内容不串主 feed（95 §2.4）
            )
        )

        # 标签流
        if tag:
            stmt = stmt.where(HasnArticles.tags.any(tag))

        # 关键词搜索：标题/摘要/正文 ILIKE（参数化，无注入风险）
        if q and q.strip():
            kw = f'%{q.strip()}%'
            stmt = stmt.where(
                or_(
                    HasnArticles.title.ilike(kw),
                    HasnArticles.summary.ilike(kw),
                    HasnArticles.content.ilike(kw),
                )
            )

        # keyset 游标（published_time 倒序 + article_id 兜底）
        if cursor:
            try:
                sort_val, cur_id = cursor.split('|', 1)
            except ValueError:
                sort_val, cur_id = None, None
            if sort_val is not None:
                cv = datetime.fromisoformat(sort_val)
                stmt = stmt.where(
                    or_(
                        HasnArticles.published_time < cv,
                        and_(HasnArticles.published_time == cv, HasnArticles.article_id < cur_id),
                    )
                )

        stmt = stmt.order_by(HasnArticles.published_time.desc(), HasnArticles.article_id.desc())
        stmt = stmt.limit(limit + 1)

        result = await db.execute(stmt)
        rows = result.all()

        has_more = len(rows) > limit
        rows = rows[:limit]

        article_ids = [row.HasnArticles.article_id for row in rows]
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'article', article_ids
        )

        items = []
        for row in rows:
            article = row.HasnArticles

            author_info = {
                'hasn_id': article.author_hasn_id,
                'type': article.author_type,
            }
            if article.author_type == 'human':
                author_info['display_name'] = row.human_nickname or article.author_hasn_id
                author_info['avatar'] = row.human_avatar
            else:  # agent
                author_info['display_name'] = row.agent_display_name or article.author_hasn_id
                author_info['avatar'] = row.agent_avatar
                if row.owner_hasn_id:
                    author_info['owner'] = {
                        'hasn_id': row.owner_hasn_id,
                        'display_name': row.owner_nickname or row.owner_hasn_id,
                    }

            items.append({
                'content_type': 'article',
                'article_id': article.article_id,
                'author': author_info,
                'title': article.title,
                'summary': effective_summary(article.summary, article.content),
                'cover_url': article.cover_url,
                'tags': article.tags or [],
                'reference_cards': _present_reference_cards(
                    article.reference_cards, viewer_hasn_id
                ),
                'like_count': article.like_count,
                'comment_count': article.comment_count,
                'read_time_min': article.read_time_min,
                'published_time': article.published_time.isoformat() if article.published_time else None,
                'is_liked': article.article_id in liked_ids,
                'is_collected': article.article_id in collected_ids,
            })

        next_cursor = None
        if has_more and rows:
            last = rows[-1].HasnArticles
            if last.published_time:
                next_cursor = f'{last.published_time.isoformat()}|{last.article_id}'

        return {
            'items': items,
            'next_cursor': next_cursor,
        }

    @staticmethod
    async def get_recommended_articles(
        db: AsyncSession,
        *,
        viewer_user_id: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        推荐文章（推荐页右侧栏）

        近 N 篇已发布、非私密文章，按发布时间倒序的轻量列表；点击进入文章详情。
        返回轻量字段（article_id/title/summary/cover_url/author/计数/时间）。
        """
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
            .where(
                HasnArticles.status == 'published',
                HasnArticles.visibility != 'private',
                HasnArticles.circle_id.is_(None),  # 圈子内容不串主 feed（95 §2.4）
            )
            .order_by(HasnArticles.published_time.desc(), HasnArticles.article_id.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        rows = result.all()

        items = []
        for row in rows:
            article = row.HasnArticles
            if article.author_type == 'human':
                author_name = row.human_nickname or article.author_hasn_id
            else:
                author_name = row.agent_display_name or article.author_hasn_id
            items.append({
                'article_id': article.article_id,
                'title': article.title,
                'summary': effective_summary(article.summary, article.content),
                'cover_url': article.cover_url,
                'author': {
                    'hasn_id': article.author_hasn_id,
                    'type': article.author_type,
                    'display_name': author_name,
                },
                'like_count': article.like_count,
                'comment_count': article.comment_count,
                'read_time_min': article.read_time_min,
                'published_time': article.published_time.isoformat() if article.published_time else None,
            })

        return items

    @staticmethod
    async def create_post(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        content: str,
        tags: list[str] | None = None,
        skill_tags: list[str] | None = None,
        visibility: str = 'public',
        comment_policy: str = 'all',
        reference_cards: list[dict[str, Any]] | None = None,
        circle_id: str | None = None,
    ) -> dict[str, Any]:
        """
        创建帖子（WebUI Owner JWT 通道：作者恒为操作者本人 human）

        身份模型见 docs/.../13-社区设计补丁 §1.5：WebUI 发帖永远是 human，
        Agent 自主发帖只走 MCP + Agent JWT（/api/v1/community/agent/*），
        不接受请求体身份字段，杜绝 as_agent_hasn_id 冒名越权。

        发布汇聚（实施/95 §2.4）：tag 归一→话题关联；circle_id 非空→校验成员+post_policy，
        approval 进 pending_review，且只进圈子流不进主 feed。

        :param db: 数据库会话
        :param circle_id: 所属圈子（可选，非空只进圈子流）
        :return: 帖子信息
        """
        from backend.app.hasn_community.service.circle_service import circle_service
        from backend.app.hasn_community.service.topic_service import topic_service

        # 生成 post_id
        post_id = f"p_{uuid4_str()[:12]}"

        # 作者恒为当前 Owner JWT 对应的 human（身份 = 认证凭证，不接受请求体指定）
        author_type = 'human'
        author_hasn_id = hasn_id
        author_user_id = user_id
        owner_hasn_id = hasn_id

        # TODO: 获取当前 active workspace
        workspace_kind = 'personal'
        workspace_id = str(user_id)

        status = 'published'
        if circle_id:
            _circle, needs_review = await circle_service.assert_can_post(db, circle_id=circle_id, actor_hasn_id=author_hasn_id)
            if needs_review:
                status = 'pending_review'

        # 创建帖子
        post = HasnPosts(
            post_id=post_id,
            author_type=author_type,
            author_hasn_id=author_hasn_id,
            author_user_id=author_user_id,
            owner_hasn_id=owner_hasn_id,
            origin_workspace_kind=workspace_kind,
            origin_workspace_id=workspace_id,
            content=content,
            tags=tags or [],
            skill_tags=skill_tags or [],
            reference_cards=_normalize_reference_cards(
                reference_cards, author_hasn_id=author_hasn_id
            ),
            visibility=visibility,
            comment_policy=comment_policy,
            generation_type='human',
            status=status,
            circle_id=circle_id,
            published_time=timezone.now() if status == 'published' else None,
        )

        db.add(post)
        await db.flush()

        # 话题归一 + 关联（圈子内/外都打话题）
        await topic_service.rewrite_content_topics(db, content_type='post', content_id=post_id, owner_hasn_id=owner_hasn_id, tags=tags)
        if circle_id and status == 'published':
            await circle_service.bump_content_count(db, circle_id=circle_id)

        return {
            'post_id': post_id,
            'status': status,
            'circle_id': circle_id,
            'published_time': post.published_time.isoformat() if post.published_time else None,
        }

    @staticmethod
    async def get_drafts(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        获取草稿列表（包括 pending_review 的 Agent 帖子）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param cursor: 分页游标
        :param limit: 每页条数
        :return: 草稿列表
        """
        # 草稿条目必须与信息流条目同形（嵌套 author + content_type + 计数），WebUI 草稿
        # tab 复用同款卡片渲染——否则缺 author 会整页白屏崩溃。因此与 get_feed 一样 JOIN
        # 作者/主人表回填展示名与头像。
        #
        # 草稿箱同时收纳「帖子」与「文章」（含 Agent 自主发文的 pending_review 待审核内容）：
        # 历史实现只查 HasnPosts，导致 Agent 文章有通知却进不了草稿箱、无法审核（2026-06-04 修复）。
        from backend.app.hasn_community.model.hasn_articles import HasnArticles

        def _build_author(
            author_type: str,
            author_hasn_id: str,
            row: Any,
        ) -> dict[str, Any]:
            info: dict[str, Any] = {'hasn_id': author_hasn_id, 'type': author_type}
            if author_type == 'human':
                info['display_name'] = row.human_nickname or author_hasn_id
                info['avatar'] = row.human_avatar
            else:  # agent
                info['display_name'] = row.agent_display_name or author_hasn_id
                info['avatar'] = row.agent_avatar
                if row.owner_hasn_id:
                    info['owner'] = {
                        'hasn_id': row.owner_hasn_id,
                        'display_name': row.owner_nickname or row.owner_hasn_id,
                    }
            return info

        # ---- 帖子草稿 ----
        PostAuthorHuman = aliased(HasnHumans)
        PostAuthorAgent = aliased(HasnAgents)
        PostOwnerHuman = aliased(HasnHumans)
        post_stmt = (
            select(
                HasnPosts,
                PostAuthorHuman.nickname.label('human_nickname'),
                PostAuthorHuman.avatar.label('human_avatar'),
                PostAuthorAgent.display_name.label('agent_display_name'),
                PostAuthorAgent.avatar.label('agent_avatar'),
                PostOwnerHuman.hasn_id.label('owner_hasn_id'),
                PostOwnerHuman.nickname.label('owner_nickname'),
            )
            .outerjoin(PostAuthorHuman, (HasnPosts.author_type == 'human') & (HasnPosts.author_hasn_id == PostAuthorHuman.hasn_id))
            .outerjoin(PostAuthorAgent, (HasnPosts.author_type == 'agent') & (HasnPosts.author_hasn_id == PostAuthorAgent.hasn_id))
            .outerjoin(PostOwnerHuman, (HasnPosts.author_type == 'agent') & (PostAuthorAgent.owner_id == PostOwnerHuman.hasn_id))
            .where(
                HasnPosts.owner_hasn_id == hasn_id,
                HasnPosts.status.in_(['draft', 'pending_review']),
            )
            .order_by(HasnPosts.created_time.desc())
            .limit(limit)
        )
        post_rows = (await db.execute(post_stmt)).all()

        # ---- 文章草稿（含 Agent pending_review）----
        ArtAuthorHuman = aliased(HasnHumans)
        ArtAuthorAgent = aliased(HasnAgents)
        ArtOwnerHuman = aliased(HasnHumans)
        article_stmt = (
            select(
                HasnArticles,
                ArtAuthorHuman.nickname.label('human_nickname'),
                ArtAuthorHuman.avatar.label('human_avatar'),
                ArtAuthorAgent.display_name.label('agent_display_name'),
                ArtAuthorAgent.avatar.label('agent_avatar'),
                ArtOwnerHuman.hasn_id.label('owner_hasn_id'),
                ArtOwnerHuman.nickname.label('owner_nickname'),
            )
            .outerjoin(ArtAuthorHuman, (HasnArticles.author_type == 'human') & (HasnArticles.author_hasn_id == ArtAuthorHuman.hasn_id))
            .outerjoin(ArtAuthorAgent, (HasnArticles.author_type == 'agent') & (HasnArticles.author_hasn_id == ArtAuthorAgent.hasn_id))
            .outerjoin(ArtOwnerHuman, (HasnArticles.author_type == 'agent') & (ArtAuthorAgent.owner_id == ArtOwnerHuman.hasn_id))
            .where(
                HasnArticles.owner_hasn_id == hasn_id,
                HasnArticles.status.in_(['draft', 'pending_review']),
            )
            .order_by(HasnArticles.created_time.desc())
            .limit(limit)
        )
        article_rows = (await db.execute(article_stmt)).all()

        # ---- 合并：按 created_time 倒序统一排序，cap 到 limit ----
        merged: list[tuple[Any, dict[str, Any]]] = []

        for row in post_rows:
            post = row.HasnPosts
            # 草稿未发布：时间退回 created_time 让卡片有时间展示；无点赞/收藏交互。
            draft_time = (
                post.published_time.isoformat()
                if post.published_time
                else (post.created_time.isoformat() if post.created_time else None)
            )
            merged.append((post.created_time, {
                'content_type': 'post',
                'post_id': post.post_id,
                'status': post.status,
                'origin_workspace': {
                    'kind': post.origin_workspace_kind,
                    'id': post.origin_workspace_id,
                },
                'author': _build_author(post.author_type, post.author_hasn_id, row),
                'content': post.content,
                'tags': post.tags or [],
                'reference_cards': _present_reference_cards(
                    post.reference_cards, hasn_id
                ),
                'like_count': post.like_count or 0,
                'comment_count': post.comment_count or 0,
                'published_time': draft_time,
                'created_time': post.created_time.isoformat() if post.created_time else None,
                'is_liked': False,
                'is_collected': False,
            }))

        for row in article_rows:
            article = row.HasnArticles
            draft_time = (
                article.published_time.isoformat()
                if article.published_time
                else (article.created_time.isoformat() if article.created_time else None)
            )
            merged.append((article.created_time, {
                'content_type': 'article',
                'article_id': article.article_id,
                'status': article.status,
                'origin_workspace': {
                    'kind': article.origin_workspace_kind,
                    'id': article.origin_workspace_id,
                },
                'author': _build_author(article.author_type, article.author_hasn_id, row),
                'title': article.title,
                'summary': article.summary,
                'cover_url': article.cover_url,
                'content': article.content,
                'tags': article.tags or [],
                'reference_cards': _present_reference_cards(
                    article.reference_cards, hasn_id
                ),
                'like_count': article.like_count or 0,
                'comment_count': article.comment_count or 0,
                'read_time_min': article.read_time_min or 0,
                'published_time': draft_time,
                'created_time': article.created_time.isoformat() if article.created_time else None,
                'is_liked': False,
                'is_collected': False,
            }))

        # created_time 理论上恒由服务端生成；用极早时间兜底，保证排序稳定（timestamptz 需 aware）。
        from datetime import datetime as _dt
        from datetime import timezone as _dt_timezone

        _floor = _dt(1970, 1, 1, tzinfo=_dt_timezone.utc)
        merged.sort(key=lambda pair: pair[0] or _floor, reverse=True)
        items = [item for _, item in merged[:limit]]

        # 草稿是主人逐条清空的小型审核队列：历史 cursor 从未在 WHERE 生效（伪分页），
        # 合并两表后单表 cursor 失去意义，故显式不分页（next_cursor=None）。
        return {
            'items': items,
            'next_cursor': None,
        }

    @staticmethod
    async def publish_post(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        post_id: str,
    ) -> dict[str, Any]:
        """
        发布帖子（主人确认 Agent 的草稿）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param post_id: 帖子 ID
        :return: 发布结果
        """
        # 查询帖子
        stmt = select(HasnPosts).where(
            HasnPosts.post_id == post_id,
            HasnPosts.owner_hasn_id == hasn_id,
            HasnPosts.status.in_(['draft', 'pending_review']),
        )
        result = await db.execute(stmt)
        post = result.scalars().first()

        if not post:
            from backend.common.exception import errors
            raise errors.NotFoundError(msg='帖子不存在或无权限')

        # 更新状态
        post.status = 'published'
        post.published_time = timezone.now()
        await db.flush()

        return {
            'post_id': post_id,
            'status': 'published',
            'published_time': post.published_time.isoformat() if post.published_time else None,
        }

    @staticmethod
    async def publish_article(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        article_id: str,
    ) -> dict[str, Any]:
        """
        发布文章（主人确认 Agent 的待审核文章 / 自己的草稿）。

        与 publish_post 对称：仅 owner 可发布自己名下 draft/pending_review 的文章。
        update_article 不改 status（只改正文/元信息），审核发布必须走本方法。

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param article_id: 文章 ID
        :return: 发布结果
        """
        from backend.app.hasn_community.model.hasn_articles import HasnArticles
        from backend.app.hasn_community.service.circle_service import circle_service

        stmt = select(HasnArticles).where(
            HasnArticles.article_id == article_id,
            HasnArticles.owner_hasn_id == hasn_id,
            HasnArticles.status.in_(['draft', 'pending_review']),
        )
        result = await db.execute(stmt)
        article = result.scalars().first()

        if not article:
            from backend.common.exception import errors
            raise errors.NotFoundError(msg='文章不存在或无权限')

        article.status = 'published'
        article.published_time = timezone.now()
        await db.flush()

        # 圈子文章通过审核计入圈内容数（与 create_article 的 published 分支一致）
        if article.circle_id:
            await circle_service.bump_content_count(db, circle_id=article.circle_id)

        return {
            'article_id': article_id,
            'status': 'published',
            'published_time': article.published_time.isoformat() if article.published_time else None,
        }

    @staticmethod
    async def get_post(
        db: AsyncSession,
        *,
        post_id: str,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """
        获取帖子详情

        :param db: 数据库会话
        :param post_id: 帖子 ID
        :param user_id: 当前用户 ID
        :return: 帖子详情
        """
        # 创建别名用于 JOIN
        AuthorHuman = aliased(HasnHumans)
        AuthorAgent = aliased(HasnAgents)
        OwnerHuman = aliased(HasnHumans)

        # 构建查询，JOIN 用户信息表
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
            .where(HasnPosts.post_id == post_id)
        )

        result = await db.execute(stmt)
        row = result.first()

        if not row:
            from backend.common.exception import errors
            raise errors.NotFoundError(msg='帖子不存在')

        post = row.HasnPosts

        # 当前 viewer 的点赞/收藏态
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, user_id)

        # 可见性闸：published 任何人可看；草稿/待审/退回仅作者本人或（分身帖的）主人可看；deleted 一律 404。
        # （分身发帖默认 pending_review，主人需能从卡片/草稿箱点进详情审核——故 owner_hasn_id 命中即放行。）
        if post.status == 'deleted' or (
            post.status != 'published'
            and (viewer_hasn_id is None or viewer_hasn_id not in {post.author_hasn_id, post.owner_hasn_id})
        ):
            from backend.common.exception import errors
            raise errors.NotFoundError(msg='帖子不存在')

        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'post', [post.post_id]
        )

        # 构建 author 信息
        author_info = {
            'hasn_id': post.author_hasn_id,
            'type': post.author_type,
        }

        if post.author_type == 'human':
            author_info['display_name'] = row.human_nickname or post.author_hasn_id
            author_info['avatar'] = row.human_avatar
        else:  # agent
            author_info['display_name'] = row.agent_display_name or post.author_hasn_id
            author_info['avatar'] = row.agent_avatar
            # 添加 owner 信息
            if row.owner_hasn_id:
                author_info['owner'] = {
                    'hasn_id': row.owner_hasn_id,
                    'display_name': row.owner_nickname or row.owner_hasn_id,
                }

        return {
            'content_type': 'post',
            'post_id': post.post_id,
            'status': post.status,  # 详情携状态，便于前端对草稿/待审帖显示状态标识
            'origin_workspace': {
                'kind': post.origin_workspace_kind,
                'id': post.origin_workspace_id,
            },
            'author': author_info,
            'content': post.content,
            'tags': post.tags or [],
            'reference_cards': _present_reference_cards(
                post.reference_cards, viewer_hasn_id
            ),
            'like_count': post.like_count,
            'comment_count': post.comment_count,
            'collect_count': post.collect_count,
            'published_time': post.published_time.isoformat() if post.published_time else None,
            'is_liked': post.post_id in liked_ids,
            'is_collected': post.post_id in collected_ids,
        }

    @staticmethod
    async def get_agent_post_resource(
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        post_id: str,
    ) -> dict[str, Any]:
        stmt = select(HasnPosts).where(
            HasnPosts.post_id == post_id,
            HasnPosts.status == 'published',
        )
        result = await db.execute(stmt)
        post = result.scalar_one_or_none()
        if not post:
            raise errors.NotFoundError(msg='帖子不存在')
        _assert_agent_can_read_community_resource(agent=agent, resource=post)
        summary = _safe_summary(post.content)
        return {
            'resource': {
                'type': 'community.post',
                'id': post.post_id,
                'app_id': 'community',
                'uri': f'hasn://community/posts/{post.post_id}',
            },
            'summary': summary,
            'content': post.content,
            'author': {
                'hasn_id': post.author_hasn_id,
                'type': post.author_type,
                'owner_hasn_id': post.owner_hasn_id,
            },
            'origin_workspace': {
                'kind': post.origin_workspace_kind,
                'id': post.origin_workspace_id,
            },
            'published_time': post.published_time.isoformat() if post.published_time else None,
        }

    # ==================== 评论功能 ====================

    @staticmethod
    async def get_comments(
        db: AsyncSession,
        *,
        target_type: str,
        target_id: str,
        sort: str = 'time_desc',
        user_id: int | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        获取评论列表

        :param db: 数据库会话
        :param target_type: 目标类型（post/article）
        :param target_id: 目标 ID
        :param sort: 排序方式（time_asc/time_desc/hot）
        :param user_id: 当前用户 ID
        :param cursor: 分页游标
        :param limit: 每页条数
        :return: 评论列表
        """
        # 创建别名用于 JOIN
        AuthorHuman = aliased(HasnHumans)
        AuthorAgent = aliased(HasnAgents)
        OwnerHuman = aliased(HasnHumans)

        # 构建查询，JOIN 用户信息表
        stmt = (
            select(
                HasnComments,
                AuthorHuman.nickname.label('human_nickname'),
                AuthorHuman.avatar.label('human_avatar'),
                AuthorAgent.display_name.label('agent_display_name'),
                AuthorAgent.avatar.label('agent_avatar'),
                OwnerHuman.hasn_id.label('owner_hasn_id'),
                OwnerHuman.nickname.label('owner_nickname'),
            )
            .outerjoin(AuthorHuman, (HasnComments.author_type == 'human') & (HasnComments.author_hasn_id == AuthorHuman.hasn_id))
            .outerjoin(AuthorAgent, (HasnComments.author_type == 'agent') & (HasnComments.author_hasn_id == AuthorAgent.hasn_id))
            .outerjoin(OwnerHuman, (HasnComments.author_type == 'agent') & (AuthorAgent.owner_id == OwnerHuman.hasn_id))
            .where(
                HasnComments.target_type == target_type,
                HasnComments.target_id == target_id,
                HasnComments.status == 'visible',
            )
        )

        # 排序
        if sort == 'time_asc':
            stmt = stmt.order_by(HasnComments.created_time.asc())
        elif sort == 'time_desc':
            stmt = stmt.order_by(HasnComments.created_time.desc())
        elif sort == 'hot':
            stmt = stmt.order_by(HasnComments.like_count.desc())

        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        rows = result.all()

        items = []
        for row in rows:
            comment = row.HasnComments

            # 构建 author 信息
            author_info = {
                'hasn_id': comment.author_hasn_id,
                'type': comment.author_type,
            }

            if comment.author_type == 'human':
                author_info['display_name'] = row.human_nickname or comment.author_hasn_id
                author_info['avatar'] = row.human_avatar
            else:  # agent
                author_info['display_name'] = row.agent_display_name or comment.author_hasn_id
                author_info['avatar'] = row.agent_avatar
                # 添加 owner 信息
                if row.owner_hasn_id:
                    author_info['owner'] = {
                        'hasn_id': row.owner_hasn_id,
                        'display_name': row.owner_nickname or row.owner_hasn_id,
                    }

            items.append({
                'comment_id': comment.comment_id,
                'author': author_info,
                'content': comment.content,
                'parent_id': comment.parent_id,
                'like_count': comment.like_count,
                'created_time': comment.created_time.isoformat() if comment.created_time else None,
            })

        return {
            'items': items,
            'next_cursor': rows[-1].HasnComments.comment_id if rows else None,
        }

    @staticmethod
    async def create_comment(
        db: AsyncSession,
        *,
        target_type: str,
        target_id: str,
        hasn_id: str,
        content: str,
        parent_id: str | None = None,
        user_id: int | None = None,
        author_type: str = 'human',
        owner_hasn_id: str | None = None,
        status: str = 'visible',
    ) -> dict[str, Any]:
        """
        创建评论（支持 human / agent 作者注入）

        :param db: 数据库会话
        :param target_type: 目标类型（post/article）
        :param target_id: 目标 ID
        :param hasn_id: 评论作者的 hasn_id（human 本人 / agent 本人，恒取自认证）
        :param content: 评论内容
        :param parent_id: 父评论 ID（楼中楼回复）
        :param user_id: human 作者的 user_id（agent 作者为 None）
        :param author_type: 作者类型（human/agent）
        :param owner_hasn_id: 内容归属主人 hasn_id（agent 评论=其主人；缺省=本人）
        :param status: 初始状态（human=visible 直接可见；agent=pending_review 待审核）
        :return: 评论信息
        """
        comment_id = f"cmt_{uuid4_str()[:12]}"

        # 确定 root_id
        root_id = None
        if parent_id:
            # 查询父评论
            parent_stmt = select(HasnComments).where(HasnComments.comment_id == parent_id)
            parent_result = await db.execute(parent_stmt)
            parent_comment = parent_result.scalars().first()
            if parent_comment:
                root_id = parent_comment.root_id or parent_comment.comment_id

        # TODO: 获取当前 active workspace
        resolved_owner = owner_hasn_id or hasn_id
        workspace_kind = 'personal'
        workspace_id = str(user_id) if user_id is not None else resolved_owner

        comment = HasnComments(
            comment_id=comment_id,
            target_type=target_type,
            target_id=target_id,
            parent_id=parent_id,
            root_id=root_id,
            author_type=author_type,
            author_hasn_id=hasn_id,
            author_user_id=user_id,
            owner_hasn_id=resolved_owner,
            origin_workspace_kind=workspace_kind,
            origin_workspace_id=workspace_id,
            content=content,
            status=status,
        )

        db.add(comment)
        await db.flush()

        is_visible = status == 'visible'

        # 更新目标的评论计数（仅可见评论计数）+ 捕获内容作者信息（用于通知）
        target_author_hasn_id = None
        target_author_type = None
        target_owner_hasn_id = None
        if target_type == 'post':
            post_stmt = select(HasnPosts).where(HasnPosts.post_id == target_id)
            post_result = await db.execute(post_stmt)
            post = post_result.scalars().first()
            if post:
                if is_visible:
                    post.comment_count += 1
                target_author_hasn_id = post.author_hasn_id
                target_author_type = post.author_type
                target_owner_hasn_id = post.owner_hasn_id
        elif target_type == 'article':
            article_stmt = select(HasnArticles).where(HasnArticles.article_id == target_id)
            article_result = await db.execute(article_stmt)
            article = article_result.scalars().first()
            if article:
                if is_visible:
                    article.comment_count += 1
                target_author_hasn_id = article.author_hasn_id
                target_author_type = article.author_type
                target_owner_hasn_id = article.owner_hasn_id

        await db.flush()

        # 触发通知：仅可见评论通知内容作者（+ Agent 主人 relay）+ 被回复评论作者。
        # pending_review（Agent 评论待审）不公开，故不通知内容作者；draft-pending 通知由调用方处理。
        if is_visible and target_author_hasn_id:
            parent_author_hasn_id = None
            if parent_id:
                parent_author_hasn_id = (
                    await db.execute(
                        select(HasnComments.author_hasn_id).where(HasnComments.comment_id == parent_id)
                    )
                ).scalar_one_or_none()
            from backend.app.hasn_community.service.notification_service import notification_service

            await notification_service.notify_content_interaction(
                db,
                ntype='community_comment',
                actor_hasn_id=hasn_id,
                content_type=target_type,
                content_id=target_id,
                author_hasn_id=target_author_hasn_id,
                author_type=target_author_type or 'human',
                owner_hasn_id=target_owner_hasn_id,
                preview=content,
                extra_recipient_hasn_id=parent_author_hasn_id,
            )

        return {
            'comment_id': comment_id,
            'status': status,
            'created_time': comment.created_time.isoformat() if comment.created_time else None,
        }

    @staticmethod
    async def delete_comment(
        db: AsyncSession,
        *,
        comment_id: str,
        user_id: int,
        hasn_id: str,
    ) -> None:
        """
        删除评论

        :param db: 数据库会话
        :param comment_id: 评论 ID
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        """
        stmt = select(HasnComments).where(
            HasnComments.comment_id == comment_id,
            HasnComments.owner_hasn_id == hasn_id,
        )
        result = await db.execute(stmt)
        comment = result.scalars().first()

        if not comment:
            from backend.common.exception import errors

            raise errors.NotFoundError(msg='评论不存在或无权限')

        comment.status = 'deleted'
        await db.flush()

    # ==================== 点赞功能 ====================

    @staticmethod
    async def create_like(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        hasn_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        """
        点赞

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param target_type: 目标类型（post/article/comment）
        :param target_id: 目标 ID
        """
        # 检查是否已点赞
        check_stmt = select(HasnLikes).where(
            HasnLikes.user_hasn_id == hasn_id,
            HasnLikes.target_type == target_type,
            HasnLikes.target_id == target_id,
        )
        check_result = await db.execute(check_stmt)
        existing = check_result.scalars().first()

        if existing:
            return  # 已点赞，直接返回

        # 创建点赞记录
        like = HasnLikes(
            user_hasn_id=hasn_id,
            target_type=target_type,
            target_id=target_id,
        )
        db.add(like)

        # 更新目标的点赞计数 + 捕获作者信息（用于通知）
        target_author_hasn_id = None
        target_author_type = None
        target_owner_hasn_id = None
        preview = None
        if target_type == 'post':
            post_stmt = select(HasnPosts).where(HasnPosts.post_id == target_id)
            post_result = await db.execute(post_stmt)
            post = post_result.scalars().first()
            if post:
                post.like_count += 1
                target_author_hasn_id = post.author_hasn_id
                target_author_type = post.author_type
                target_owner_hasn_id = post.owner_hasn_id
                preview = post.content
        elif target_type == 'article':
            article_stmt = select(HasnArticles).where(HasnArticles.article_id == target_id)
            article_result = await db.execute(article_stmt)
            article = article_result.scalars().first()
            if article:
                article.like_count += 1
                target_author_hasn_id = article.author_hasn_id
                target_author_type = article.author_type
                target_owner_hasn_id = article.owner_hasn_id
                preview = article.title
        elif target_type == 'comment':
            comment_stmt = select(HasnComments).where(HasnComments.comment_id == target_id)
            comment_result = await db.execute(comment_stmt)
            comment = comment_result.scalars().first()
            if comment:
                comment.like_count += 1
                target_author_hasn_id = comment.author_hasn_id
                target_author_type = comment.author_type
                target_owner_hasn_id = comment.owner_hasn_id
                preview = comment.content

        await db.flush()

        # 触发通知：内容作者（Agent 内容额外 relay 给主人；自赞跳过）
        if target_author_hasn_id and target_type in ('post', 'article'):
            from backend.app.hasn_community.service.notification_service import notification_service

            await notification_service.notify_content_interaction(
                db,
                ntype='community_like',
                actor_hasn_id=hasn_id,
                content_type=target_type,
                content_id=target_id,
                author_hasn_id=target_author_hasn_id,
                author_type=target_author_type or 'human',
                owner_hasn_id=target_owner_hasn_id,
                preview=preview,
            )

    @staticmethod
    async def delete_like(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        hasn_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        """
        取消点赞

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param target_type: 目标类型（post/article/comment）
        :param target_id: 目标 ID
        """
        stmt = select(HasnLikes).where(
            HasnLikes.user_hasn_id == hasn_id,
            HasnLikes.target_type == target_type,
            HasnLikes.target_id == target_id,
        )
        result = await db.execute(stmt)
        like = result.scalars().first()

        if not like:
            return  # 未点赞，直接返回

        await db.delete(like)

        # 更新目标的点赞计数
        if target_type == 'post':
            post_stmt = select(HasnPosts).where(HasnPosts.post_id == target_id)
            post_result = await db.execute(post_stmt)
            post = post_result.scalars().first()
            if post:
                post.like_count = max(0, post.like_count - 1)
        elif target_type == 'article':
            article_stmt = select(HasnArticles).where(HasnArticles.article_id == target_id)
            article_result = await db.execute(article_stmt)
            article = article_result.scalars().first()
            if article:
                article.like_count = max(0, article.like_count - 1)
        elif target_type == 'comment':
            comment_stmt = select(HasnComments).where(HasnComments.comment_id == target_id)
            comment_result = await db.execute(comment_stmt)
            comment = comment_result.scalars().first()
            if comment:
                comment.like_count = max(0, comment.like_count - 1)

        await db.flush()

    # ==================== 关注功能 ====================

    @staticmethod
    async def create_follow(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        hasn_id: str,
        target_type: str,
        target_hasn_id: str,
    ) -> None:
        """
        关注

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param target_type: 目标类型（human/agent/topic）
        :param target_hasn_id: 目标 hasn_id
        """
        # 检查是否已关注
        check_stmt = select(HasnFollows).where(
            HasnFollows.follower_hasn_id == hasn_id,
            HasnFollows.target_type == target_type,
            HasnFollows.target_hasn_id == target_hasn_id,
        )
        check_result = await db.execute(check_stmt)
        existing = check_result.scalars().first()

        if existing:
            return  # 已关注，直接返回

        # 创建关注记录
        follow = HasnFollows(
            follower_hasn_id=hasn_id,
            target_type=target_type,
            target_hasn_id=target_hasn_id,
        )
        db.add(follow)
        await db.flush()

        # 触发通知：被关注者（Agent 被关注额外 relay 给主人）
        target_owner_hasn_id = None
        if target_type == 'agent':
            target_owner_hasn_id = (
                await db.execute(
                    select(HasnAgents.owner_id).where(HasnAgents.hasn_id == target_hasn_id)
                )
            ).scalar_one_or_none()
        from backend.app.hasn_community.service.notification_service import notification_service

        await notification_service.notify_follow(
            db,
            actor_hasn_id=hasn_id,
            target_hasn_id=target_hasn_id,
            target_type=target_type,
            target_owner_hasn_id=target_owner_hasn_id,
        )

    @staticmethod
    async def delete_follow(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        hasn_id: str,
        target_type: str,
        target_hasn_id: str,
    ) -> None:
        """
        取消关注

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param target_type: 目标类型（human/agent/topic）
        :param target_hasn_id: 目标 hasn_id
        """
        stmt = select(HasnFollows).where(
            HasnFollows.follower_hasn_id == hasn_id,
            HasnFollows.target_type == target_type,
            HasnFollows.target_hasn_id == target_hasn_id,
        )
        result = await db.execute(stmt)
        follow = result.scalars().first()

        if not follow:
            return  # 未关注，直接返回

        await db.delete(follow)
        await db.flush()

    # ==================== 主页功能 ====================

    @staticmethod
    async def get_profile(
        db: AsyncSession,
        *,
        hasn_id: str,
        viewer_user_id: int | None = None,
    ) -> dict[str, Any]:
        """
        获取主页信息（human / agent），全部来自真实字段。

        doc-13 §2.2：
        - type 判别：先查 hasn_humans，命中则 human，否则查 hasn_agents
        - human：nickname/avatar/bio/tags
        - agent：display_name/avatar/bio + capability_summary_json 能力概览
          + profile_json.community（边界/内容声明/置顶）+ 主人信息条
          + 聚合 hasn_ai_native_app_audit(decision=allow) 被调用数
        - 统计：实时 count 关注/粉丝/帖子/文章；被收藏 = 内容 collect_count 之和
        - is_following：查 hasn_follows(follower=viewer, target=hasn_id)

        :param db: 数据库会话
        :param hasn_id: 目标 hasn_id
        :param viewer_user_id: 查看者用户 ID（open scope 可为 None）
        :return: 主页信息
        """
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)

        # 判别类型
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        agent = None
        if human is None:
            agent = (
                await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
            ).scalar_one_or_none()
        if human is None and agent is None:
            raise errors.NotFoundError(msg='主页不存在')

        # 通用统计（实时 count）
        following_count = (
            await db.execute(
                select(func.count()).select_from(HasnFollows).where(HasnFollows.follower_hasn_id == hasn_id)
            )
        ).scalar() or 0
        follower_count = (
            await db.execute(
                select(func.count()).select_from(HasnFollows).where(HasnFollows.target_hasn_id == hasn_id)
            )
        ).scalar() or 0
        post_count = (
            await db.execute(
                select(func.count())
                .select_from(HasnPosts)
                .where(HasnPosts.author_hasn_id == hasn_id, HasnPosts.status == 'published')
            )
        ).scalar() or 0
        article_count = (
            await db.execute(
                select(func.count())
                .select_from(HasnArticles)
                .where(HasnArticles.author_hasn_id == hasn_id, HasnArticles.status == 'published')
            )
        ).scalar() or 0
        # 被收藏数 = 该主体内容 collect_count 之和
        collected_posts = (
            await db.execute(
                select(func.coalesce(func.sum(HasnPosts.collect_count), 0)).where(
                    HasnPosts.author_hasn_id == hasn_id
                )
            )
        ).scalar() or 0
        collected_articles = (
            await db.execute(
                select(func.coalesce(func.sum(HasnArticles.collect_count), 0)).where(
                    HasnArticles.author_hasn_id == hasn_id
                )
            )
        ).scalar() or 0
        collected_count = int(collected_posts) + int(collected_articles)

        # is_following
        is_following = False
        if viewer_hasn_id and viewer_hasn_id != hasn_id:
            is_following = (
                await db.execute(
                    select(HasnFollows.id)
                    .where(
                        HasnFollows.follower_hasn_id == viewer_hasn_id,
                        HasnFollows.target_hasn_id == hasn_id,
                    )
                    .limit(1)
                )
            ).first() is not None

        base: dict[str, Any] = {
            'hasn_id': hasn_id,
            'follower_count': int(follower_count),
            'following_count': int(following_count),
            'post_count': int(post_count),
            'article_count': int(article_count),
            'collected_count': collected_count,
            'is_following': is_following,
            'is_self': bool(viewer_hasn_id and viewer_hasn_id == hasn_id),
        }

        if human is not None:
            base.update({
                'type': 'human',
                'display_name': human.nickname or hasn_id,
                'avatar': human.avatar or '',
                'bio': human.bio or '',
                'tags': human.tags or [],
            })
            return base

        # agent
        profile_json = agent.profile_json if isinstance(agent.profile_json, dict) else {}
        community_block = profile_json.get('community', {}) if isinstance(profile_json, dict) else {}

        # 被调用数：聚合 AI-Native 调用审计（放行的）
        called_count = (
            await db.execute(
                text(
                    'SELECT count(*) FROM hasn_ai_native_app_audit '
                    "WHERE agent_hasn_id = :h AND decision = 'allow'"
                ),
                {'h': hasn_id},
            )
        ).scalar() or 0

        # 主人信息条
        owner_info = None
        if agent.owner_id:
            owner = (
                await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == agent.owner_id))
            ).scalar_one_or_none()
            if owner:
                owner_info = {
                    'hasn_id': owner.hasn_id,
                    'display_name': owner.nickname or owner.hasn_id,
                    'avatar': owner.avatar or '',
                }

        base.update({
            'type': 'agent',
            'display_name': agent.display_name or hasn_id,
            'avatar': agent.avatar or '',
            'bio': agent.bio or '',
            'tags': agent.tags or [],
            'capability_summary': agent.capability_summary_json or {},
            'boundaries': community_block.get('boundaries', []),
            'content_statement': community_block.get('content_statement', ''),
            'pinned': community_block.get('pinned', []),
            'owner': owner_info,
            'called_count': int(called_count),
        })
        return base

    @staticmethod
    async def _resolve_profile_author(db: AsyncSession, hasn_id: str) -> dict[str, Any]:
        """
        解析主页作者身份，构造与信息流条目同形的 author（含 agent 的 owner）。

        主页帖子/文章列表所有条目同属一个 hasn_id（human 或 agent），故只需解析一次，
        供 get_profile_posts / get_profile_articles 复用，避免前端按 content_type/author
        渲染失败（缺 author 会渲染成空头像卡）。
        """
        author_human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if author_human is not None:
            return {
                'hasn_id': hasn_id,
                'type': 'human',
                'display_name': author_human.nickname or hasn_id,
                'avatar': author_human.avatar,
            }

        author_agent = (
            await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
        ).scalar_one_or_none()
        author_info: dict[str, Any] = {
            'hasn_id': hasn_id,
            'type': 'agent',
            'display_name': (author_agent.display_name if author_agent else None) or hasn_id,
            'avatar': author_agent.avatar if author_agent else None,
        }
        if author_agent and author_agent.owner_id:
            owner_human = (
                await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == author_agent.owner_id))
            ).scalar_one_or_none()
            author_info['owner'] = {
                'hasn_id': author_agent.owner_id,
                'display_name': (owner_human.nickname if owner_human else None) or author_agent.owner_id,
            }
        return author_info

    @staticmethod
    async def get_profile_posts(
        db: AsyncSession,
        *,
        hasn_id: str,
        viewer_user_id: int,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        获取主页帖子列表

        :param db: 数据库会话
        :param hasn_id: 目标 hasn_id
        :param viewer_user_id: 查看者用户 ID
        :param cursor: 分页游标
        :param limit: 每页条数
        :return: 帖子列表
        """
        stmt = (
            select(HasnPosts)
            .where(
                HasnPosts.author_hasn_id == hasn_id,
                HasnPosts.status == 'published',
            )
            .order_by(HasnPosts.published_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        posts = result.scalars().all()

        # 作者身份（该列表所有帖子同属 hasn_id）+ 查看者点赞态，与信息流条目同形，
        # 否则前端复用的帖子卡缺 author/content_type 会渲染成空头像卡。
        author_info = await CommunityService._resolve_profile_author(db, hasn_id)
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)
        post_ids = [post.post_id for post in posts]
        liked_ids, _ = await CommunityService._batch_reactions(db, viewer_hasn_id, 'post', post_ids)

        items = [{
                'content_type': 'post',
                'post_id': post.post_id,
                'author': author_info,
                'content': post.content,
                'tags': post.tags or [],
                'like_count': post.like_count,
                'comment_count': post.comment_count,
                'published_time': post.published_time.isoformat() if post.published_time else None,
                'is_liked': post.post_id in liked_ids,
            } for post in posts]

        return {
            'items': items,
            'next_cursor': posts[-1].post_id if posts else None,
        }

    @staticmethod
    async def get_profile_articles(
        db: AsyncSession,
        *,
        hasn_id: str,
        viewer_user_id: int,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        获取主页文章列表

        :param db: 数据库会话
        :param hasn_id: 目标 hasn_id
        :param viewer_user_id: 查看者用户 ID
        :param cursor: 分页游标
        :param limit: 每页条数
        :return: 文章列表
        """
        stmt = (
            select(HasnArticles)
            .where(
                HasnArticles.author_hasn_id == hasn_id,
                HasnArticles.status == 'published',
            )
            .order_by(HasnArticles.published_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        articles = result.scalars().all()

        # 作者身份（该列表所有文章同属 hasn_id）+ 查看者点赞/收藏态，与信息流条目同形
        author_info = await CommunityService._resolve_profile_author(db, hasn_id)
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)
        article_ids = [article.article_id for article in articles]
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'article', article_ids
        )

        items = [{
                'content_type': 'article',
                'article_id': article.article_id,
                'author': author_info,
                'title': article.title,
                'summary': effective_summary(article.summary, article.content),
                'cover_url': article.cover_url,
                'tags': article.tags or [],
                'reference_cards': _present_reference_cards(
                    article.reference_cards, viewer_hasn_id
                ),
                'like_count': article.like_count,
                'comment_count': article.comment_count,
                'read_time_min': article.read_time_min,
                'published_time': article.published_time.isoformat() if article.published_time else None,
                'is_liked': article.article_id in liked_ids,
                'is_collected': article.article_id in collected_ids,
            } for article in articles]

        return {
            'items': items,
            'next_cursor': articles[-1].article_id if articles else None,
        }

    @staticmethod
    async def get_profile_agents(
        db: AsyncSession,
        *,
        hasn_id: str,
        viewer_user_id: int,
    ) -> list[dict[str, Any]]:
        """
        获取主页拥有的 Agent 列表

        :param db: 数据库会话
        :param hasn_id: 目标 hasn_id
        :param viewer_user_id: 查看者用户 ID
        :return: Agent 列表
        """
        # 查询该用户拥有的 Agent（hasn_agents 无 follower_count 列，改按创建时间倒序）
        stmt = (
            select(HasnAgents)
            .where(HasnAgents.owner_id == hasn_id)
            .order_by(HasnAgents.created_time.desc())
        )

        result = await db.execute(stmt)
        agents = result.scalars().all()

        # 查询主人真实 display_name（修 owner display_name 写死，doc-12 C4）
        owner_human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        owner_display_name = (owner_human.nickname if owner_human else None) or hasn_id

        # 查询当前用户是否已关注这些 Agent
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)

        agent_list = []
        for agent in agents:
            # 检查是否已关注
            is_following = False
            if viewer_hasn_id:
                follow_stmt = select(HasnFollows).where(
                    HasnFollows.follower_hasn_id == viewer_hasn_id,
                    HasnFollows.target_type == 'agent',
                    HasnFollows.target_hasn_id == agent.hasn_id,
                )
                follow_result = await db.execute(follow_stmt)
                is_following = follow_result.scalars().first() is not None

            # 实时统计该 Agent 的粉丝数
            agent_follower_count = (
                await db.execute(
                    select(func.count())
                    .select_from(HasnFollows)
                    .where(
                        HasnFollows.target_type == 'agent',
                        HasnFollows.target_hasn_id == agent.hasn_id,
                    )
                )
            ).scalar() or 0

            agent_list.append({
                'hasn_id': agent.hasn_id,
                'display_name': agent.display_name,
                'profession': agent.profession,
                'description': agent.description,
                'bio': agent.bio or '',
                'avatar': agent.avatar,
                'owner': {
                    'hasn_id': hasn_id,
                    'display_name': owner_display_name,
                },
                'follower_count': int(agent_follower_count),
                'is_following': is_following,
            })

        return agent_list

    @staticmethod
    async def get_profile_collections(
        db: AsyncSession,
        *,
        hasn_id: str,
        viewer_user_id: int,
    ) -> list[dict[str, Any]]:
        """
        获取主页公开收藏夹列表

        :param db: 数据库会话
        :param hasn_id: 目标 hasn_id
        :param viewer_user_id: 查看者用户 ID
        :return: 收藏夹列表
        """
        # 查询公开收藏夹
        stmt = (
            select(HasnCollections)
            .where(
                HasnCollections.owner_hasn_id == hasn_id,
                HasnCollections.is_public == True,  # noqa: E712
            )
            .order_by(HasnCollections.created_time.desc())
        )

        result = await db.execute(stmt)
        collections = result.scalars().all()

        collection_list = [{
                'collection_id': collection.collection_id,
                'name': collection.name,
                'is_public': collection.is_public,
                'item_count': collection.item_count,
            } for collection in collections]

        return collection_list

    # ==================== 收藏夹与收藏动作 ====================

    @staticmethod
    async def list_collections(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
    ) -> dict[str, Any]:
        """收藏夹列表（含 item_count），doc-13 §3.2。"""
        stmt = (
            select(HasnCollections)
            .where(HasnCollections.owner_hasn_id == owner_hasn_id)
            .order_by(HasnCollections.created_time.desc())
        )
        collections = (await db.execute(stmt)).scalars().all()
        return {
            'items': [
                {
                    'collection_id': c.collection_id,
                    'name': c.name,
                    'is_public': c.is_public,
                    'item_count': c.item_count,
                    'created_time': c.created_time.isoformat() if c.created_time else None,
                }
                for c in collections
            ]
        }

    @staticmethod
    async def create_collection(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        name: str,
        is_public: bool = False,
    ) -> dict[str, Any]:
        """创建收藏夹，doc-13 §3.2。"""
        collection_id = f'col_{uuid4_str()[:12]}'
        collection = HasnCollections(
            collection_id=collection_id,
            owner_hasn_id=owner_hasn_id,
            name=name,
            is_public=is_public,
            item_count=0,
        )
        db.add(collection)
        await db.flush()
        return {
            'collection_id': collection_id,
            'name': name,
            'is_public': is_public,
            'item_count': 0,
        }

    @staticmethod
    async def delete_collection(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        collection_id: str,
    ) -> None:
        """删除收藏夹（仅本人）+ 级联删除其收藏项，doc-13 §3.2。"""
        collection = (
            await db.execute(
                select(HasnCollections).where(
                    HasnCollections.collection_id == collection_id,
                    HasnCollections.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if not collection:
            raise errors.NotFoundError(msg='收藏夹不存在')

        # 删除项 + 回退被收藏内容的 collect_count
        items = (
            await db.execute(
                select(HasnCollectionItems).where(
                    HasnCollectionItems.collection_id == collection_id
                )
            )
        ).scalars().all()
        for item in items:
            await CommunityService._adjust_collect_count(db, item.target_type, item.target_id, -1)
            await db.delete(item)
        await db.delete(collection)
        await db.flush()

    @staticmethod
    async def get_collection_items(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        collection_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """收藏夹内容列表（回填 target 内容摘要），doc-13 §3.2。"""
        collection = (
            await db.execute(
                select(HasnCollections).where(
                    HasnCollections.collection_id == collection_id,
                    HasnCollections.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if not collection:
            raise errors.NotFoundError(msg='收藏夹不存在')

        stmt = (
            select(HasnCollectionItems)
            .where(HasnCollectionItems.collection_id == collection_id)
            .order_by(HasnCollectionItems.id.desc())
        )
        if cursor:
            stmt = stmt.where(HasnCollectionItems.id < int(cursor))
        stmt = stmt.limit(limit + 1)
        items = (await db.execute(stmt)).scalars().all()

        has_more = len(items) > limit
        items = items[:limit]

        # 批量回填内容摘要
        post_ids = [i.target_id for i in items if i.target_type == 'post']
        article_ids = [i.target_id for i in items if i.target_type == 'article']
        post_map: dict[str, Any] = {}
        article_map: dict[str, Any] = {}
        if post_ids:
            for p in (await db.execute(select(HasnPosts).where(HasnPosts.post_id.in_(post_ids)))).scalars().all():
                post_map[p.post_id] = p
        if article_ids:
            for a in (await db.execute(select(HasnArticles).where(HasnArticles.article_id.in_(article_ids)))).scalars().all():
                article_map[a.article_id] = a

        result_items = []
        for item in items:
            entry: dict[str, Any] = {
                'target_type': item.target_type,
                'target_id': item.target_id,
            }
            if item.target_type == 'post' and item.target_id in post_map:
                p = post_map[item.target_id]
                entry['preview'] = (p.content or '')[:120]
                entry['like_count'] = p.like_count
            elif item.target_type == 'article' and item.target_id in article_map:
                a = article_map[item.target_id]
                entry['title'] = a.title
                entry['preview'] = (a.summary or a.content or '')[:120]
                entry['like_count'] = a.like_count
            result_items.append(entry)

        return {
            'items': result_items,
            'next_cursor': str(items[-1].id) if has_more and items else None,
        }

    @staticmethod
    async def get_collection_detail(
        db: AsyncSession,
        *,
        viewer_hasn_id: str,
        collection_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        收藏夹详情（含 owner 信息 + 内容项），用于他人主页"公开收藏夹"直达。

        access 控制：仅 owner 本人或 ``is_public`` 收藏夹可见；
        私有且非本人 → 404（不泄露私有收藏夹是否存在）。
        """
        collection = (
            await db.execute(
                select(HasnCollections).where(HasnCollections.collection_id == collection_id)
            )
        ).scalar_one_or_none()
        if not collection:
            raise errors.NotFoundError(msg='收藏夹不存在')

        is_owner = collection.owner_hasn_id == viewer_hasn_id
        if not collection.is_public and not is_owner:
            # 不泄露私有收藏夹的存在
            raise errors.NotFoundError(msg='收藏夹不存在')

        owner = (
            await db.execute(
                select(HasnHumans).where(HasnHumans.hasn_id == collection.owner_hasn_id)
            )
        ).scalar_one_or_none()

        # 复用 owner-scoped 的内容项投影（access 已在上方校验）
        items_page = await CommunityService.get_collection_items(
            db,
            owner_hasn_id=collection.owner_hasn_id,
            collection_id=collection_id,
            cursor=cursor,
            limit=limit,
        )

        return {
            'collection': {
                'collection_id': collection.collection_id,
                'name': collection.name,
                'is_public': collection.is_public,
                'item_count': collection.item_count,
                'is_owner': is_owner,
                'owner': {
                    'hasn_id': collection.owner_hasn_id,
                    'display_name': (owner.nickname if owner else collection.owner_hasn_id),
                    'avatar': (owner.avatar if owner else None),
                },
            },
            'items': items_page['items'],
            'next_cursor': items_page['next_cursor'],
        }

    @staticmethod
    async def _adjust_collect_count(db: AsyncSession, target_type: str, target_id: str, delta: int) -> None:
        """维护被收藏内容的 collect_count。"""
        if target_type == 'post':
            obj = (await db.execute(select(HasnPosts).where(HasnPosts.post_id == target_id))).scalars().first()
        elif target_type == 'article':
            obj = (await db.execute(select(HasnArticles).where(HasnArticles.article_id == target_id))).scalars().first()
        else:
            obj = None
        if obj is not None:
            obj.collect_count = max(0, (obj.collect_count or 0) + delta)

    @staticmethod
    async def collect(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        target_type: str,
        target_id: str,
        collection_id: str | None = None,
    ) -> dict[str, Any]:
        """收藏内容（缺省进默认收藏夹，首次自动创建），doc-13 §2.4/§3.2。"""
        # 解析目标收藏夹
        if collection_id:
            collection = (
                await db.execute(
                    select(HasnCollections).where(
                        HasnCollections.collection_id == collection_id,
                        HasnCollections.owner_hasn_id == owner_hasn_id,
                    )
                )
            ).scalar_one_or_none()
            if not collection:
                raise errors.NotFoundError(msg='收藏夹不存在')
        else:
            # 默认收藏夹：取最早的一个，没有则创建
            collection = (
                await db.execute(
                    select(HasnCollections)
                    .where(HasnCollections.owner_hasn_id == owner_hasn_id)
                    .order_by(HasnCollections.created_time.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not collection:
                collection = HasnCollections(
                    collection_id=f'col_{uuid4_str()[:12]}',
                    owner_hasn_id=owner_hasn_id,
                    name='默认收藏夹',
                    is_public=False,
                    item_count=0,
                )
                db.add(collection)
                await db.flush()

        # 幂等：已收藏则直接返回
        existing = (
            await db.execute(
                select(HasnCollectionItems).where(
                    HasnCollectionItems.collection_id == collection.collection_id,
                    HasnCollectionItems.target_type == target_type,
                    HasnCollectionItems.target_id == target_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {'collection_id': collection.collection_id, 'is_collected': True}

        db.add(
            HasnCollectionItems(
                collection_id=collection.collection_id,
                target_type=target_type,
                target_id=target_id,
            )
        )
        collection.item_count = (collection.item_count or 0) + 1
        await CommunityService._adjust_collect_count(db, target_type, target_id, 1)
        await db.flush()

        # 触发通知：内容作者（Agent 内容额外 relay 给主人；自藏跳过）
        # 注意：collect 的 owner_hasn_id 参数 = 收藏者本人（actor）；内容作者另取。
        author_hasn_id = None
        author_type = None
        content_owner_hasn_id = None
        preview = None
        if target_type == 'post':
            obj = (await db.execute(select(HasnPosts).where(HasnPosts.post_id == target_id))).scalars().first()
            if obj:
                author_hasn_id, author_type, content_owner_hasn_id, preview = (
                    obj.author_hasn_id, obj.author_type, obj.owner_hasn_id, obj.content,
                )
        elif target_type == 'article':
            obj = (await db.execute(select(HasnArticles).where(HasnArticles.article_id == target_id))).scalars().first()
            if obj:
                author_hasn_id, author_type, content_owner_hasn_id, preview = (
                    obj.author_hasn_id, obj.author_type, obj.owner_hasn_id, obj.title,
                )
        if author_hasn_id:
            from backend.app.hasn_community.service.notification_service import notification_service

            await notification_service.notify_content_interaction(
                db,
                ntype='community_collect',
                actor_hasn_id=owner_hasn_id,
                content_type=target_type,
                content_id=target_id,
                author_hasn_id=author_hasn_id,
                author_type=author_type or 'human',
                owner_hasn_id=content_owner_hasn_id,
                preview=preview,
            )

        return {'collection_id': collection.collection_id, 'is_collected': True}

    @staticmethod
    async def uncollect(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        target_type: str,
        target_id: str,
    ) -> dict[str, Any]:
        """取消收藏（移除该 owner 所有收藏夹中的该目标），doc-13 §3.2。"""
        items = (
            await db.execute(
                select(HasnCollectionItems)
                .join(HasnCollections, HasnCollectionItems.collection_id == HasnCollections.collection_id)
                .where(
                    HasnCollections.owner_hasn_id == owner_hasn_id,
                    HasnCollectionItems.target_type == target_type,
                    HasnCollectionItems.target_id == target_id,
                )
            )
        ).scalars().all()
        if not items:
            return {'is_collected': False}

        affected_collection_ids = set()
        for item in items:
            affected_collection_ids.add(item.collection_id)
            await db.delete(item)
        # 回退 item_count 与 collect_count
        for cid in affected_collection_ids:
            collection = (
                await db.execute(select(HasnCollections).where(HasnCollections.collection_id == cid))
            ).scalars().first()
            if collection:
                collection.item_count = max(0, (collection.item_count or 0) - 1)
        await CommunityService._adjust_collect_count(db, target_type, target_id, -len(items))
        await db.flush()
        return {'is_collected': False}

    # ==================== 热门话题 ====================

    @staticmethod
    async def get_trending_topics(
        db: AsyncSession,
        *,
        limit: int = 5,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """
        获取热门话题（真实统计，doc-12 C3）。

        聚合近 ``days`` 天内已发布帖子+文章的 tags：
        - post_count：使用该 tag 的内容数
        - heat：内容互动量之和（点赞+评论），用于排序
        - trend：对比近半窗口 vs 远半窗口的内容数（rising/stable/falling），真实计算

        :param db: 数据库会话
        :param limit: 返回数量
        :param days: 统计窗口（天）
        :return: 热门话题列表 [{topic, post_count, trend}]
        """
        half = max(1, days // 2)
        sql = text(
            """
            WITH tagged AS (
                SELECT unnest(tags) AS tag, like_count, comment_count, published_time
                FROM hasn_posts
                WHERE status = 'published'
                  AND published_time >= now() - make_interval(days => :days)
                UNION ALL
                SELECT unnest(tags) AS tag, like_count, comment_count, published_time
                FROM hasn_articles
                WHERE status = 'published'
                  AND published_time >= now() - make_interval(days => :days)
            )
            SELECT
                tag,
                count(*) AS post_count,
                COALESCE(SUM(like_count + comment_count), 0) AS heat,
                count(*) FILTER (WHERE published_time >= now() - make_interval(days => :half)) AS recent_cnt,
                count(*) FILTER (WHERE published_time <  now() - make_interval(days => :half)) AS older_cnt
            FROM tagged
            WHERE tag IS NOT NULL AND tag <> ''
            GROUP BY tag
            ORDER BY heat DESC, post_count DESC, tag ASC
            LIMIT :limit
            """
        )
        rows = (
            await db.execute(sql, {'days': days, 'half': half, 'limit': limit})
        ).mappings().all()

        topics: list[dict[str, Any]] = []
        for row in rows:
            recent = row['recent_cnt']
            older = row['older_cnt']
            if recent > older:
                trend = 'rising'
            elif recent < older:
                trend = 'falling'
            else:
                trend = 'stable'
            topics.append({
                'topic': row['tag'],
                'post_count': int(row['post_count']),
                'trend': trend,
            })
        return topics

    # ==================== 推荐 Agent ====================

    @staticmethod
    async def get_recommended_agents(
        db: AsyncSession,
        *,
        viewer_user_id: int | None = None,
        category: str | None = None,
        sort: str = 'relevance',
        capability: str | None = None,
        cursor: str | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        """
        获取推荐/广场 Agent（doc-13 §3.4，对应 D1/E-4 筛选）。

        - category/capability：按 capability_summary_json 文本匹配过滤（真实过滤）
        - sort：relevance（粉丝数）/ collected（内容被收藏数）/ active（最近活跃）
        - cursor：offset 分页
        修复历史 bug：social_enabled（非 hasn_social_enabled）、follower_count 实时统计（无该列）。

        :return: {items, next_cursor}
        """
        OwnerHuman = aliased(HasnHumans)

        # 实时粉丝数（相关子查询）
        follower_sq = (
            select(func.count())
            .select_from(HasnFollows)
            .where(
                HasnFollows.target_type == 'agent',
                HasnFollows.target_hasn_id == HasnAgents.hasn_id,
            )
            .correlate(HasnAgents)
            .scalar_subquery()
        )
        # 实时关注数（该 Agent 作为 follower 关注的条数）
        following_sq = (
            select(func.count())
            .select_from(HasnFollows)
            .where(HasnFollows.follower_hasn_id == HasnAgents.hasn_id)
            .correlate(HasnAgents)
            .scalar_subquery()
        )
        # 内容被收藏数（相关子查询，用于 collected 排序）
        collected_sq = (
            select(func.coalesce(func.sum(HasnPosts.collect_count), 0))
            .where(HasnPosts.author_hasn_id == HasnAgents.hasn_id)
            .correlate(HasnAgents)
            .scalar_subquery()
        )

        stmt = (
            select(
                HasnAgents,
                OwnerHuman.hasn_id.label('owner_hasn_id'),
                OwnerHuman.nickname.label('owner_nickname'),
                follower_sq.label('follower_count'),
                following_sq.label('following_count'),
                collected_sq.label('collected_count'),
            )
            .join(OwnerHuman, HasnAgents.owner_id == OwnerHuman.hasn_id)
            .where(HasnAgents.social_enabled == True)  # noqa: E712
        )

        # 能力/分类过滤：capability_summary_json 文本匹配
        keyword = capability or category
        if keyword:
            stmt = stmt.where(
                cast(HasnAgents.capability_summary_json, Text).ilike(f'%{keyword}%')
            )

        # 排序
        if sort == 'collected':
            stmt = stmt.order_by(collected_sq.desc(), HasnAgents.created_time.desc())
        elif sort == 'active':
            stmt = stmt.order_by(HasnAgents.last_heartbeat_at.desc().nullslast(), HasnAgents.created_time.desc())
        else:  # relevance
            stmt = stmt.order_by(follower_sq.desc(), HasnAgents.created_time.desc())

        # offset 游标
        offset = int(cursor) if cursor else 0
        stmt = stmt.offset(offset).limit(limit + 1)

        rows = (await db.execute(stmt)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]

        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)

        agents = []
        for row in rows:
            agent = row.HasnAgents
            is_following = False
            if viewer_hasn_id:
                is_following = (
                    await db.execute(
                        select(HasnFollows.id).where(
                            HasnFollows.follower_hasn_id == viewer_hasn_id,
                            HasnFollows.target_type == 'agent',
                            HasnFollows.target_hasn_id == agent.hasn_id,
                        ).limit(1)
                    )
                ).first() is not None

            agents.append({
                'hasn_id': agent.hasn_id,
                'display_name': agent.display_name,
                'profession': agent.profession,
                'description': agent.description,
                'bio': agent.bio or '',
                'avatar': agent.avatar,
                'capability_summary': agent.capability_summary_json or {},
                'owner': {
                    'hasn_id': row.owner_hasn_id,
                    'display_name': row.owner_nickname or row.owner_hasn_id,
                },
                'follower_count': int(row.follower_count or 0),
                'following_count': int(row.following_count or 0),
                'collected_count': int(row.collected_count or 0),
                'is_following': is_following,
            })

        return {
            'items': agents,
            'next_cursor': str(offset + limit) if has_more else None,
        }

    # ==================== 待确认草稿 ====================

    @staticmethod
    async def get_pending_drafts(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        cursor: str | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        """
        获取待确认草稿（需要主人确认的 Agent 草稿）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param cursor: 分页游标
        :param limit: 每页条数
        :return: 待确认草稿列表
        """
        # 查询当前用户拥有的 Agent
        agent_stmt = select(HasnAgents.hasn_id).where(HasnAgents.owner_id == hasn_id)
        agent_result = await db.execute(agent_stmt)
        agent_ids = [row[0] for row in agent_result.all()]

        if not agent_ids:
            return {'items': [], 'next_cursor': None}

        # 查询这些 Agent 的待确认草稿
        AuthorAgent = aliased(HasnAgents)

        stmt = (
            select(
                HasnPosts,
                AuthorAgent.display_name.label('agent_display_name'),
            )
            .join(AuthorAgent, HasnPosts.author_hasn_id == AuthorAgent.hasn_id)
            .where(
                HasnPosts.author_type == 'agent',
                HasnPosts.author_hasn_id.in_(agent_ids),
                HasnPosts.status == 'pending_review',
            )
            .order_by(HasnPosts.created_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        rows = result.all()

        items = []
        for row in rows:
            post = row.HasnPosts

            items.append({
                'content_type': 'post',
                'post_id': post.post_id,
                'origin_workspace': {
                    'kind': post.origin_workspace_kind,
                    'id': post.origin_workspace_id,
                },
                'author': {
                    'hasn_id': post.author_hasn_id,
                    'type': 'agent',
                    'display_name': row.agent_display_name or post.author_hasn_id,
                },
                'content': post.content,
                'tags': post.tags or [],
                'like_count': post.like_count,
                'comment_count': post.comment_count,
                'published_time': post.created_time.isoformat() if post.created_time else None,
                'is_liked': False,
                'is_collected': False,
            })

        return {
            'items': items,
            'next_cursor': rows[-1].HasnPosts.post_id if rows else None,
        }

    # ==================== 文章相关方法 ====================

    @staticmethod
    async def create_article(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        title: str,
        content: str,
        summary: str | None = None,
        cover_url: str | None = None,
        tags: list[str] | None = None,
        visibility: str = 'public',
        comment_policy: str = 'all',
        generation_type: str = 'human',
        reference_cards: list[dict[str, Any]] | None = None,
        circle_id: str | None = None,
        doc_placement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        创建文章（WebUI Owner JWT 通道：作者恒为操作者本人 human）

        身份模型见 docs/.../13-社区设计补丁 §1.5：WebUI 发文永远是 human，
        Agent 自主发文只走 MCP + Agent JWT（/api/v1/community/agent/*），
        不接受请求体身份字段，杜绝 as_agent_hasn_id 冒名越权。

        发布汇聚（实施/95 §2.4）：tag 归一→话题关联；circle_id 校验成员+post_policy；
        doc_placement 建/复用目录链 → article 叶子落位 → 设可见性/密码。

        :param circle_id: 所属圈子（可选）
        :param doc_placement: 文集落位（可选，见 17 §6.3）
        :return: 文章信息
        """
        from backend.app.hasn_community.model.hasn_articles import HasnArticles
        from backend.app.hasn_community.service.circle_service import circle_service
        from backend.app.hasn_community.service.doc_service import doc_service
        from backend.app.hasn_community.service.topic_service import topic_service

        # 生成 article_id
        article_id = f"art_{uuid4_str()[:12]}"

        # 作者恒为当前 Owner JWT 对应的 human（身份 = 认证凭证，不接受请求体指定）
        author_type = 'human'
        author_hasn_id = hasn_id
        author_user_id = user_id
        owner_hasn_id = hasn_id

        # TODO: 获取当前 active workspace
        workspace_kind = 'personal'
        workspace_id = str(user_id)

        status = 'published'
        if circle_id:
            _circle, needs_review = await circle_service.assert_can_post(db, circle_id=circle_id, actor_hasn_id=author_hasn_id)
            if needs_review:
                status = 'pending_review'

        # 创建文章
        article = HasnArticles(
            article_id=article_id,
            author_type=author_type,
            author_hasn_id=author_hasn_id,
            author_user_id=author_user_id,
            owner_hasn_id=owner_hasn_id,
            origin_workspace_kind=workspace_kind,
            origin_workspace_id=workspace_id,
            title=title,
            summary=summary,
            cover_url=cover_url,
            content=content,
            tags=tags or [],
            reference_cards=_normalize_reference_cards(
                reference_cards, author_hasn_id=author_hasn_id
            ),
            visibility=visibility,
            comment_policy=comment_policy,
            generation_type=generation_type if generation_type in ALLOWED_GENERATION_TYPES else 'human',
            status=status,
            circle_id=circle_id,
            published_time=timezone.now() if status == 'published' else None,
        )

        db.add(article)
        await db.flush()

        # 话题归一 + 关联
        await topic_service.rewrite_content_topics(db, content_type='article', content_id=article_id, owner_hasn_id=owner_hasn_id, tags=tags)
        if circle_id and status == 'published':
            await circle_service.bump_content_count(db, circle_id=circle_id)

        placement_result = None
        if doc_placement:
            placement_result = await doc_service.place_article(
                db, article_id=article_id, article_title=title, actor_hasn_id=owner_hasn_id, owner_user_id=user_id,
                author_type='human', author_hasn_id=author_hasn_id, placement=doc_placement, allow_visibility=True,
            )

        return {
            'article_id': article_id,
            'status': status,
            'circle_id': circle_id,
            'doc_placement': placement_result,
            'published_time': article.published_time.isoformat() if article.published_time else None,
        }

    @staticmethod
    async def get_article(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        article_id: str,
    ) -> dict[str, Any]:
        """
        获取文章详情

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param article_id: 文章 ID
        :return: 文章详情
        """
        from backend.app.hasn_community.model.hasn_articles import HasnArticles
        from backend.app.hasn_core import HasnAgents, HasnHumans

        # 查询文章
        stmt = select(HasnArticles).where(HasnArticles.article_id == article_id)
        result = await db.execute(stmt)
        article = result.scalar_one_or_none()

        if not article:
            from backend.common.exception import errors

            raise errors.NotFoundError(msg='文章不存在')

        # 查询作者信息
        author_info = {'hasn_id': article.author_hasn_id, 'type': article.author_type}

        if article.author_type == 'human':
            stmt = select(HasnHumans).where(HasnHumans.hasn_id == article.author_hasn_id)
            result = await db.execute(stmt)
            human = result.scalar_one_or_none()
            if human:
                author_info['display_name'] = human.nickname
                author_info['avatar'] = human.avatar
        else:
            stmt = select(HasnAgents).where(HasnAgents.hasn_id == article.author_hasn_id)
            result = await db.execute(stmt)
            agent = result.scalar_one_or_none()
            if agent:
                author_info['display_name'] = agent.display_name
                author_info['avatar'] = agent.avatar

                # 查询 Agent 的主人信息（HasnAgents 主人字段是 owner_id，不是 owner_hasn_id）
                stmt = select(HasnHumans).where(HasnHumans.hasn_id == agent.owner_id)
                result = await db.execute(stmt)
                owner = result.scalar_one_or_none()
                if owner:
                    author_info['owner'] = {
                        'hasn_id': owner.hasn_id,
                        'display_name': owner.nickname,
                    }

        # 回填当前 viewer 对该文章的点赞/收藏态（doc-12 B-3，与 get_post 一致）
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, hasn_id, 'article', [article.article_id]
        )
        is_liked = article.article_id in liked_ids
        is_collected = article.article_id in collected_ids

        return {
            'article_id': article.article_id,
            'title': article.title,
            'summary': effective_summary(article.summary, article.content),
            'cover_url': article.cover_url,
            'content': article.content,
            'author': author_info,
            'tags': article.tags or [],
            'reference_cards': _present_reference_cards(
                article.reference_cards, hasn_id
            ),
            'visibility': article.visibility,
            'comment_policy': article.comment_policy,
            'generation_type': article.generation_type,
            'like_count': article.like_count,
            'comment_count': article.comment_count,
            'read_time_min': article.read_time_min,
            'published_time': article.published_time.isoformat() if article.published_time else None,
            'updated_time': article.updated_time.isoformat() if article.updated_time else None,
            'is_liked': is_liked,
            'is_collected': is_collected,
        }

    @staticmethod
    async def get_agent_article_resource(
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        article_id: str,
    ) -> dict[str, Any]:
        stmt = select(HasnArticles).where(
            HasnArticles.article_id == article_id,
            HasnArticles.status == 'published',
        )
        result = await db.execute(stmt)
        article = result.scalar_one_or_none()
        if not article:
            raise errors.NotFoundError(msg='文章不存在')
        _assert_agent_can_read_community_resource(agent=agent, resource=article)
        return {
            'resource': {
                'type': 'community.article',
                'id': article.article_id,
                'app_id': 'community',
                'uri': f'hasn://community/articles/{article.article_id}',
            },
            'summary': article.summary or _safe_summary(article.content),
            'content': article.content,
            'title': article.title,
            'author': {
                'hasn_id': article.author_hasn_id,
                'type': article.author_type,
                'owner_hasn_id': article.owner_hasn_id,
            },
            'origin_workspace': {
                'kind': article.origin_workspace_kind,
                'id': article.origin_workspace_id,
            },
            'published_time': article.published_time.isoformat() if article.published_time else None,
        }

    @staticmethod
    async def update_article(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        article_id: str,
        title: str | None = None,
        summary: str | None = None,
        cover_url: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        visibility: str | None = None,
        comment_policy: str | None = None,
        generation_type: str | None = None,
        reference_cards: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        更新文章

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param article_id: 文章 ID
        :param title: 文章标题
        :param summary: 文章摘要
        :param cover_url: 封面图片 URL
        :param content: 文章内容
        :param tags: 话题标签
        :param visibility: 可见范围
        :param comment_policy: 评论策略
        :return: 更新结果
        """
        from backend.app.hasn_community.model.hasn_articles import HasnArticles

        # 查询文章
        stmt = select(HasnArticles).where(HasnArticles.article_id == article_id)
        result = await db.execute(stmt)
        article = result.scalar_one_or_none()

        if not article:
            from backend.common.exception import errors

            raise errors.NotFoundError(msg='文章不存在')

        # 验证权限（只有作者或主人可以编辑）
        if article.author_hasn_id != hasn_id and article.owner_hasn_id != hasn_id:
            from backend.common.exception import errors

            raise errors.ForbiddenError(msg='无权编辑此文章')

        # 更新字段
        if title is not None:
            article.title = title
        if summary is not None:
            article.summary = summary
        if cover_url is not None:
            article.cover_url = cover_url
        if content is not None:
            article.content = content
        if tags is not None:
            article.tags = tags
        if visibility is not None:
            article.visibility = visibility
        if comment_policy is not None:
            article.comment_policy = comment_policy
        if generation_type is not None and generation_type in ALLOWED_GENERATION_TYPES:
            article.generation_type = generation_type
        if reference_cards is not None:
            article.reference_cards = _normalize_reference_cards(
                reference_cards, author_hasn_id=article.author_hasn_id
            )

        article.updated_time = timezone.now()

        await db.flush()

        return {
            'article_id': article_id,
            'status': 'published',
            'updated_time': article.updated_time.isoformat() if article.updated_time else None,
        }

    @staticmethod
    async def delete_post(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        post_id: str,
    ) -> dict[str, Any]:
        """
        删除帖子（与 delete_article 对称）

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param post_id: 帖子 ID
        :return: 删除结果
        """
        # 查询帖子
        stmt = select(HasnPosts).where(HasnPosts.post_id == post_id)
        result = await db.execute(stmt)
        post = result.scalar_one_or_none()

        if not post:
            from backend.common.exception import errors

            raise errors.NotFoundError(msg='帖子不存在')

        # 验证权限（只有作者或主人可以删除）
        if post.author_hasn_id != hasn_id and post.owner_hasn_id != hasn_id:
            from backend.common.exception import errors

            raise errors.ForbiddenError(msg='无权删除此帖子')

        # 软删除
        post.status = 'deleted'
        post.updated_time = timezone.now()

        await db.flush()

        return {
            'post_id': post_id,
            'status': 'deleted',
        }

    @staticmethod
    async def delete_article(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        article_id: str,
    ) -> dict[str, Any]:
        """
        删除文章

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param hasn_id: 用户的 hasn_id
        :param article_id: 文章 ID
        :return: 删除结果
        """
        from backend.app.hasn_community.model.hasn_articles import HasnArticles

        # 查询文章
        stmt = select(HasnArticles).where(HasnArticles.article_id == article_id)
        result = await db.execute(stmt)
        article = result.scalar_one_or_none()

        if not article:
            from backend.common.exception import errors

            raise errors.NotFoundError(msg='文章不存在')

        # 验证权限（只有作者或主人可以删除）
        if article.author_hasn_id != hasn_id and article.owner_hasn_id != hasn_id:
            from backend.common.exception import errors

            raise errors.ForbiddenError(msg='无权删除此文章')

        # 软删除
        article.status = 'deleted'
        article.updated_time = timezone.now()

        await db.flush()

        return {
            'article_id': article_id,
            'status': 'deleted',
        }

    @staticmethod
    async def get_public_article(
        db: AsyncSession,
        *,
        article_id: str,
    ) -> dict[str, Any]:
        """公开（匿名）获取文章详情：仅 status=published 且 visibility=public。

        供 open scope 使用，不接受查看者身份，不做个性化（is_liked/is_collected）。
        """
        stmt = select(HasnArticles).where(
            HasnArticles.article_id == article_id,
            HasnArticles.status == 'published',
            HasnArticles.visibility == 'public',
        )
        article = (await db.execute(stmt)).scalar_one_or_none()
        if not article:
            raise errors.NotFoundError(msg='文章不存在')

        author_info: dict[str, Any] = {'hasn_id': article.author_hasn_id, 'type': article.author_type}
        if article.author_type == 'human':
            human = (
                await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == article.author_hasn_id))
            ).scalar_one_or_none()
            if human:
                author_info['display_name'] = human.nickname
                author_info['avatar'] = human.avatar
        else:
            agent = (
                await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == article.author_hasn_id))
            ).scalar_one_or_none()
            if agent:
                author_info['display_name'] = agent.display_name
                author_info['avatar'] = agent.avatar
                owner = (
                    await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == agent.owner_id))
                ).scalar_one_or_none()
                if owner:
                    author_info['owner'] = {'hasn_id': owner.hasn_id, 'display_name': owner.nickname}

        return {
            'article_id': article.article_id,
            'title': article.title,
            'summary': effective_summary(article.summary, article.content),
            'cover_url': article.cover_url,
            'content': article.content,
            'author': author_info,
            'tags': article.tags or [],
            'reference_cards': _present_reference_cards(article.reference_cards, None),
            'visibility': article.visibility,
            'comment_policy': article.comment_policy,
            'like_count': article.like_count,
            'comment_count': article.comment_count,
            'read_time_min': article.read_time_min,
            'published_time': article.published_time.isoformat() if article.published_time else None,
            'updated_time': article.updated_time.isoformat() if article.updated_time else None,
        }


community_service = CommunityService()
