"""话题体系服务（设计文档 15）。

把话题从 tag 字符串升级为一等实体：归一/resolve、聚合流、trending、搜索、关注、运营。
身份一律由调用方（认证凭证）决定；本服务不读请求体身份。
"""

from __future__ import annotations

import operator

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from backend.app.hasn_community.model import HasnContentTopics, HasnFollows, HasnTopics
from backend.app.hasn_community.service.community_cards import fetch_article_cards, fetch_post_cards
from backend.app.hasn_community.service.content_visibility import content_visibility_sql
from backend.app.hasn_community.service.topic_normalize import normalize_topic_name, slugify_topic
from backend.common.exception import errors
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TopicService:
    # ---------- 归一 / resolve（写路径） ----------

    @staticmethod
    async def _resolve_one(db: AsyncSession, name: str, *, created_by: str | None) -> str | None:
        norm = normalize_topic_name(name)
        if not norm:
            return None
        existing = (
            await db.execute(
                select(HasnTopics).where(func.lower(HasnTopics.name) == norm.lower(), HasnTopics.status == 'active')
            )
        ).scalars().first()
        if existing:
            return existing.topic_id
        topic_id = f'tpc_{uuid4_str()[:12]}'
        slug = slugify_topic(norm, uuid4_str()[:8])
        if (await db.execute(select(HasnTopics.id).where(HasnTopics.slug == slug))).first():
            slug = f't-{uuid4_str()[:8]}'
        topic = HasnTopics(
            topic_id=topic_id, name=norm, slug=slug, status='active',
            is_featured=False, is_official=False, created_by_hasn_id=created_by,
            content_count=0, follow_count=0, view_count=0,
        )
        try:
            async with db.begin_nested():
                db.add(topic)
                await db.flush()
            return topic_id
        except IntegrityError:
            # 并发归一：重查活跃同名
            again = (
                await db.execute(
                    select(HasnTopics).where(func.lower(HasnTopics.name) == norm.lower(), HasnTopics.status == 'active')
                )
            ).scalars().first()
            return again.topic_id if again else None

    @staticmethod
    async def resolve_topics(db: AsyncSession, names: list[str], *, created_by: str | None = None) -> list[dict[str, Any]]:
        """名称数组 → 话题列表（缺则建）。返回 [{topic_id,name,slug,content_count,is_new}]，发布弹窗回显用。"""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in names or []:
            norm = normalize_topic_name(raw)
            key = norm.lower()
            if not norm or key in seen:
                continue
            seen.add(key)
            before = (
                await db.execute(select(HasnTopics).where(func.lower(HasnTopics.name) == key, HasnTopics.status == 'active'))
            ).scalars().first()
            tid = await TopicService._resolve_one(db, norm, created_by=created_by)
            if not tid:
                continue
            topic = (await db.execute(select(HasnTopics).where(HasnTopics.topic_id == tid))).scalars().first()
            if topic is None:
                raise errors.ServerError(msg='话题创建成功但权威记录不可见')
            out.append({
                'topic_id': topic.topic_id, 'name': topic.name, 'slug': topic.slug,
                'content_count': topic.content_count, 'is_new': before is None,
            })
        return out

    @staticmethod
    async def rewrite_content_topics(
        db: AsyncSession, *, content_type: str, content_id: str, owner_hasn_id: str, tags: list[str] | None
    ) -> list[str]:
        """先删后插，幂等重写内容↔话题关联；维护 content_count/last_active。返回 topic_id 列表。"""
        resolved = await TopicService.resolve_topics(db, tags or [], created_by=owner_hasn_id)
        new_ids = [r['topic_id'] for r in resolved]
        # 删旧关联
        old = (
            await db.execute(
                select(HasnContentTopics.topic_id).where(
                    HasnContentTopics.content_type == content_type, HasnContentTopics.content_id == content_id
                )
            )
        ).scalars().all()
        await db.execute(
            delete(HasnContentTopics).where(
                HasnContentTopics.content_type == content_type, HasnContentTopics.content_id == content_id
            )
        )
        for tid in new_ids:
            db.add(HasnContentTopics(topic_id=tid, content_type=content_type, content_id=content_id, owner_hasn_id=owner_hasn_id))
        await db.flush()
        # 维护计数（受影响话题：新旧并集）
        for tid in set(new_ids) | set(old):
            cnt = (await db.execute(select(func.count()).select_from(HasnContentTopics).where(HasnContentTopics.topic_id == tid))).scalar() or 0
            await db.execute(
                update(HasnTopics).where(HasnTopics.topic_id == tid).values(content_count=cnt, last_active_time=timezone.now())
            )
        await db.flush()
        return new_ids

    # ---------- 读路径 ----------

    @staticmethod
    async def _get_by_ident(db: AsyncSession, ident: str) -> HasnTopics | None:
        # 先按权威标识解析：tpc_ 前缀走 topic_id，否则走 slug。
        col = HasnTopics.topic_id if ident.startswith('tpc_') else HasnTopics.slug
        hit = (await db.execute(select(HasnTopics).where(col == ident))).scalars().first()
        if hit:
            return hit
        # 兜底：按归一话题名解析。帖子/文章 tags 存的是名称，中文名 slug 回退为随机 t-xxx，
        # 仅按 slug 命不中；按 lower(name)（唯一索引）兜底，让标签点击对中文标签也可达。
        norm = normalize_topic_name(ident)
        if not norm:
            return None
        return (
            await db.execute(select(HasnTopics).where(func.lower(HasnTopics.name) == norm.lower()))
        ).scalars().first()

    @staticmethod
    def _topic_dict(t: HasnTopics, *, is_following: bool | None = None) -> dict[str, Any]:
        d = {
            'topic_id': t.topic_id, 'name': t.name, 'slug': t.slug, 'description': t.description,
            'cover_url': t.cover_url, 'status': t.status, 'is_featured': t.is_featured, 'is_official': t.is_official,
            'content_count': t.content_count, 'follow_count': t.follow_count, 'view_count': t.view_count,
            'last_active_time': t.last_active_time.isoformat() if t.last_active_time else None,
        }
        if is_following is not None:
            d['is_following'] = is_following
        return d

    @staticmethod
    async def _followed_topic_ids(db: AsyncSession, viewer_hasn_id: str | None, topic_ids: list[str]) -> set[str]:
        """批量查 viewer 已关注的 topic_id 集合（trending/search 回填 is_following，避免逐条查）。"""
        if not viewer_hasn_id or not topic_ids:
            return set()
        rows = await db.execute(
            select(HasnFollows.target_hasn_id).where(
                HasnFollows.follower_hasn_id == viewer_hasn_id,
                HasnFollows.target_type == 'topic',
                HasnFollows.target_hasn_id.in_(topic_ids),
            )
        )
        return {r[0] for r in rows.all()}

    @staticmethod
    async def get_topic(db: AsyncSession, ident: str, *, viewer_hasn_id: str | None = None, public_only: bool = False) -> dict[str, Any]:
        t = await TopicService._get_by_ident(db, ident)
        if not t or (public_only and t.status != 'active'):
            raise errors.NotFoundError(msg='话题不存在')
        is_following = None
        if viewer_hasn_id:
            is_following = (
                await db.execute(
                    select(HasnFollows.id).where(
                        HasnFollows.follower_hasn_id == viewer_hasn_id,
                        HasnFollows.target_type == 'topic',
                        HasnFollows.target_hasn_id == t.topic_id,
                    )
                )
            ).first() is not None
        return TopicService._topic_dict(t, is_following=is_following)

    @staticmethod
    async def get_topic_feed(
        db: AsyncSession, ident: str, *, sort: str = 'latest', cursor: str | None = None, limit: int = 20, public_only: bool = False, viewer_hasn_id: str | None = None
    ) -> dict[str, Any]:
        from backend.app.hasn_community.model import HasnArticles, HasnPosts

        t = await TopicService._get_by_ident(db, ident)
        if not t:
            raise errors.NotFoundError(msg='话题不存在')
        # 可见性改走 content_visibility 唯一判据（此前 _APP_VIS 直接放行 followers、
        # 不查关注，等于 followers 内容对全体登录用户公开）。open 面按匿名判权只剩 public。
        content_viewer = None if public_only else viewer_hasn_id
        cur_id = int(cursor) if cursor and cursor.isdigit() else None

        def _join(content_model, id_col, ctype):
            j = (
                select(HasnContentTopics.id.label('ct_id'), HasnContentTopics.content_id)
                .join(content_model, id_col == HasnContentTopics.content_id)
                .where(
                    HasnContentTopics.topic_id == t.topic_id,
                    HasnContentTopics.content_type == ctype,
                    content_model.status == 'published',
                    content_model.circle_id.is_(None),
                    content_visibility_sql(content_model, viewer_hasn_id=content_viewer),
                )
            )
            if cur_id is not None:
                j = j.where(HasnContentTopics.id < cur_id)
            return j.order_by(HasnContentTopics.id.desc()).limit(limit + 1)

        post_rows = (await db.execute(_join(HasnPosts, HasnPosts.post_id, 'post'))).all()
        article_rows = (await db.execute(_join(HasnArticles, HasnArticles.article_id, 'article'))).all()
        merged = sorted(
            [(r.ct_id, 'post', r.content_id) for r in post_rows] + [(r.ct_id, 'article', r.content_id) for r in article_rows],
            key=operator.itemgetter(0), reverse=True,
        )
        has_more = len(merged) > limit
        merged = merged[:limit]
        post_cards = await fetch_post_cards(db, [c for _, ct, c in merged if ct == 'post'], viewer_hasn_id=content_viewer)
        article_cards = await fetch_article_cards(db, [c for _, ct, c in merged if ct == 'article'], viewer_hasn_id=content_viewer)
        items = []
        for _ct_id, ctype, cid in merged:
            card = (post_cards if ctype == 'post' else article_cards).get(cid)
            if card:
                items.append(card)
        next_cursor = str(merged[-1][0]) if has_more and merged else None
        # hot 排序：按互动量重排当前页（窗口内近似）
        if sort == 'hot':
            items.sort(key=lambda c: (c.get('like_count', 0) + c.get('comment_count', 0)), reverse=True)
        return {'topic': TopicService._topic_dict(t), 'items': items, 'next_cursor': next_cursor}

    @staticmethod
    async def get_trending(db: AsyncSession, *, limit: int = 10, window_days: int = 7, viewer_hasn_id: str | None = None) -> list[dict[str, Any]]:
        """真实 trending：窗口内内容增量 + 总量打分，封禁/归档不计。

        viewer_hasn_id 非空时回填每条 is_following（登录浏览者刷新后关注态正确）。
        """
        since = timezone.now() - timedelta(days=window_days)
        recent = (
            select(HasnContentTopics.topic_id, func.count().label('recent_cnt'))
            .where(HasnContentTopics.created_time >= since)
            .group_by(HasnContentTopics.topic_id)
            .subquery()
        )
        stmt = (
            select(HasnTopics, func.coalesce(recent.c.recent_cnt, 0).label('recent_cnt'))
            .outerjoin(recent, recent.c.topic_id == HasnTopics.topic_id)
            .where(HasnTopics.status == 'active')
            .order_by(
                func.coalesce(recent.c.recent_cnt, 0).desc(),
                HasnTopics.is_featured.desc(),
                HasnTopics.content_count.desc(),
                HasnTopics.last_active_time.desc().nullslast(),
            )
            .limit(limit)
        )
        out = []
        for row in (await db.execute(stmt)).all():
            d = TopicService._topic_dict(row.HasnTopics)
            d['recent_count'] = int(row.recent_cnt)
            out.append(d)
        topic_ids = [item['topic_id'] for item in out]
        if topic_ids:
            movement_rows = (
                await db.execute(
                    text(
                        """
                        SELECT topic_id,
                               count(*) FILTER (
                                   WHERE created_time >= now() - interval '24 hours'
                               ) AS current_count,
                               count(*) FILTER (
                                   WHERE created_time >= now() - interval '48 hours'
                                     AND created_time < now() - interval '24 hours'
                               ) AS previous_count
                        FROM hasn_community.hasn_content_topics
                        WHERE created_time >= now() - interval '48 hours'
                        GROUP BY topic_id
                        """
                    )
                )
            ).mappings().all()
            current_counts = {row['topic_id']: int(row['current_count']) for row in movement_rows}
            previous_counts = {row['topic_id']: int(row['previous_count']) for row in movement_rows}
            current_order = sorted(
                topic_ids,
                key=lambda topic_id: (-current_counts.get(topic_id, 0), topic_id),
            )
            previous_order = sorted(
                previous_counts,
                key=lambda topic_id: (-previous_counts[topic_id], topic_id),
            )
            current_ranks = {topic_id: index + 1 for index, topic_id in enumerate(current_order)}
            previous_ranks = {topic_id: index + 1 for index, topic_id in enumerate(previous_order)}
            for index, item in enumerate(out):
                topic_id = item['topic_id']
                item['rank'] = index + 1
                item['new_content_count'] = current_counts.get(topic_id, 0)
                item['rank_delta'] = (
                    previous_ranks[topic_id] - current_ranks[topic_id]
                    if topic_id in previous_ranks
                    else 0
                )
        if viewer_hasn_id is not None:
            followed = await TopicService._followed_topic_ids(db, viewer_hasn_id, [d['topic_id'] for d in out])
            for d in out:
                d['is_following'] = d['topic_id'] in followed
        return out

    @staticmethod
    async def search_topics(db: AsyncSession, q: str, *, limit: int = 20, viewer_hasn_id: str | None = None) -> list[dict[str, Any]]:
        q = (q or '').strip()
        if not q:
            return []
        like = f'%{q.lower()}%'
        stmt = (
            select(HasnTopics)
            .where(HasnTopics.status == 'active', or_(func.lower(HasnTopics.name).like(like), func.lower(HasnTopics.slug).like(like)))
            .order_by(func.lower(HasnTopics.name).like(f'{q.lower()}%').desc(), HasnTopics.content_count.desc())
            .limit(limit)
        )
        items = [TopicService._topic_dict(t) for t in (await db.execute(stmt)).scalars().all()]
        if viewer_hasn_id is not None:
            followed = await TopicService._followed_topic_ids(db, viewer_hasn_id, [d['topic_id'] for d in items])
            for d in items:
                d['is_following'] = d['topic_id'] in followed
        return items

    @staticmethod
    async def create_topic(db: AsyncSession, *, name: str, description: str | None, cover_url: str | None, created_by_hasn_id: str) -> dict[str, Any]:
        norm = normalize_topic_name(name)
        if not norm:
            raise errors.RequestError(msg='话题名不能为空')
        existing = (
            await db.execute(select(HasnTopics).where(func.lower(HasnTopics.name) == norm.lower(), HasnTopics.status == 'active'))
        ).scalars().first()
        if existing:
            if description and not existing.description:
                existing.description = description
            if cover_url and not existing.cover_url:
                existing.cover_url = cover_url
            await db.flush()
            return TopicService._topic_dict(existing)
        tid = await TopicService._resolve_one(db, norm, created_by=created_by_hasn_id)
        if tid is None:
            raise errors.ServerError(msg='话题创建失败')
        topic = (await db.execute(select(HasnTopics).where(HasnTopics.topic_id == tid))).scalars().first()
        if topic is None:
            raise errors.ServerError(msg='话题创建成功但权威记录不可见')
        if description:
            topic.description = description
        if cover_url:
            topic.cover_url = cover_url
        await db.flush()
        return TopicService._topic_dict(topic)

    @staticmethod
    async def follow_topic(db: AsyncSession, *, follower_hasn_id: str, topic_id: str, following: bool) -> dict[str, Any]:
        t = await TopicService._get_by_ident(db, topic_id)
        if not t:
            raise errors.NotFoundError(msg='话题不存在')
        exists = (
            await db.execute(
                select(HasnFollows).where(
                    HasnFollows.follower_hasn_id == follower_hasn_id,
                    HasnFollows.target_type == 'topic',
                    HasnFollows.target_hasn_id == t.topic_id,
                )
            )
        ).scalars().first()
        if following and not exists:
            db.add(HasnFollows(follower_hasn_id=follower_hasn_id, target_type='topic', target_hasn_id=t.topic_id))
            await db.execute(update(HasnTopics).where(HasnTopics.topic_id == t.topic_id).values(follow_count=HasnTopics.follow_count + 1))
        elif not following and exists:
            await db.delete(exists)
            await db.execute(update(HasnTopics).where(HasnTopics.topic_id == t.topic_id).values(follow_count=func.greatest(HasnTopics.follow_count - 1, 0)))
        await db.flush()
        return {'topic_id': t.topic_id, 'is_following': following}

    @staticmethod
    async def get_following(db: AsyncSession, *, follower_hasn_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(HasnTopics)
            .join(HasnFollows, and_(HasnFollows.target_hasn_id == HasnTopics.topic_id, HasnFollows.target_type == 'topic'))
            .where(HasnFollows.follower_hasn_id == follower_hasn_id, HasnTopics.status == 'active')
            .order_by(HasnFollows.created_time.desc())
        )
        items = [
            TopicService._topic_dict(t, is_following=True)
            for t in (await db.execute(stmt)).scalars().all()
        ]
        topic_ids = [item['topic_id'] for item in items]
        if not topic_ids:
            return items
        activity_rows = (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT ON (ct.topic_id)
                           ct.topic_id,
                           count(*) FILTER (
                               WHERE ct.created_time >= now() - interval '24 hours'
                           ) OVER (PARTITION BY ct.topic_id) AS new_content_count,
                           CASE
                               WHEN ct.content_type = 'article' THEN article.title
                               ELSE post.content
                           END AS latest_title
                    FROM hasn_community.hasn_content_topics AS ct
                    LEFT JOIN hasn_community.hasn_articles AS article
                      ON ct.content_type = 'article' AND article.article_id = ct.content_id
                    LEFT JOIN hasn_community.hasn_posts AS post
                      ON ct.content_type = 'post' AND post.post_id = ct.content_id
                    WHERE ct.topic_id = ANY(CAST(:topic_ids AS varchar[]))
                    ORDER BY ct.topic_id, ct.created_time DESC
                    """
                ),
                {'topic_ids': topic_ids},
            )
        ).mappings().all()
        activity = {row['topic_id']: row for row in activity_rows}
        for item in items:
            row = activity.get(item['topic_id'])
            item['new_content_count'] = int(row['new_content_count']) if row else 0
            latest_title = (row['latest_title'] or '').strip() if row else ''
            item['latest_title'] = (
                f'{latest_title[:77]}…' if len(latest_title) > 78 else latest_title
            ) or None
        return items

    # ---------- admin ----------

    @staticmethod
    async def update_topic(db: AsyncSession, *, ident: str, name: str | None = None, description: str | None = None, cover_url: str | None = None) -> dict[str, Any]:
        t = await TopicService._get_by_ident(db, ident)
        if not t:
            raise errors.NotFoundError(msg='话题不存在')
        if name:
            t.name = normalize_topic_name(name)  # 改名不改 slug（保链接稳定）
        if description is not None:
            t.description = description
        if cover_url is not None:
            t.cover_url = cover_url
        await db.flush()
        return TopicService._topic_dict(t)

    @staticmethod
    async def feature_topic(db: AsyncSession, *, ident: str, featured: bool) -> dict[str, Any]:
        t = await TopicService._get_by_ident(db, ident)
        if not t:
            raise errors.NotFoundError(msg='话题不存在')
        t.is_featured = featured
        await db.flush()
        return TopicService._topic_dict(t)

    @staticmethod
    async def block_topic(db: AsyncSession, *, ident: str) -> dict[str, Any]:
        t = await TopicService._get_by_ident(db, ident)
        if not t:
            raise errors.NotFoundError(msg='话题不存在')
        t.status = 'blocked'
        await db.flush()
        return TopicService._topic_dict(t)

    @staticmethod
    async def merge_topic(db: AsyncSession, *, ident: str, target_ident: str) -> dict[str, Any]:
        src = await TopicService._get_by_ident(db, ident)
        dst = await TopicService._get_by_ident(db, target_ident)
        if not src or not dst:
            raise errors.NotFoundError(msg='话题不存在')
        if src.topic_id == dst.topic_id:
            raise errors.RequestError(msg='不能合并到自身')
        # 重写关联到目标（避开 (topic_id,content_type,content_id) 唯一冲突）
        dst_pairs = {
            (r.content_type, r.content_id)
            for r in (await db.execute(select(HasnContentTopics.content_type, HasnContentTopics.content_id).where(HasnContentTopics.topic_id == dst.topic_id))).all()
        }
        for link in (await db.execute(select(HasnContentTopics).where(HasnContentTopics.topic_id == src.topic_id))).scalars().all():
            if (link.content_type, link.content_id) in dst_pairs:
                await db.delete(link)
            else:
                link.topic_id = dst.topic_id
        # 关注迁移
        await db.execute(
            update(HasnFollows).where(HasnFollows.target_type == 'topic', HasnFollows.target_hasn_id == src.topic_id).values(target_hasn_id=dst.topic_id)
        )
        src.status = 'merged'
        src.merged_into_topic_id = dst.topic_id
        await db.flush()
        cnt = (await db.execute(select(func.count()).select_from(HasnContentTopics).where(HasnContentTopics.topic_id == dst.topic_id))).scalar() or 0
        dst.content_count = cnt
        await db.flush()
        return {'merged_from': src.topic_id, 'merged_into': dst.topic_id, 'content_count': cnt}


topic_service = TopicService()
