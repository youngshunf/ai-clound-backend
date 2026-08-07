"""
社区服务

处理用户端社区功能：信息流、发帖、评论、点赞、关注等。
"""
from __future__ import annotations

import logging

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Text, and_, any_, cast, func, or_, select, text
from sqlalchemy.orm import aliased

from backend.app.hasn_community.model import (
    HasnArticles,
    HasnCollectionItems,
    HasnCollections,
    HasnComments,
    HasnCommunityBlocks,
    HasnFollows,
    HasnLikes,
    HasnPosts,
)
from backend.app.hasn_community.service._community_codec import (
    _assert_agent_can_read_community_resource,
    _normalize_media,
    _normalize_reference_cards,
    _present_media,
    _present_reference_cards,
    _safe_summary,
)
from backend.app.hasn_community.service.article_summary import effective_summary
from backend.app.hasn_community.service.content_visibility import (
    content_visibility_sql,
    evaluate_content_visibility,
    is_own_content,
)
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.app.hasn_core import HasnAgents, HasnHumans, identity
from backend.app.hasn.model import HasnContactRequests, HasnContacts
from backend.app.hasn_im.application.provider import get_presence_query
from backend.common.exception import errors
from backend.database.db import uuid4_str
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

logger = logging.getLogger(__name__)

_presence_query = get_presence_query()

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
        human = await identity.get_human_by_user_id(db, user_id=user_id)
        return human.hasn_id if human else None

    @staticmethod
    async def _fanout_to_followers(db: AsyncSession, *, author_hasn_id: str) -> None:
        """关注的人发帖/评论/点赞成功后 → 实时通知关注者所在的在线设备刷新社区镜像。

        本地优先架构下 daemon 侧读走 local_first_or_cloud，云端写点必须主动
        bump_owner(KIND_COMMUNITY) 才会推 WSPUSH 给对应 owner 的在线节点；否则
        关注者只能等下次冷刷新才看到新内容，达不到「后端可达即实时看到最新数据」。

        hasn_follows 不存 follower_type，故按 follower_hasn_id 命中 hasn_agents 判为
        agent（其通知落到主人 owner_id 头上），否则 follower_hasn_id 本身就是 human
        owner_hasn_id；按 owner 去重后逐个 bump，单次失败不拖垮主写操作（best-effort）。
        """
        follower_ids = (
            await db.execute(
                select(HasnFollows.follower_hasn_id).where(HasnFollows.target_hasn_id == author_hasn_id)
            )
        ).scalars().all()
        if not follower_ids:
            return

        agent_refs = await identity.refs_for_agents(db, hasn_ids=follower_ids)
        agent_owner_map = {hid: ref.owner_hasn_id for hid, ref in agent_refs.items()}

        owner_hasn_ids = {agent_owner_map.get(fid) or fid for fid in follower_ids}
        owner_hasn_ids.discard('')

        if not owner_hasn_ids:
            return

        for owner_hasn_id in owner_hasn_ids:
            await CommunityService._bump_owner_community_sync(db, owner_hasn_id)

    @staticmethod
    async def _bump_owner_community_sync(db: AsyncSession, owner_hasn_id: str) -> None:
        """单个 owner 的 sync invalidate 推送，单次失败不拖垮批量 fan-out（best-effort）。"""
        try:
            from backend.app.hasn.service import sync_invalidate_service as siv

            await siv.bump_owner(siv.KIND_COMMUNITY, db, owner_hasn_id)
        except Exception as e:
            logger.warning('[community] 关注者 fan-out sync invalidate 推送失败 (非致命): %s', e)

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
    async def _enrich_authors(db: AsyncSession, authors: list[dict[str, Any]]) -> None:
        """给一批已序列化的 author dict（仅含 hasn_id/type）就地补齐展示字段。

        二段式批量投影（替代此前"JOIN 身份表回填展示列"的写法）：
        - human：nickname → display_name，avatar；
        - agent：display_name/avatar/专家名称(profession)/主人(owner，取自 AgentRef.owner_hasn_id
          二次批量查 HumanRef)/online_status（Redis presence，断线即 offline，不读持久列避免僵尸在线）。

        社区作者可为全网任意分身，云端 hasn_agents 是权威，故能给出所有作者的专家头衔
        （不像 daemon 本地镜像仅自有分身有值）。诚实留空：查不到 → display_name 回落
        hasn_id，profession 回落 ''；human 作者不设 profession/online_status/owner 字段。
        daemon 社区镜像存整条 source_json 原样回放，故云端补的字段冷读/热读都带，无需
        daemon 侧改动。
        """
        human_ids = list({a['hasn_id'] for a in authors if a.get('type') == 'human' and a.get('hasn_id')})
        agent_ids = list({a['hasn_id'] for a in authors if a.get('type') == 'agent' and a.get('hasn_id')})
        if not human_ids and not agent_ids:
            return

        human_refs = await identity.refs_for_humans(db, hasn_ids=human_ids) if human_ids else {}
        agent_refs = await identity.refs_for_agents(db, hasn_ids=agent_ids) if agent_ids else {}
        owner_ids = list({r.owner_hasn_id for r in agent_refs.values() if r.owner_hasn_id})
        owner_refs = await identity.refs_for_humans(db, hasn_ids=owner_ids) if owner_ids else {}
        online_map = await _presence_query.get_online_map(agent_ids) if agent_ids else {}

        for a in authors:
            hid = a.get('hasn_id')
            if not isinstance(hid, str):
                continue
            if a.get('type') == 'human':
                h = human_refs.get(hid)
                a['display_name'] = (h.nickname if h and h.nickname else hid)
                a['avatar'] = h.avatar if h else None
            elif a.get('type') == 'agent':
                ag = agent_refs.get(hid)
                a['display_name'] = (ag.display_name if ag and ag.display_name else hid)
                a['avatar'] = ag.avatar if ag else None
                a['profession'] = (ag.profession or '') if ag else ''
                a['online_status'] = 'online' if online_map.get(hid) else 'offline'
                owner_hid = ag.owner_hasn_id if ag else None
                if owner_hid:
                    owner = owner_refs.get(owner_hid)
                    a['owner'] = {
                        'hasn_id': owner_hid,
                        'display_name': (owner.nickname if owner and owner.nickname else owner_hid),
                    }

    @staticmethod
    async def _build_single_author_info(
        db: AsyncSession,
        *,
        author_type: str,
        author_hasn_id: str,
        fallback_owner_hasn_id: str | None = None,
    ) -> dict[str, Any]:
        """单条 author 富化（create 写路径返回用），字段形状与 get_comments 列表的 author_info 一致。

        列表路径走 JOIN 批量构造；写路径只有一条，逐表点查。查不到对应行时 display_name
        回落 hasn_id（与列表路径的 `or comment.author_hasn_id` 同语义），不造假。
        """
        author_info: dict[str, Any] = {
            'hasn_id': author_hasn_id,
            'type': author_type,
        }
        if author_type == 'human':
            author_human = await identity.get_human(db, hasn_id=author_hasn_id)
            author_info['display_name'] = (
                author_human.nickname if author_human and author_human.nickname else author_hasn_id
            )
            author_info['avatar'] = author_human.avatar if author_human else None
            return author_info

        author_agent = await identity.get_agent(db, hasn_id=author_hasn_id)
        author_info['display_name'] = (
            author_agent.display_name if author_agent and author_agent.display_name else author_hasn_id
        )
        author_info['avatar'] = author_agent.avatar if author_agent else None
        owner_hid = (author_agent.owner_id if author_agent else None) or fallback_owner_hasn_id
        if owner_hid:
            owner_human = await identity.get_human(db, hasn_id=owner_hid)
            author_info['owner'] = {
                'hasn_id': owner_hid,
                'display_name': (owner_human.nickname if owner_human and owner_human.nickname else owner_hid),
            }
        return author_info

    @staticmethod
    def _apply_visibility_filters(
        stmt: Any,
        *,
        content_model: Any,
        viewer_hasn_id: str | None,
        exclude_unsearchable_authors: bool = False,
    ) -> Any:
        """在信息流/搜索取数语句上叠加「可被搜索」与「黑名单双向」过滤（设置真生效）。

        - exclude_unsearchable_authors（仅搜索路径传 True）：剔除作者 human 显式
          searchable=False 的内容；分身作者不受此约束（与 show_profile/allow_follow 一致，
          这些边界只治理「人」的可见性，分身可见性另有治理）。JSONB 缺省键 → 视为可被搜索。
          用 `identity.unsearchable_human_hasn_ids_subquery()` 的 `.notin_(...)` 子查询表达，
          不再 JOIN 身份表——调用方不需要 import `HasnHumans`。
        - viewer_hasn_id 非空：剔除与 viewer 互为拉黑关系（任一方向）的作者内容，
          双向都看不到对方（黑名单语义）。viewer 匿名（None）时不施加（无身份可比对）。
        """
        if exclude_unsearchable_authors:
            stmt = stmt.where(
                or_(
                    content_model.author_type != 'human',
                    content_model.author_hasn_id.notin_(identity.unsearchable_human_hasn_ids_subquery()),
                )
            )
            # 可见性判据走唯一实现（content_visibility 的 SQL 投影），不再各写一套。
            stmt = stmt.where(
                content_visibility_sql(content_model, viewer_hasn_id=viewer_hasn_id)
            )
        if viewer_hasn_id:
            blocked_by_me = select(HasnCommunityBlocks.blocked_hasn_id).where(
                HasnCommunityBlocks.blocker_hasn_id == viewer_hasn_id
            )
            blocked_me = select(HasnCommunityBlocks.blocker_hasn_id).where(
                HasnCommunityBlocks.blocked_hasn_id == viewer_hasn_id
            )
            stmt = stmt.where(
                content_model.author_hasn_id.notin_(blocked_by_me),
                content_model.author_hasn_id.notin_(blocked_me),
            )
        return stmt

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
        exclude_unsearchable_authors: bool = False,
    ) -> dict[str, Any]:
        """
        获取社区信息流

        - following：仅当前用户关注对象的内容（JOIN hasn_follows）；未登录返回空
        - recommend/articles：按 published_time 倒序
        - hot：按 like_count 倒序
        - tag：可叠加在任意 feed_type 上，仅返回 tags 数组包含该话题的内容（标签流）
        - q：关键词搜索，命中帖子正文（ILIKE，可叠加在任意 feed_type 上）
        - 可见性：published 之外还必须过 visibility 判据（public/followers/private/circle），
          私密与 followers 帖不会漏给无权 viewer，匿名只剩 public——判据与详情/翻译接口同一份。
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
            select(HasnPosts)
            .where(HasnPosts.status == 'published', HasnPosts.circle_id.is_(None))  # 圈子内容不串主 feed（95 §2.4）
            # 可见性闸：published 之外还必须过 visibility——此前这里完全不看 visibility，
            # 私密/followers 帖会漏进所有 feed_type（含 open 匿名流）。
            .where(content_visibility_sql(HasnPosts, viewer_hasn_id=viewer_hasn_id))
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
            stmt = stmt.where(any_(HasnPosts.tags) == tag)

        # 关键词搜索：帖子正文 ILIKE（值经 bind 参数化，无注入风险）
        if q and q.strip():
            stmt = stmt.where(HasnPosts.content.ilike(f'%{q.strip()}%'))

        # 「可被搜索」+「黑名单双向」过滤（设置真生效）
        stmt = CommunityService._apply_visibility_filters(
            stmt,
            content_model=HasnPosts,
            viewer_hasn_id=viewer_hasn_id,
            exclude_unsearchable_authors=exclude_unsearchable_authors,
        )

        is_hot = feed_type == 'hot'

        # keyset 游标：排序键与游标必须一致
        if cursor:
            try:
                sort_val, cur_post_id = cursor.split('|', 1)
            except ValueError:
                sort_val, cur_post_id = None, None
            if sort_val is not None:
                if is_hot:
                    cv_int = int(sort_val)
                    stmt = stmt.where(
                        or_(
                            HasnPosts.like_count < cv_int,
                            and_(HasnPosts.like_count == cv_int, HasnPosts.post_id < cur_post_id),
                        )
                    )
                else:
                    cv_time = datetime.fromisoformat(sort_val)
                    stmt = stmt.where(
                        or_(
                            HasnPosts.published_time < cv_time,
                            and_(HasnPosts.published_time == cv_time, HasnPosts.post_id < cur_post_id),
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
        posts = result.scalars().all()

        has_more = len(posts) > limit
        posts = posts[:limit]

        # 批量回填 is_liked / is_collected
        post_ids = [post.post_id for post in posts]
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'post', post_ids
        )

        items = []
        for post in posts:
            author_info = {
                'hasn_id': post.author_hasn_id,
                'type': post.author_type,
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
                'media': _present_media(post.media_json),
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
        if has_more and posts:
            last = posts[-1]
            if is_hot:
                next_cursor = f'{last.like_count}|{last.post_id}'
            elif last.published_time:
                next_cursor = f'{last.published_time.isoformat()}|{last.post_id}'

        await CommunityService._enrich_authors(db, [it['author'] for it in items if it.get('author')])
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
                exclude_unsearchable_authors=True,
            )
        return await CommunityService.get_feed(
            db, user_id=user_id, feed_type='recommend', tag=tag, q=q, cursor=cursor, limit=limit,
            exclude_unsearchable_authors=True,
        )

    @staticmethod
    async def _search_content_total(
        db: AsyncSession,
        *,
        query: str,
        content_type: str,
        viewer_hasn_id: str | None,
    ) -> int:
        """按统一搜索的可见性规则计算帖子或文章精确总数。"""
        model = HasnArticles if content_type == 'article' else HasnPosts
        stmt = (
            select(func.count())
            .select_from(model)
            .where(model.status == 'published', model.circle_id.is_(None))
        )
        keyword = f'%{query}%'
        if content_type == 'article':
            stmt = stmt.where(
                or_(
                    HasnArticles.title.ilike(keyword),
                    HasnArticles.summary.ilike(keyword),
                    HasnArticles.content.ilike(keyword),
                )
            )
        else:
            stmt = stmt.where(HasnPosts.content.ilike(keyword))
        stmt = CommunityService._apply_visibility_filters(
            stmt,
            content_model=model,
            viewer_hasn_id=viewer_hasn_id,
            exclude_unsearchable_authors=True,
        )
        return int((await db.execute(stmt)).scalar() or 0)

    @staticmethod
    async def search_group(
        db: AsyncSession,
        *,
        query: str,
        group: str,
        viewer_user_id: int | None,
        cursor: str | None = None,
        limit: int = 10,
        relation_gateway: Any | None = None,
    ) -> dict[str, Any]:
        """统一搜索一个资源组，返回独立分页、精确总数和真实关系状态。"""
        q = (query or '').strip()
        if not q:
            return {'group': group, 'items': [], 'total': 0, 'next_cursor': None}
        limit = max(1, min(int(limit), 20))
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)

        if group in {'posts', 'articles'}:
            content_type = 'article' if group == 'articles' else 'post'
            page = await CommunityService.search(
                db,
                query=q,
                content_type=content_type,
                user_id=viewer_user_id,
                cursor=cursor,
                limit=limit,
            )
            total = await CommunityService._search_content_total(
                db,
                query=q,
                content_type=content_type,
                viewer_hasn_id=viewer_hasn_id,
            )
            return {
                'group': group,
                'items': page['items'],
                'total': total,
                'next_cursor': page['next_cursor'],
            }

        if group not in {'humans', 'agents'}:
            raise errors.RequestError(msg='不支持的搜索分组')

        from backend.app.hasn_im.application.provider import get_relation_gateway

        resolved_relation_gateway = relation_gateway or get_relation_gateway()
        offset = max(0, int(cursor or 0))
        keyword = f'%{q}%'
        blocked_by_me = select(HasnCommunityBlocks.blocked_hasn_id).where(
            HasnCommunityBlocks.blocker_hasn_id == viewer_hasn_id
        )
        blocked_me = select(HasnCommunityBlocks.blocker_hasn_id).where(
            HasnCommunityBlocks.blocked_hasn_id == viewer_hasn_id
        )

        if group == 'humans':
            conditions: list[Any] = [
                HasnHumans.status == 'active',
                CommunityService._human_searchable_cond(),
                HasnHumans.community_settings['show_profile'].astext.is_distinct_from('false'),
                or_(
                    HasnHumans.nickname.ilike(keyword),
                    HasnHumans.star_id.ilike(keyword),
                    HasnHumans.bio.ilike(keyword),
                ),
            ]
            if viewer_hasn_id:
                conditions.extend(
                    [
                        HasnHumans.hasn_id != viewer_hasn_id,
                        HasnHumans.hasn_id.notin_(blocked_by_me),
                        HasnHumans.hasn_id.notin_(blocked_me),
                    ]
                )
            total = int(
                (
                    await db.execute(
                        select(func.count()).select_from(HasnHumans).where(*conditions)
                    )
                ).scalar()
                or 0
            )
            rows = (
                await db.execute(
                    select(HasnHumans)
                    .where(*conditions)
                    .order_by(
                        func.lower(HasnHumans.nickname)
                        .like(f'{q.lower()}%')
                        .desc(),
                        HasnHumans.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars().all()
            items = []
            for human in rows:
                item = CommunityService._peer_human_item(
                    human,
                    match_reason='社区搜索',
                    rank=0,
                )
                item.pop('_rank', None)
                item['friendship_status'] = await CommunityService._resolve_friendship_status(
                    db,
                    viewer_hasn_id=viewer_hasn_id,
                    target_hasn_id=human.hasn_id,
                )
                items.append(item)
            next_cursor = str(offset + len(items)) if offset + len(items) < total else None
            return {
                'group': group,
                'items': items,
                'total': total,
                'next_cursor': next_cursor,
            }

        owner_human = aliased(HasnHumans)
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
        conditions = [
            HasnAgents.status == 'active',
            HasnAgents.deleted_at.is_(None),
            or_(
                HasnAgents.display_name.ilike(keyword),
                HasnAgents.agent_name.ilike(keyword),
                HasnAgents.bio.ilike(keyword),
                HasnAgents.description.ilike(keyword),
                HasnAgents.profession.ilike(keyword),
                cast(HasnAgents.capability_summary_json, Text).ilike(keyword),
            ),
        ]
        if viewer_hasn_id:
            conditions.extend(
                [
                    HasnAgents.owner_id != viewer_hasn_id,
                    HasnAgents.hasn_id.notin_(blocked_by_me),
                    HasnAgents.hasn_id.notin_(blocked_me),
                    HasnAgents.owner_id.notin_(blocked_by_me),
                    HasnAgents.owner_id.notin_(blocked_me),
                ]
            )
        agent_rows = list(
            (
                await db.execute(
                    select(
                        HasnAgents,
                        owner_human.hasn_id.label('owner_hasn_id'),
                        owner_human.nickname.label('owner_nickname'),
                        follower_sq.label('follower_count'),
                    )
                    .join(owner_human, HasnAgents.owner_id == owner_human.hasn_id)
                    .where(*conditions)
                    .order_by(
                        func.lower(HasnAgents.display_name)
                        .like(f'{q.lower()}%')
                        .desc(),
                        HasnAgents.created_time.desc(),
                    )
                )
            ).all()
        )
        enabled = await resolved_relation_gateway.filter_socially_enabled_agents(
            agent_hasn_ids=[row.HasnAgents.hasn_id for row in agent_rows]
        )
        visible_rows = [row for row in agent_rows if row.HasnAgents.hasn_id in enabled]
        total = len(visible_rows)
        page_rows = visible_rows[offset : offset + limit]
        online_map = await _presence_query.get_online_map(
            [row.HasnAgents.hasn_id for row in page_rows]
        )
        items = []
        for row in page_rows:
            agent = row.HasnAgents
            item = CommunityService._peer_agent_item(
                agent,
                owner_hasn_id=row.owner_hasn_id,
                owner_name=row.owner_nickname,
                follower_count=row.follower_count,
                match_reason='社区搜索',
                rank=0,
            )
            item.pop('_rank', None)
            item['online_status'] = (
                'online' if online_map.get(agent.hasn_id) else 'offline'
            )
            item['friendship_status'] = await CommunityService._resolve_friendship_status(
                db,
                viewer_hasn_id=viewer_hasn_id,
                target_hasn_id=agent.hasn_id,
                target_owner_hasn_id=agent.owner_id,
            )
            items.append(item)
        next_cursor = str(offset + len(items)) if offset + len(items) < total else None
        return {
            'group': group,
            'items': items,
            'total': total,
            'next_cursor': next_cursor,
        }

    @staticmethod
    async def _get_articles_feed(
        db: AsyncSession,
        *,
        viewer_hasn_id: str | None,
        tag: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
        exclude_unsearchable_authors: bool = False,
    ) -> dict[str, Any]:
        """
        文章信息流（hasn_articles）

        - 仅 status='published' 且过 visibility 判据（public/followers/private/circle，
          与详情/翻译接口同一份；匿名只剩 public）。此前只排 private，followers 文与
          未知取值（如遗留 workspace_group）都会漏给任何人。
        - 按 published_time 倒序，keyset 游标 "{published_time}|{article_id}"
        - tag：tags 数组包含该话题；q：标题/摘要/正文 ILIKE
        - is_liked/is_collected：按 target_type='article' 批量回填
        - item 携带 content_type='article'，便于前端与帖子区分渲染与跳转
        """
        stmt = (
            select(HasnArticles)
            .where(
                HasnArticles.status == 'published',
                HasnArticles.circle_id.is_(None),  # 圈子内容不串主 feed（95 §2.4）
                content_visibility_sql(HasnArticles, viewer_hasn_id=viewer_hasn_id),
            )
        )

        # 标签流
        if tag:
            stmt = stmt.where(any_(HasnArticles.tags) == tag)

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

        # 「可被搜索」+「黑名单双向」过滤（设置真生效）
        stmt = CommunityService._apply_visibility_filters(
            stmt,
            content_model=HasnArticles,
            viewer_hasn_id=viewer_hasn_id,
            exclude_unsearchable_authors=exclude_unsearchable_authors,
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
        articles = result.scalars().all()

        has_more = len(articles) > limit
        articles = articles[:limit]

        article_ids = [article.article_id for article in articles]
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'article', article_ids
        )

        items = []
        for article in articles:
            author_info = {
                'hasn_id': article.author_hasn_id,
                'type': article.author_type,
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
        if has_more and articles:
            last = articles[-1]
            if last.published_time:
                next_cursor = f'{last.published_time.isoformat()}|{last.article_id}'

        await CommunityService._enrich_authors(db, [it['author'] for it in items if it.get('author')])
        return {
            'items': items,
            'next_cursor': next_cursor,
        }

    @staticmethod
    async def _fetch_post_items(
        db: AsyncSession, viewer_hasn_id: str | None, post_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """按 id 取一批帖子并富化为 feed 同形 item，返回 {post_id: item}（已下架的略过）。"""
        if not post_ids:
            return {}
        stmt = select(HasnPosts).where(HasnPosts.post_id.in_(post_ids), HasnPosts.status == 'published')
        posts = (await db.execute(stmt)).scalars().all()
        _, collected_ids = await CommunityService._batch_reactions(db, viewer_hasn_id, 'post', post_ids)
        result: dict[str, dict[str, Any]] = {}
        for post in posts:
            author_info: dict[str, Any] = {'hasn_id': post.author_hasn_id, 'type': post.author_type}
            result[post.post_id] = {
                'content_type': 'post',
                'post_id': post.post_id,
                'origin_workspace': {
                    'kind': post.origin_workspace_kind,
                    'id': post.origin_workspace_id,
                },
                'author': author_info,
                'content': post.content,
                'tags': post.tags or [],
                'media': _present_media(post.media_json),
                'reference_cards': _present_reference_cards(post.reference_cards, viewer_hasn_id),
                'like_count': post.like_count,
                'comment_count': post.comment_count,
                'published_time': post.published_time.isoformat() if post.published_time else None,
                'is_liked': True,
                'is_collected': post.post_id in collected_ids,
            }
        return result

    @staticmethod
    async def _fetch_article_items(
        db: AsyncSession, viewer_hasn_id: str | None, article_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """按 id 取一批文章并富化为 feed 同形 item，返回 {article_id: item}（已下架的略过）。"""
        if not article_ids:
            return {}
        stmt = select(HasnArticles).where(HasnArticles.article_id.in_(article_ids), HasnArticles.status == 'published')
        articles = (await db.execute(stmt)).scalars().all()
        _, collected_ids = await CommunityService._batch_reactions(db, viewer_hasn_id, 'article', article_ids)
        result: dict[str, dict[str, Any]] = {}
        for article in articles:
            author_info: dict[str, Any] = {'hasn_id': article.author_hasn_id, 'type': article.author_type}
            result[article.article_id] = {
                'content_type': 'article',
                'article_id': article.article_id,
                'author': author_info,
                'title': article.title,
                'summary': effective_summary(article.summary, article.content),
                'cover_url': article.cover_url,
                'tags': article.tags or [],
                'reference_cards': _present_reference_cards(article.reference_cards, viewer_hasn_id),
                'like_count': article.like_count,
                'comment_count': article.comment_count,
                'read_time_min': article.read_time_min,
                'published_time': article.published_time.isoformat() if article.published_time else None,
                'is_liked': True,
                'is_collected': article.article_id in collected_ids,
            }
        return result

    @staticmethod
    async def get_my_liked_items(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """当前用户点赞过的内容（帖子 + 文章），按点赞时间倒序。

        - 读 hasn_likes（target_type in post/article），keyset 游标按 like.id 倒序（cursor=末条 like.id）
        - 富化为与 feed 同形的 content item（is_liked 恒 True；is_collected 批量回填）
        - 目标已删除/下架则 JOIN 不到自动略过（零 fake，不返回幽灵条目）
        """
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, user_id)
        if not viewer_hasn_id:
            return {'items': [], 'next_cursor': None}

        like_stmt = select(HasnLikes.id, HasnLikes.target_type, HasnLikes.target_id).where(
            HasnLikes.user_hasn_id == viewer_hasn_id,
            HasnLikes.target_type.in_(['post', 'article']),
        )
        if cursor:
            try:
                like_stmt = like_stmt.where(HasnLikes.id < int(cursor))
            except ValueError:
                pass
        like_stmt = like_stmt.order_by(HasnLikes.id.desc()).limit(limit + 1)
        like_rows = (await db.execute(like_stmt)).all()

        has_more = len(like_rows) > limit
        like_rows = like_rows[:limit]

        post_ids = [r.target_id for r in like_rows if r.target_type == 'post']
        article_ids = [r.target_id for r in like_rows if r.target_type == 'article']
        post_map = await CommunityService._fetch_post_items(db, viewer_hasn_id, post_ids)
        article_map = await CommunityService._fetch_article_items(db, viewer_hasn_id, article_ids)

        items: list[dict[str, Any]] = []
        for r in like_rows:
            item = post_map.get(r.target_id) if r.target_type == 'post' else article_map.get(r.target_id)
            if item is not None:
                items.append(item)

        next_cursor = str(like_rows[-1].id) if has_more and like_rows else None
        await CommunityService._enrich_authors(db, [it['author'] for it in items if it.get('author')])
        return {'items': items, 'next_cursor': next_cursor}

    @staticmethod
    async def _enrich_relations(
        db: AsyncSession, entries: list[tuple[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """把一批 (hasn_id, type) 富化为身份卡片，返回 {hasn_id: card}。

        - agent → display_name/avatar/bio/profession(专家名称)/owner(主人 hasn_id+昵称+头像)
        - human → nickname/avatar/bio/region(地区，由 sys_user province/city/district 拼接)
        """
        from backend.app.admin.model.user import User

        human_ids = [hid for hid, t in entries if t == 'human']
        agent_ids = [hid for hid, t in entries if t == 'agent']
        result: dict[str, dict[str, Any]] = {}

        if human_ids:
            human_refs = await identity.refs_for_humans(db, hasn_ids=human_ids)
            # region（省市区）来自 admin.User，不属于身份投影字段；按 HumanRef.user_id
            # 二次批量查询，不需要 JOIN 身份表本身（也不需要 import HasnHumans）。
            region_map: dict[int, tuple[str | None, str | None, str | None]] = {}
            user_ids = [h.user_id for h in human_refs.values()]
            if user_ids:
                user_rows = (
                    await db.execute(
                        select(User.id, User.province, User.city, User.district).where(User.id.in_(user_ids))
                    )
                ).all()
                region_map = {u.id: (u.province, u.city, u.district) for u in user_rows}
            for hid, h in human_refs.items():
                province, city, district = region_map.get(h.user_id, (None, None, None))
                region = ' '.join(p for p in (province, city, district) if p)
                result[hid] = {
                    'hasn_id': hid,
                    'type': 'human',
                    'display_name': h.nickname or hid,
                    'avatar': h.avatar or '',
                    'bio': h.bio or '',
                    'region': region,
                }

        if agent_ids:
            agent_refs = await identity.refs_for_agents(db, hasn_ids=agent_ids)
            owner_ids = [a.owner_hasn_id for a in agent_refs.values() if a.owner_hasn_id]
            owner_refs = await identity.refs_for_humans(db, hasn_ids=owner_ids) if owner_ids else {}
            owner_map: dict[str, dict[str, Any]] = {
                o_hid: {
                    'hasn_id': o_hid,
                    'display_name': o.nickname or o_hid,
                    'avatar': o.avatar or '',
                }
                for o_hid, o in owner_refs.items()
            }
            for hid, a in agent_refs.items():
                result[hid] = {
                    'hasn_id': hid,
                    'type': 'agent',
                    'display_name': a.display_name or hid,
                    'avatar': a.avatar or '',
                    'bio': a.bio or '',
                    'profession': a.profession or '',
                    'owner': owner_map.get(a.owner_hasn_id),
                }
        return result

    @staticmethod
    async def list_following(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """当前用户关注的对象列表（human/agent），按关注时间倒序，附富化身份卡片。"""
        viewer = await CommunityService._resolve_human_hasn_id(db, user_id)
        if not viewer:
            return {'items': [], 'next_cursor': None}

        stmt = select(
            HasnFollows.id, HasnFollows.target_type, HasnFollows.target_hasn_id, HasnFollows.created_time
        ).where(
            HasnFollows.follower_hasn_id == viewer,
            HasnFollows.target_type.in_(['human', 'agent']),
        )
        if cursor:
            try:
                stmt = stmt.where(HasnFollows.id < int(cursor))
            except ValueError:
                pass
        stmt = stmt.order_by(HasnFollows.id.desc()).limit(limit + 1)
        rows = (await db.execute(stmt)).all()

        has_more = len(rows) > limit
        rows = rows[:limit]

        entries = [(r.target_hasn_id, r.target_type) for r in rows]
        enriched = await CommunityService._enrich_relations(db, entries)

        items: list[dict[str, Any]] = []
        for r in rows:
            base = enriched.get(r.target_hasn_id)
            if base is None:
                continue
            item = dict(base)
            item['is_following'] = True
            item['followed_time'] = r.created_time.isoformat() if r.created_time else None
            items.append(item)

        next_cursor = str(rows[-1].id) if has_more and rows else None
        return {'items': items, 'next_cursor': next_cursor}

    @staticmethod
    async def list_followers(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """关注当前用户的对象列表（粉丝），按关注时间倒序，附富化身份卡片与回关态。

        hasn_follows 不存 follower_type，故按 follower_hasn_id 命中 hasn_agents 判为 agent，否则 human。
        """
        viewer = await CommunityService._resolve_human_hasn_id(db, user_id)
        if not viewer:
            return {'items': [], 'next_cursor': None}

        stmt = select(HasnFollows.id, HasnFollows.follower_hasn_id, HasnFollows.created_time).where(
            HasnFollows.target_hasn_id == viewer,
        )
        if cursor:
            try:
                stmt = stmt.where(HasnFollows.id < int(cursor))
            except ValueError:
                pass
        stmt = stmt.order_by(HasnFollows.id.desc()).limit(limit + 1)
        rows = (await db.execute(stmt)).all()

        has_more = len(rows) > limit
        rows = rows[:limit]

        follower_ids = [r.follower_hasn_id for r in rows]
        agent_id_set: set[str] = set()
        following_back: set[str] = set()
        if follower_ids:
            agent_id_set = set((await identity.refs_for_agents(db, hasn_ids=follower_ids)).keys())
            following_back = set(
                (
                    await db.execute(
                        select(HasnFollows.target_hasn_id).where(
                            HasnFollows.follower_hasn_id == viewer,
                            HasnFollows.target_hasn_id.in_(follower_ids),
                        )
                    )
                ).scalars().all()
            )

        entries = [(fid, 'agent' if fid in agent_id_set else 'human') for fid in follower_ids]
        enriched = await CommunityService._enrich_relations(db, entries)

        items: list[dict[str, Any]] = []
        for r in rows:
            base = enriched.get(r.follower_hasn_id)
            if base is None:
                continue
            item = dict(base)
            item['is_follower'] = True
            item['is_following'] = r.follower_hasn_id in following_back
            item['followed_time'] = r.created_time.isoformat() if r.created_time else None
            items.append(item)

        next_cursor = str(rows[-1].id) if has_more and rows else None
        return {'items': items, 'next_cursor': next_cursor}

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
        stmt = (
            select(HasnArticles)
            .where(
                HasnArticles.status == 'published',
                HasnArticles.visibility != 'private',
                HasnArticles.circle_id.is_(None),  # 圈子内容不串主 feed（95 §2.4）
            )
            .order_by(HasnArticles.published_time.desc(), HasnArticles.article_id.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        articles = result.scalars().all()

        items: list[dict[str, Any]] = []
        for article in articles:
            items.append({
                'article_id': article.article_id,
                'title': article.title,
                'summary': effective_summary(article.summary, article.content),
                'cover_url': article.cover_url,
                'author': {
                    'hasn_id': article.author_hasn_id,
                    'type': article.author_type,
                },
                'like_count': article.like_count,
                'comment_count': article.comment_count,
                'read_time_min': article.read_time_min,
                'published_time': article.published_time.isoformat() if article.published_time else None,
            })
        await CommunityService._enrich_authors(db, [it['author'] for it in items])

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
        comment_policy: str | None = None,
        reference_cards: list[dict[str, Any]] | None = None,
        media: list[dict[str, Any]] | None = None,
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

        # 评论策略：未显式指定 → 回落主人默认（default_comment_policy 设置真生效）
        if comment_policy is None:
            comment_policy = await community_settings_service.get_default_comment_policy(db, hasn_id=owner_hasn_id)

        # TODO: 获取当前 active workspace
        workspace_kind = 'personal'
        workspace_id = str(user_id)

        status = 'published'
        circle = None
        if circle_id:
            circle, needs_review = await circle_service.assert_can_post(
                db,
                circle_id=circle_id,
                actor_hasn_id=author_hasn_id,
            )
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
            media_json=_normalize_media(media),
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
        elif circle and status == 'pending_review':
            await circle_service.notify_pending_content(
                db,
                circle=circle,
                author_hasn_id=author_hasn_id,
                content_type='post',
                content_id=post_id,
            )

        # 已发布（非待审）才对关注者可见，实时通知其在线设备刷新社区镜像
        if status == 'published':
            await CommunityService._fanout_to_followers(db, author_hasn_id=author_hasn_id)

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
        # tab 复用同款卡片渲染——否则缺 author 会整页白屏崩溃。作者展示字段统一交给末尾
        # 的 `_enrich_authors` 二段式批量投影回填，取数阶段不再 JOIN 身份表。
        #
        # 草稿箱同时收纳「帖子」与「文章」（含 Agent 自主发文的 pending_review 待审核内容）：
        # 历史实现只查 HasnPosts，导致 Agent 文章有通知却进不了草稿箱、无法审核（2026-06-04 修复）。
        from backend.app.hasn_community.model.hasn_articles import HasnArticles

        # ---- 帖子草稿 ----
        post_stmt = (
            select(HasnPosts)
            .where(
                HasnPosts.owner_hasn_id == hasn_id,
                HasnPosts.status.in_(['draft', 'pending_review']),
            )
            .order_by(HasnPosts.created_time.desc())
            .limit(limit)
        )
        posts = (await db.execute(post_stmt)).scalars().all()

        # ---- 文章草稿（含 Agent pending_review）----
        article_stmt = (
            select(HasnArticles)
            .where(
                HasnArticles.owner_hasn_id == hasn_id,
                HasnArticles.status.in_(['draft', 'pending_review']),
            )
            .order_by(HasnArticles.created_time.desc())
            .limit(limit)
        )
        articles = (await db.execute(article_stmt)).scalars().all()

        # ---- 合并：按 created_time 倒序统一排序，cap 到 limit ----
        merged: list[tuple[Any, dict[str, Any]]] = []

        for post in posts:
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
                'author': {'hasn_id': post.author_hasn_id, 'type': post.author_type},
                'content': post.content,
                'tags': post.tags or [],
                'media': _present_media(post.media_json),
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

        for article in articles:
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
                'author': {'hasn_id': article.author_hasn_id, 'type': article.author_type},
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

        floor = _dt(1970, 1, 1, tzinfo=_dt_timezone.utc)
        merged.sort(key=lambda pair: pair[0] or floor, reverse=True)
        items = [item for _, item in merged[:limit]]

        await CommunityService._enrich_authors(db, [it['author'] for it in items if it.get('author')])
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
        from backend.app.hasn_community.service.doc_service import doc_service

        await doc_service.notify_article_updated(
            db,
            article_id=article.article_id,
            actor_hasn_id=article.author_hasn_id,
        )

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
        stmt = select(HasnPosts).where(HasnPosts.post_id == post_id)
        post = (await db.execute(stmt)).scalars().first()

        if not post:
            raise errors.NotFoundError(msg='帖子不存在')

        # 当前 viewer 的点赞/收藏态
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, user_id)

        # 状态闸：草稿/待审/退回仅作者本人或（分身帖的）主人可看；deleted 一律 404。
        # （分身发帖默认 pending_review，主人需能从卡片/草稿箱点进详情审核——故 owner_hasn_id 命中即放行。）
        if post.status == 'deleted' or (
            post.status != 'published'
            and not is_own_content(
                viewer_hasn_id=viewer_hasn_id,
                author_hasn_id=post.author_hasn_id,
                owner_hasn_id=post.owner_hasn_id,
            )
        ):
            raise errors.NotFoundError(msg='帖子不存在')

        # 可见性闸：published 帖也必须过 visibility（public/followers/private/circle）。
        # 此前这里只判 status，任何人拿到 post_id 就能读别人的私密帖全文，未登录走
        # /api/v1/community/open/posts/{post_id} 同样能读——真实越权读取漏洞。
        # 判据与翻译接口共用 content_visibility 唯一实现，不再各写一套。
        #
        # **一律回 404「帖子不存在」，不回 403**：本接口是主要的存在性探测面（尤其匿名 open 路由），
        # 403 等于承认「这条帖子存在、只是你没权限」，把整张 hasn_posts 表变成可枚举的存在性预言机。
        # 而且本接口对草稿/已删已经用 404「不泄露存在性」，私密帖回 403 会自相矛盾。
        # 翻译接口保持 403 + 具体文案：它只有在详情已放行后才够得着，不额外泄露存在性。
        if post.status == 'published':
            decision = await evaluate_content_visibility(
                db,
                visibility=post.visibility,
                author_hasn_id=post.author_hasn_id,
                owner_hasn_id=post.owner_hasn_id,
                circle_id=post.circle_id,
                viewer_hasn_id=viewer_hasn_id,
            )
            if not decision.allowed:
                raise errors.NotFoundError(msg='帖子不存在')

        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, viewer_hasn_id, 'post', [post.post_id]
        )

        # 构建 author 信息（展示字段交给下方 `_enrich_authors` 二段式批量投影回填）
        author_info: dict[str, Any] = {
            'hasn_id': post.author_hasn_id,
            'type': post.author_type,
        }

        # 评论权限预判（doc-12 B-3 姊妹刀）：详情接口直接把 comment_policy 真实生效的判定结果
        # 带给 webui，驱动「关闭评论时不显示输入框、改提示原因」，而不是让用户点提交才被拒。
        can_comment, comment_disabled_reason = await CommunityService._check_can_comment(
            db, policy=post.comment_policy, author_hasn_id=post.author_hasn_id, commenter_hasn_id=viewer_hasn_id
        )

        await CommunityService._enrich_authors(db, [author_info])
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
            'media': _present_media(post.media_json),
            'reference_cards': _present_reference_cards(
                post.reference_cards, viewer_hasn_id
            ),
            'like_count': post.like_count,
            'comment_count': post.comment_count,
            'collect_count': post.collect_count,
            'comment_policy': post.comment_policy,
            'can_comment': can_comment,
            'comment_disabled_reason': comment_disabled_reason,
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
        stmt = select(HasnComments).where(
            HasnComments.target_type == target_type,
            HasnComments.target_id == target_id,
            HasnComments.status == 'visible',
        )

        if sort not in {'time_asc', 'time_desc', 'hot'}:
            raise errors.RequestError(msg='未知评论排序方式')

        # 游标字段和排序字段保持完全一致，最后用 comment_id 打破并列。
        if cursor:
            try:
                if sort == 'hot':
                    like_count_raw, created_time_raw, comment_id = cursor.split('|', 2)
                    cursor_like_count = int(like_count_raw)
                    cursor_created_time = datetime.fromisoformat(created_time_raw)
                    if not comment_id:
                        raise ValueError
                    stmt = stmt.where(
                        or_(
                            HasnComments.like_count < cursor_like_count,
                            and_(
                                HasnComments.like_count == cursor_like_count,
                                or_(
                                    HasnComments.created_time < cursor_created_time,
                                    and_(
                                        HasnComments.created_time == cursor_created_time,
                                        HasnComments.comment_id < comment_id,
                                    ),
                                ),
                            ),
                        )
                    )
                else:
                    created_time_raw, comment_id = cursor.split('|', 1)
                    cursor_created_time = datetime.fromisoformat(created_time_raw)
                    if not comment_id:
                        raise ValueError
                    if sort == 'time_asc':
                        stmt = stmt.where(
                            or_(
                                HasnComments.created_time > cursor_created_time,
                                and_(
                                    HasnComments.created_time == cursor_created_time,
                                    HasnComments.comment_id > comment_id,
                                ),
                            )
                        )
                    else:
                        stmt = stmt.where(
                            or_(
                                HasnComments.created_time < cursor_created_time,
                                and_(
                                    HasnComments.created_time == cursor_created_time,
                                    HasnComments.comment_id < comment_id,
                                ),
                            )
                        )
            except (TypeError, ValueError) as exc:
                raise errors.RequestError(msg='评论分页游标无效') from exc

        if sort == 'time_asc':
            stmt = stmt.order_by(
                HasnComments.created_time.asc(),
                HasnComments.comment_id.asc(),
            )
        elif sort == 'time_desc':
            stmt = stmt.order_by(
                HasnComments.created_time.desc(),
                HasnComments.comment_id.desc(),
            )
        else:
            stmt = stmt.order_by(
                HasnComments.like_count.desc(),
                HasnComments.created_time.desc(),
                HasnComments.comment_id.desc(),
            )

        stmt = stmt.limit(limit + 1)
        result = await db.execute(stmt)
        comments = result.scalars().all()
        has_more = len(comments) > limit
        comments = comments[:limit]

        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, user_id)
        liked_comment_ids: set[str] = set()
        if viewer_hasn_id and comments:
            comment_ids = [comment.comment_id for comment in comments]
            liked_comment_ids = set(
                (
                    await db.execute(
                        select(HasnLikes.target_id).where(
                            HasnLikes.user_hasn_id == viewer_hasn_id,
                            HasnLikes.target_type == 'comment',
                            HasnLikes.target_id.in_(comment_ids),
                        )
                    )
                ).scalars().all()
            )

        items: list[dict[str, Any]] = []
        for comment in comments:
            # 构建 author 信息（展示字段交给下方 `_enrich_authors` 二段式批量投影回填）
            author_info = {
                'hasn_id': comment.author_hasn_id,
                'type': comment.author_type,
            }

            items.append({
                'comment_id': comment.comment_id,
                'author': author_info,
                'content': comment.content,
                'parent_id': comment.parent_id,
                'like_count': comment.like_count,
                'is_liked': comment.comment_id in liked_comment_ids,
                'created_time': comment.created_time.isoformat() if comment.created_time else None,
            })

        await CommunityService._enrich_authors(db, [it['author'] for it in items if it.get('author')])
        next_cursor = None
        if has_more and comments:
            last = comments[-1]
            if sort == 'hot':
                next_cursor = f'{last.like_count}|{last.created_time.isoformat()}|{last.comment_id}'
            else:
                next_cursor = f'{last.created_time.isoformat()}|{last.comment_id}'

        return {'items': items, 'next_cursor': next_cursor}

    @staticmethod
    async def _check_can_comment(
        db: AsyncSession,
        *,
        policy: str | None,
        author_hasn_id: str | None,
        commenter_hasn_id: str | None,
    ) -> tuple[bool, str | None]:
        """按内容的 comment_policy 判定评论权限，返回 (是否允许, 不允许时的原因)。

        - 作者本人评论自己内容：恒允许（不被自己的策略锁死）；
        - closed：拒绝（仅作者可评论）；
        - followers：要求评论者已关注作者（HasnFollows）；
        - all / 缺省：放行。

        供 `_assert_can_comment`（发评论时的写路径硬闸）与详情接口（`get_post`/
        `get_article`，读路径预判断 `can_comment`，驱动 webui 隐藏输入框+提示原因）
        复用同一套判定，避免两处逻辑各写一份漂移。
        """
        if not author_hasn_id or (commenter_hasn_id and commenter_hasn_id == author_hasn_id):
            return True, None
        if policy == 'closed':
            return False, '作者已关闭该内容的评论'
        if policy == 'followers':
            if not commenter_hasn_id:
                return False, '该内容仅允许作者的关注者评论'
            follows = (
                await db.execute(
                    select(HasnFollows.follower_hasn_id)
                    .where(
                        HasnFollows.follower_hasn_id == commenter_hasn_id,
                        HasnFollows.target_hasn_id == author_hasn_id,
                    )
                    .limit(1)
                )
            ).first()
            if follows is None:
                return False, '该内容仅允许作者的关注者评论'
        return True, None

    @staticmethod
    async def _assert_can_comment(
        db: AsyncSession,
        *,
        policy: str | None,
        author_hasn_id: str | None,
        commenter_hasn_id: str,
    ) -> None:
        """按内容的 comment_policy 把关评论权限（comment_policy 设置真生效），不允许则抛错。"""
        allowed, reason = await CommunityService._check_can_comment(
            db, policy=policy, author_hasn_id=author_hasn_id, commenter_hasn_id=commenter_hasn_id
        )
        if not allowed:
            raise errors.RequestError(msg=reason or '当前内容不允许评论')

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

        # 先取目标内容作者 + 评论策略（拉黑闸/评论策略闸 + 后续计数/通知共用一次取数），取不到作者则不拦（目标可能已删）。
        target_post = None
        target_article = None
        target_author_hasn_id = None
        target_author_type = None
        target_owner_hasn_id = None
        target_comment_policy = None
        if target_type == 'post':
            target_post = (
                await db.execute(select(HasnPosts).where(HasnPosts.post_id == target_id))
            ).scalars().first()
            if target_post:
                target_author_hasn_id = target_post.author_hasn_id
                target_author_type = target_post.author_type
                target_owner_hasn_id = target_post.owner_hasn_id
                target_comment_policy = target_post.comment_policy
        elif target_type == 'article':
            target_article = (
                await db.execute(select(HasnArticles).where(HasnArticles.article_id == target_id))
            ).scalars().first()
            if target_article:
                target_author_hasn_id = target_article.author_hasn_id
                target_author_type = target_article.author_type
                target_owner_hasn_id = target_article.owner_hasn_id
                target_comment_policy = target_article.comment_policy

        # 拉黑双向闸：评论者与内容作者互为拉黑关系（任一方向）→ 拒绝评论。
        if target_author_hasn_id and await community_settings_service.is_blocked_between(
            db, a_hasn_id=hasn_id, b_hasn_id=target_author_hasn_id
        ):
            raise errors.RequestError(msg='你与作者存在拉黑关系，无法评论')

        # 评论策略闸（comment_policy 设置真生效）：closed 仅作者可评论；followers 仅作者关注者可评论。
        await CommunityService._assert_can_comment(
            db,
            policy=target_comment_policy,
            author_hasn_id=target_author_hasn_id,
            commenter_hasn_id=hasn_id,
        )

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

        # 更新目标的评论计数（仅可见评论计数；作者信息已在前置取数捕获，复用同一 ORM 对象不再二次查询）
        if is_visible:
            if target_post is not None:
                target_post.comment_count += 1
            elif target_article is not None:
                target_article.comment_count += 1

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

        # 已可见评论才对外，实时通知评论者（动作发起人）的关注者刷新社区镜像
        if is_visible:
            await CommunityService._fanout_to_followers(db, author_hasn_id=hasn_id)

        # 富化返回完整评论（形状对齐 get_comments 单条 + status）：
        # daemon 本地优先把本响应经 apply_authoritative_comment 原样写入镜像 source_json，
        # 若缺 author/content，客户端评论列表渲染 comment.author.hasn_id 直接崩。
        author_info = await CommunityService._build_single_author_info(
            db, author_type=author_type, author_hasn_id=hasn_id, fallback_owner_hasn_id=owner_hasn_id
        )
        await CommunityService._enrich_authors(db, [author_info])

        return {
            'comment_id': comment_id,
            'author': author_info,
            'content': content,
            'parent_id': parent_id,
            'like_count': 0,
            'is_liked': False,
            'created_time': comment.created_time.isoformat() if comment.created_time else None,
            'status': status,
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
        notification_link = None
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
                notification_link = (
                    f'/community/articles/{comment.target_id}#comment-{comment.comment_id}'
                    if comment.target_type == 'article'
                    else f'/community/posts/{comment.target_id}#comment-{comment.comment_id}'
                )

        await db.flush()

        # 触发通知：内容作者（Agent 内容额外 relay 给主人；自赞跳过）
        if target_author_hasn_id and target_type in ('post', 'article', 'comment'):
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
                link=notification_link,
            )

        # 实时通知点赞者（动作发起人）的关注者刷新社区镜像
        await CommunityService._fanout_to_followers(db, author_hasn_id=hasn_id)

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

        # 拉黑双向闸：与对方互为拉黑关系（任一方向）→ 拒绝建立关注。
        if await community_settings_service.is_blocked_between(
            db, a_hasn_id=hasn_id, b_hasn_id=target_hasn_id
        ):
            raise errors.RequestError(msg='你与对方存在拉黑关系，无法关注')

        # 「允许被关注」边界：human 关闭后拒绝新增关注（已关注者取关不受影响）。
        # 仅约束 human 主体；agent 的可关注性另有治理，这里不拦。
        if target_type == 'human' and not await community_settings_service.get_profile_flag(
            db, hasn_id=target_hasn_id, key='allow_follow'
        ):
            raise errors.RequestError(msg='对方未开启「允许被关注」')

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
            target_agent = await identity.get_agent(db, hasn_id=target_hasn_id)
            target_owner_hasn_id = target_agent.owner_id if target_agent else None
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
    async def _resolve_friendship_status(
        db: AsyncSession,
        *,
        viewer_hasn_id: str | None,
        target_hasn_id: str,
        target_owner_hasn_id: str | None = None,
    ) -> str:
        """返回主页查看者与目标之间的权威好友状态。"""
        if not viewer_hasn_id:
            return 'none'
        if target_hasn_id == viewer_hasn_id:
            return 'self'
        if target_owner_hasn_id == viewer_hasn_id:
            return 'owned'

        relation = (
            await db.execute(
                select(HasnContacts.status, HasnContacts.trust_level)
                .where(
                    HasnContacts.owner_id == viewer_hasn_id,
                    HasnContacts.peer_id == target_hasn_id,
                    HasnContacts.relation_type == 'social',
                )
                .limit(1)
            )
        ).first()
        if relation is not None:
            if relation.trust_level == 0 or relation.status == 'blocked':
                return 'blocked'
            if relation.status == 'connected':
                return 'connected'

        pending_request = (
            await db.execute(
                select(HasnContactRequests.id)
                .where(
                    HasnContactRequests.from_id == viewer_hasn_id,
                    HasnContactRequests.to_id == target_hasn_id,
                    HasnContactRequests.relation_type == 'social',
                    HasnContactRequests.status == 'pending',
                )
                .limit(1)
            )
        ).first()
        return 'pending' if pending_request is not None else 'none'

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
        human = await identity.get_human(db, hasn_id=hasn_id)
        agent = None
        if human is None:
            agent = await identity.get_agent(db, hasn_id=hasn_id)
        if human is None and agent is None:
            raise errors.NotFoundError(msg='主页不存在')

        # 「公开个人主页」边界：human 关闭后，除本人外不可查看其社区主页。
        # 仅约束 human（设置在其 community_settings）；agent 主页可见性另有治理。
        if human is not None:
            is_self_view = bool(viewer_hasn_id and viewer_hasn_id == hasn_id)
            human_settings = human.community_settings if isinstance(human.community_settings, dict) else {}
            if not is_self_view and not human_settings.get('show_profile', True):
                raise errors.NotFoundError(msg='该用户未公开社区主页')

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
            friendship_status = await CommunityService._resolve_friendship_status(
                db,
                viewer_hasn_id=viewer_hasn_id,
                target_hasn_id=hasn_id,
            )
            base.update({
                'type': 'human',
                'display_name': human.nickname or hasn_id,
                'avatar': human.avatar or '',
                'bio': human.bio or '',
                'tags': human.tags or [],
                'friendship_status': friendship_status,
            })
            return base

        # agent
        if agent is None:
            raise errors.NotFoundError(msg='主页不存在')
        profile_json = agent.profile_json if isinstance(agent.profile_json, dict) else {}
        community_block = profile_json.get('community', {}) if isinstance(profile_json, dict) else {}

        # 分身主页直接返回稳定关系字段，避免客户端再从主人分身列表拼装资料。
        # 好友数按“以该分身为联系人目标”的 connected social 边计数；每个请求方仅有一条权威边。
        friend_count = (
            await db.execute(
                select(func.count())
                .select_from(HasnContacts)
                .where(
                    HasnContacts.peer_id == hasn_id,
                    HasnContacts.relation_type == 'social',
                    HasnContacts.status == 'connected',
                )
            )
        ).scalar() or 0
        friendship_status = await CommunityService._resolve_friendship_status(
            db,
            viewer_hasn_id=viewer_hasn_id,
            target_hasn_id=hasn_id,
            target_owner_hasn_id=agent.owner_id,
        )
        online_map = await _presence_query.get_online_map([hasn_id])

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
            owner = await identity.get_human(db, hasn_id=agent.owner_id)
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
            'profession': agent.profession or '',
            'online_status': 'online' if online_map.get(hasn_id) else 'offline',
            'friend_count': int(friend_count),
            # 当前好友请求契约统一由目标分身主人审批；此字段直接反映真实服务行为。
            'add_friend_needs_approval': True,
            'friendship_status': friendship_status,
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
        author_human = await identity.get_human(db, hasn_id=hasn_id)
        if author_human is not None:
            return {
                'hasn_id': hasn_id,
                'type': 'human',
                'display_name': author_human.nickname or hasn_id,
                'avatar': author_human.avatar,
            }

        author_agent = await identity.get_agent(db, hasn_id=hasn_id)
        author_info: dict[str, Any] = {
            'hasn_id': hasn_id,
            'type': 'agent',
            'display_name': (author_agent.display_name if author_agent else None) or hasn_id,
            'avatar': author_agent.avatar if author_agent else None,
        }
        if author_agent and author_agent.owner_id:
            owner_human = await identity.get_human(db, hasn_id=author_agent.owner_id)
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
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)
        stmt = (
            select(HasnPosts)
            .where(
                HasnPosts.author_hasn_id == hasn_id,
                HasnPosts.status == 'published',
                # 可见性闸：主页列表同样按 viewer 判权——看别人的主页不再能看到
                # 其私密/followers 帖（followers 帖仅关注者可见）；看自己主页不受限。
                content_visibility_sql(HasnPosts, viewer_hasn_id=viewer_hasn_id),
            )
            .order_by(HasnPosts.published_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        posts = result.scalars().all()

        # 作者身份（该列表所有帖子同属 hasn_id）+ 查看者点赞态，与信息流条目同形，
        # 否则前端复用的帖子卡缺 author/content_type 会渲染成空头像卡。
        author_info = await CommunityService._resolve_profile_author(db, hasn_id)
        post_ids = [post.post_id for post in posts]
        liked_ids, _ = await CommunityService._batch_reactions(db, viewer_hasn_id, 'post', post_ids)

        items = [{
                'content_type': 'post',
                'post_id': post.post_id,
                'author': author_info,
                'content': post.content,
                'tags': post.tags or [],
                'media': _present_media(post.media_json),
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
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)
        stmt = (
            select(HasnArticles)
            .where(
                HasnArticles.author_hasn_id == hasn_id,
                HasnArticles.status == 'published',
                # 可见性闸：与 get_profile_posts 同一判据——别人主页的私密/followers 文不再外漏。
                content_visibility_sql(HasnArticles, viewer_hasn_id=viewer_hasn_id),
            )
            .order_by(HasnArticles.published_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        articles = result.scalars().all()

        # 作者身份（该列表所有文章同属 hasn_id）+ 查看者点赞/收藏态，与信息流条目同形
        author_info = await CommunityService._resolve_profile_author(db, hasn_id)
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
        agents = await identity.agents_of_owner(db, owner_hasn_id=hasn_id)

        # 查询主人真实 display_name（修 owner display_name 写死，doc-12 C4）
        owner_human = await identity.get_human(db, hasn_id=hasn_id)
        owner_display_name = (owner_human.nickname if owner_human else None) or hasn_id

        # 查询当前用户是否已关注这些 Agent
        viewer_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)

        agent_list: list[dict[str, Any]] = []
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

        # 头像在线状态点（Redis presence，断线即 offline 不读僵尸持久列），与广场一致。
        if agent_list:
            online_map = await _presence_query.get_online_map([a['hasn_id'] for a in agent_list])
            for a in agent_list:
                a['online_status'] = 'online' if online_map.get(a['hasn_id']) else 'offline'

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
        """收藏夹列表（含 item_count 与默认夹标记）。

        默认夹采用不依赖名称的稳定策略：每位主人创建时间最早、同秒时内部 ID 最小的
        收藏夹为默认夹。默认夹始终保留，作为未指定收藏夹时的确定落点。
        """
        stmt = (
            select(HasnCollections)
            .where(HasnCollections.owner_hasn_id == owner_hasn_id)
            .order_by(HasnCollections.created_time.asc(), HasnCollections.id.asc())
        )
        collections = (await db.execute(stmt)).scalars().all()
        default_collection_id = collections[0].collection_id if collections else None
        return {
            'items': [
                {
                    'collection_id': c.collection_id,
                    'name': c.name,
                    'is_public': c.is_public,
                    'is_default': c.collection_id == default_collection_id,
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
        normalized_name = name.strip()
        if not normalized_name:
            raise errors.RequestError(msg='收藏夹名称不能为空')
        has_existing = (
            await db.execute(
                select(HasnCollections.id)
                .where(HasnCollections.owner_hasn_id == owner_hasn_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        collection_id = f'col_{uuid4_str()[:12]}'
        collection = HasnCollections(
            collection_id=collection_id,
            owner_hasn_id=owner_hasn_id,
            name=normalized_name,
            is_public=is_public,
            item_count=0,
        )
        db.add(collection)
        await db.flush()
        return {
            'collection_id': collection_id,
            'name': normalized_name,
            'is_public': is_public,
            'is_default': has_existing is None,
            'item_count': 0,
        }

    @staticmethod
    async def update_collection(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        collection_id: str,
        name: str | None = None,
        is_public: bool | None = None,
    ) -> dict[str, Any]:
        """更新本人收藏夹的名称与公开性。"""
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

        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise errors.RequestError(msg='收藏夹名称不能为空')
            collection.name = normalized_name
        if is_public is not None:
            collection.is_public = is_public
        collection.updated_time = timezone.now()
        await db.flush()

        default_collection_id = (
            await db.execute(
                select(HasnCollections.collection_id)
                .where(HasnCollections.owner_hasn_id == owner_hasn_id)
                .order_by(HasnCollections.created_time.asc(), HasnCollections.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return {
            'collection_id': collection.collection_id,
            'name': collection.name,
            'is_public': collection.is_public,
            'is_default': collection.collection_id == default_collection_id,
            'item_count': collection.item_count,
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

        default_collection_id = (
            await db.execute(
                select(HasnCollections.collection_id)
                .where(HasnCollections.owner_hasn_id == owner_hasn_id)
                .order_by(HasnCollections.created_time.asc(), HasnCollections.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if collection.collection_id == default_collection_id:
            raise errors.RequestError(msg='默认收藏夹不能删除')

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
        target_type: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """收藏夹内容列表（先按内容类型过滤，再做稳定游标分页）。"""
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
        if target_type:
            stmt = stmt.where(HasnCollectionItems.target_type == target_type)
        if cursor:
            try:
                cursor_id = int(cursor)
            except ValueError as exc:
                raise errors.RequestError(msg='收藏夹游标无效') from exc
            stmt = stmt.where(HasnCollectionItems.id < cursor_id)
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
    async def remove_collection_item(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        collection_id: str,
        target_type: str,
        target_id: str,
    ) -> dict[str, Any]:
        """仅从指定收藏夹移出目标，不影响同一目标在其他收藏夹中的副本。"""
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

        item = (
            await db.execute(
                select(HasnCollectionItems).where(
                    HasnCollectionItems.collection_id == collection_id,
                    HasnCollectionItems.target_type == target_type,
                    HasnCollectionItems.target_id == target_id,
                )
            )
        ).scalar_one_or_none()
        if item:
            await db.delete(item)
            collection.item_count = max(0, (collection.item_count or 0) - 1)
            await CommunityService._adjust_collect_count(db, target_type, target_id, -1)
            await db.flush()

        remaining = (
            await db.execute(
                select(HasnCollectionItems.id)
                .join(
                    HasnCollections,
                    HasnCollectionItems.collection_id == HasnCollections.collection_id,
                )
                .where(
                    HasnCollections.owner_hasn_id == owner_hasn_id,
                    HasnCollectionItems.target_type == target_type,
                    HasnCollectionItems.target_id == target_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return {'is_collected': remaining is not None}

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

        owner = await identity.get_human(db, hasn_id=collection.owner_hasn_id)

        # 复用 owner-scoped 的内容项投影（access 已在上方校验）
        items_page = await CommunityService.get_collection_items(
            db,
            owner_hasn_id=collection.owner_hasn_id,
            collection_id=collection_id,
            target_type=None,
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
        obj: HasnPosts | HasnArticles | None
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
                    .order_by(HasnCollections.created_time.asc(), HasnCollections.id.asc())
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
            post_obj = (await db.execute(select(HasnPosts).where(HasnPosts.post_id == target_id))).scalars().first()
            if post_obj:
                author_hasn_id, author_type, content_owner_hasn_id, preview = (
                    post_obj.author_hasn_id,
                    post_obj.author_type,
                    post_obj.owner_hasn_id,
                    post_obj.content,
                )
        elif target_type == 'article':
            article_obj = (
                await db.execute(select(HasnArticles).where(HasnArticles.article_id == target_id))
            ).scalars().first()
            if article_obj:
                author_hasn_id, author_type, content_owner_hasn_id, preview = (
                    article_obj.author_hasn_id,
                    article_obj.author_type,
                    article_obj.owner_hasn_id,
                    article_obj.title,
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
                FROM hasn_community.hasn_posts
                WHERE status = 'published'
                  AND published_time >= now() - make_interval(days => :days)
                UNION ALL
                SELECT unnest(tags) AS tag, like_count, comment_count, published_time
                FROM hasn_community.hasn_articles
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
        online_only: bool = False,
        cursor: str | None = None,
        limit: int = 3,
        relation_gateway: Any | None = None,
    ) -> dict[str, Any]:
        """
        获取推荐/广场 Agent（doc-13 §3.4，对应 D1/E-4 筛选）。

        - category/capability：按 capability_summary_json 文本匹配过滤（真实过滤）
        - sort：relevance（粉丝数）/ collected（内容被收藏数）/ active（最近活跃）
        - online_only：仅返回 Redis presence 当前在线的分身
        - cursor：offset 分页
        社交开放读取 IM 权威设置；follower_count 使用实时统计（身份表无该列）。

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
        friend_sq = (
            select(func.count())
            .select_from(HasnContacts)
            .where(
                HasnContacts.peer_id == HasnAgents.hasn_id,
                HasnContacts.relation_type == 'social',
                HasnContacts.status == 'connected',
            )
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
                friend_sq.label('friend_count'),
                collected_sq.label('collected_count'),
            )
            .join(OwnerHuman, HasnAgents.owner_id == OwnerHuman.hasn_id)
            .where(
                HasnAgents.status == 'active',
                HasnAgents.deleted_at.is_(None),
            )
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

        from backend.app.hasn_im.application.provider import get_relation_gateway

        resolved_relation_gateway = relation_gateway or get_relation_gateway()
        # offset 游标基于身份候选位置；跨域设置按批读取，关闭社交的分身不会占返回配额。
        offset = int(cursor) if cursor else 0
        scan_offset = offset
        page_size = max(50, limit * 2)
        rows: list[Any] = []
        resume_offset: int | None = None
        next_cursor: str | None = None
        while True:
            page = list(
                (
                    await db.execute(
                        stmt.offset(scan_offset).limit(page_size),
                    )
                ).all()
            )
            if not page:
                break
            enabled = await resolved_relation_gateway.filter_socially_enabled_agents(
                agent_hasn_ids=[row.HasnAgents.hasn_id for row in page],
            )
            online_map = (
                await _presence_query.get_online_map(
                    [row.HasnAgents.hasn_id for row in page if row.HasnAgents.hasn_id in enabled]
                )
                if online_only
                else {}
            )
            found_more = False
            for index, row in enumerate(page):
                if row.HasnAgents.hasn_id not in enabled:
                    continue
                if online_only and not online_map.get(row.HasnAgents.hasn_id):
                    continue
                if len(rows) < limit:
                    rows.append(row)
                    if len(rows) == limit:
                        resume_offset = scan_offset + index + 1
                else:
                    found_more = True
                    break
            if found_more:
                next_cursor = str(resume_offset)
                break
            scan_offset += len(page)
            if len(page) < page_size:
                break

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
            friendship_status = await CommunityService._resolve_friendship_status(
                db,
                viewer_hasn_id=viewer_hasn_id,
                target_hasn_id=agent.hasn_id,
                target_owner_hasn_id=agent.owner_id,
            )
            summary = agent.capability_summary_json or {}
            capabilities: list[str] = []
            if isinstance(summary, dict):
                for key in ('skills', 'strengths', 'tags'):
                    values = summary.get(key)
                    if not isinstance(values, list):
                        continue
                    for value in values:
                        if isinstance(value, str) and value.strip() and value.strip() not in capabilities:
                            capabilities.append(value.strip())

            agents.append({
                'hasn_id': agent.hasn_id,
                'display_name': agent.display_name,
                'profession': agent.profession,
                'description': agent.description,
                'bio': agent.bio or '',
                'avatar': agent.avatar,
                'capability_summary': summary,
                'capabilities': capabilities,
                'owner': {
                    'hasn_id': row.owner_hasn_id,
                    'display_name': row.owner_nickname or row.owner_hasn_id,
                },
                'follower_count': int(row.follower_count or 0),
                'following_count': int(row.following_count or 0),
                'friend_count': int(row.friend_count or 0),
                'collected_count': int(row.collected_count or 0),
                'is_following': is_following,
                'friendship_status': friendship_status,
                'add_friend_needs_approval': True,
                'last_heartbeat_at': (
                    agent.last_heartbeat_at.isoformat() if agent.last_heartbeat_at else None
                ),
            })

        # 头像在线状态点（Redis presence，断线即 offline 不读僵尸持久列），与社区作者一致。
        if agents:
            online_map = await _presence_query.get_online_map([a['hasn_id'] for a in agents])
            for a in agents:
                a['online_status'] = 'online' if online_map.get(a['hasn_id']) else 'offline'

        return {
            'items': agents,
            'next_cursor': next_cursor,
        }

    # ==================== 发现用户与 Agent（统一搜索 + 无参自动推荐）====================

    @staticmethod
    def _peer_human_item(human: HasnHumans, *, match_reason: str, rank: int) -> dict[str, Any]:
        """把 HasnHumans 行规范成统一发现结果 item（人）。"""
        return {
            '_rank': rank,
            'hasn_id': human.hasn_id,
            'star_id': human.star_id,
            'type': 'human',
            'name': human.nickname or human.star_id or '',
            'avatar': human.avatar,
            'bio': human.bio or '',
            'tags': list(human.tags or []),
            'profession': None,
            'owner': None,
            'match_reason': match_reason,
            'existing_relation': None,
        }

    @staticmethod
    def _peer_agent_item(
        agent: HasnAgents,
        *,
        owner_hasn_id: str | None,
        owner_name: str | None,
        follower_count: int,
        match_reason: str,
        rank: int,
    ) -> dict[str, Any]:
        """把 HasnAgents 行规范成统一发现结果 item（分身）。"""
        return {
            '_rank': rank,
            'hasn_id': agent.hasn_id,
            'star_id': agent.star_id,
            'type': 'agent',
            'name': agent.display_name or agent.agent_name or '',
            'avatar': agent.avatar,
            'bio': agent.bio or agent.description or '',
            'tags': list(agent.tags or []),
            'profession': agent.profession,
            'owner': {
                'hasn_id': owner_hasn_id,
                'display_name': owner_name or owner_hasn_id or '',
            },
            'follower_count': int(follower_count or 0),
            'match_reason': match_reason,
            'existing_relation': None,
        }

    @staticmethod
    def _human_searchable_cond() -> Any:
        """人是否允许被搜索/发现：community_settings.searchable 缺省或非 'false' 即可见（CS-RF-345）。"""
        searchable_txt = HasnHumans.community_settings['searchable'].astext
        return or_(searchable_txt.is_(None), searchable_txt != 'false')

    @staticmethod
    async def _discover_agent_rows(
        db: AsyncSession,
        *,
        extra_where: list[Any],
        order_by: list[Any],
        limit: int,
        exclude_owner_hasn_id: str | None,
        relation_gateway: Any,
    ) -> list[Any]:
        """查询可发现的分身（社交开放 + 未删 + owner join + 实时粉丝数），供搜索/推荐复用。"""
        owner_human = aliased(HasnHumans)
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
        stmt = (
            select(
                HasnAgents,
                owner_human.hasn_id.label('owner_hasn_id'),
                owner_human.nickname.label('owner_nickname'),
                follower_sq.label('follower_count'),
            )
            .join(owner_human, HasnAgents.owner_id == owner_human.hasn_id)
            .where(HasnAgents.deleted_at.is_(None))
        )
        if exclude_owner_hasn_id:
            stmt = stmt.where(HasnAgents.owner_id != exclude_owner_hasn_id)
        for cond in extra_where:
            stmt = stmt.where(cond)
        stmt = stmt.order_by(*order_by)
        visible: list[Any] = []
        offset = 0
        page_size = max(50, limit * 2)
        while len(visible) < limit:
            rows = list(
                (
                    await db.execute(
                        stmt.offset(offset).limit(page_size),
                    )
                ).all()
            )
            if not rows:
                break
            enabled = await relation_gateway.filter_socially_enabled_agents(
                agent_hasn_ids=[row.HasnAgents.hasn_id for row in rows],
            )
            visible.extend(
                row for row in rows if row.HasnAgents.hasn_id in enabled
            )
            offset += len(rows)
            if len(rows) < page_size:
                break
        return visible[:limit]

    @staticmethod
    async def discover_peers(
        db: AsyncSession,
        *,
        viewer_user_id: int | None = None,
        query: str | None = None,
        peer_type: str = 'all',
        limit: int = 12,
        relation_gateway: Any | None = None,
    ) -> dict[str, Any]:
        """发现用户和 Agent（统一搜索 + 无参自动推荐）。

        - **传 query**：唤星号精确（含 `#` 查 agent，否则查 human）+ 昵称(human)/显示名(agent) 前缀
          + 手机号(human)精确，human 与 agent 一起返回。
        - **不传 query**：按主人 `tags`（兴趣）匹配 human（标签重叠）+ agent（标签/专业匹配）；
          无兴趣信号或命中不足时回落「活跃度推荐」（agent 按粉丝数，human 按近期）。

        每条带 `type`(human/agent)、`match_reason`、`existing_relation`（从主人视角，驱动「已是好友/已发送」）。
        隐私：human 仅 active 且尊重 `searchable` 设置；agent 仅 social_enabled。从主人视角排除自己与名下分身。

        :return: {items, total, mode}
        """
        from operator import itemgetter

        from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao
        from backend.app.hasn_im.application.provider import get_relation_gateway

        q = (query or '').strip()
        resolved_relation_gateway = relation_gateway or get_relation_gateway()
        peer_type = peer_type if peer_type in ('all', 'human', 'agent') else 'all'
        want_human = peer_type in ('all', 'human')
        want_agent = peer_type in ('all', 'agent')
        limit = max(1, min(int(limit or 12), 50))
        self_hasn_id = await CommunityService._resolve_human_hasn_id(db, viewer_user_id)

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _push(item: dict[str, Any]) -> None:
            if item['hasn_id'] in seen or item['hasn_id'] == self_hasn_id:
                return
            seen.add(item['hasn_id'])
            candidates.append(item)

        if q:
            await CommunityService._discover_by_query(
                db, q=q, self_hasn_id=self_hasn_id, want_human=want_human,
                want_agent=want_agent, limit=limit, push=_push,
                humans_dao=hasn_humans_dao, agents_dao=hasn_agents_dao,
                relation_gateway=resolved_relation_gateway,
            )
            mode = 'search'
        else:
            await CommunityService._discover_auto(
                db, self_hasn_id=self_hasn_id, want_human=want_human,
                want_agent=want_agent, limit=limit, push=_push, humans_dao=hasn_humans_dao,
                relation_gateway=resolved_relation_gateway,
            )
            mode = 'discover'

        candidates.sort(key=itemgetter('_rank'), reverse=True)
        items = candidates[:limit]

        for item in items:
            item.pop('_rank', None)
            relation = None
            if self_hasn_id:
                relation = await resolved_relation_gateway.resolve_effective_relation(
                    owner_hasn_id=self_hasn_id,
                    peer_hasn_id=item['hasn_id'],
                )
            item['existing_relation'] = relation.status if relation else None

        return {'items': items, 'total': len(items), 'mode': mode}

    @staticmethod
    async def _discover_by_query(
        db: AsyncSession, *, q: str, self_hasn_id: str | None, want_human: bool,
        want_agent: bool, limit: int, push: Any, humans_dao: Any,
        agents_dao: Any, relation_gateway: Any,
    ) -> None:
        """query 模式：人与分身分头搜，结果汇入 push。"""
        if want_human:
            await CommunityService._query_humans(
                db, q=q, self_hasn_id=self_hasn_id, limit=limit, push=push, humans_dao=humans_dao
            )
        if want_agent:
            await CommunityService._query_agents(
                db,
                q=q,
                self_hasn_id=self_hasn_id,
                limit=limit,
                push=push,
                agents_dao=agents_dao,
                relation_gateway=relation_gateway,
            )

    @staticmethod
    async def _query_humans(
        db: AsyncSession, *, q: str, self_hasn_id: str | None, limit: int, push: Any, humans_dao: Any
    ) -> None:
        """query·人：唤星号精确 + 昵称前缀（仅 searchable）+ 手机号精确。"""
        if '#' not in q:
            human = await humans_dao.get_by_star_id(db, q)
            if human and human.status == 'active':
                push(CommunityService._peer_human_item(human, match_reason='唤星号精确', rank=100))
        for h in await humans_dao.search_by_name(db, prefix=q, limit=limit, exclude_hasn_id=self_hasn_id):
            if await community_settings_service.get_profile_flag(db, hasn_id=h.hasn_id, key='searchable'):
                push(CommunityService._peer_human_item(h, match_reason='昵称匹配', rank=80))
        phone_match = await humans_dao.search_by_phone(db, q, exclude_hasn_id=self_hasn_id)
        if phone_match and phone_match.status == 'active':
            push(CommunityService._peer_human_item(phone_match, match_reason='手机号精确', rank=100))

    @staticmethod
    async def _query_agents(
        db: AsyncSession, *, q: str, self_hasn_id: str | None, limit: int,
        push: Any, agents_dao: Any, relation_gateway: Any,
    ) -> None:
        """query·分身：唤星号精确（含 #）+ 显示名前缀。"""
        if '#' in q:
            agent = await agents_dao.get_by_star_id(db, q)
            enabled = (
                await relation_gateway.filter_socially_enabled_agents(
                    agent_hasn_ids=[agent.hasn_id],
                )
                if agent
                else set()
            )
            if (
                agent
                and agent.hasn_id in enabled
                and agent.deleted_at is None
                and agent.owner_id != self_hasn_id
            ):
                push(CommunityService._peer_agent_item(
                    agent, owner_hasn_id=agent.owner_id, owner_name=None,
                    follower_count=0, match_reason='唤星号精确', rank=100,
                ))
        name_cond = func.lower(HasnAgents.display_name).like(f'{q.lower()}%')
        rows = await CommunityService._discover_agent_rows(
            db, extra_where=[name_cond], order_by=[HasnAgents.created_time.desc()],
            limit=limit, exclude_owner_hasn_id=self_hasn_id,
            relation_gateway=relation_gateway,
        )
        for row in rows:
            push(CommunityService._peer_agent_item(
                row.HasnAgents, owner_hasn_id=row.owner_hasn_id, owner_name=row.owner_nickname,
                follower_count=row.follower_count, match_reason='名称匹配', rank=80,
            ))

    @staticmethod
    async def _discover_auto(
        db: AsyncSession, *, self_hasn_id: str | None, want_human: bool,
        want_agent: bool, limit: int, push: Any, humans_dao: Any,
        relation_gateway: Any,
    ) -> None:
        """无参模式：解析主人兴趣，人与分身分头「兴趣→活跃」推荐。"""
        interests: list[str] = []
        if self_hasn_id:
            me = await humans_dao.get_by_hasn_id(db, self_hasn_id)
            interests = [t for t in (me.tags or []) if t][:20] if me else []
        if want_human:
            await CommunityService._discover_humans(
                db, interests=interests, self_hasn_id=self_hasn_id, limit=limit, push=push
            )
        if want_agent:
            await CommunityService._discover_agents(
                db,
                interests=interests,
                self_hasn_id=self_hasn_id,
                limit=limit,
                push=push,
                relation_gateway=relation_gateway,
            )

    @staticmethod
    async def _discover_humans(
        db: AsyncSession, *, interests: list[str], self_hasn_id: str | None, limit: int, push: Any
    ) -> None:
        """无参·人：先 tags 兴趣重叠，再近期注册活跃回落（均 active + searchable）。"""
        from sqlalchemy.dialects.postgresql import ARRAY

        searchable = CommunityService._human_searchable_cond()
        base = [HasnHumans.status == 'active', searchable]
        if self_hasn_id:
            base.append(HasnHumans.hasn_id != self_hasn_id)
        if interests:
            stmt = (
                select(HasnHumans)
                .where(*base, HasnHumans.tags.op('&&')(cast(interests, ARRAY(Text))))
                .order_by(HasnHumans.id.desc())
                .limit(limit)
            )
            for h in (await db.execute(stmt)).scalars().all():
                matched = next((t for t in (h.tags or []) if t in interests), None)
                push(CommunityService._peer_human_item(
                    h, match_reason=f'兴趣匹配:{matched}' if matched else '兴趣匹配', rank=60,
                ))
        stmt = select(HasnHumans).where(*base).order_by(HasnHumans.id.desc()).limit(limit)
        for h in (await db.execute(stmt)).scalars().all():
            push(CommunityService._peer_human_item(h, match_reason='活跃推荐', rank=40))

    @staticmethod
    async def _discover_agents(
        db: AsyncSession, *, interests: list[str], self_hasn_id: str | None,
        limit: int, push: Any, relation_gateway: Any,
    ) -> None:
        """无参·分身：先 tags/专业兴趣匹配，再按粉丝活跃回落。"""
        from sqlalchemy.dialects.postgresql import ARRAY

        if interests:
            # HasnAgents.tags 是 JSONB（非 PG 数组），用 jsonb_exists_any 判与兴趣交集
            cond = or_(
                func.jsonb_exists_any(HasnAgents.tags, cast(interests, ARRAY(Text))),
                HasnAgents.profession.in_(interests),
            )
            rows = await CommunityService._discover_agent_rows(
                db, extra_where=[cond], order_by=[HasnAgents.created_time.desc()],
                limit=limit, exclude_owner_hasn_id=self_hasn_id,
                relation_gateway=relation_gateway,
            )
            for row in rows:
                agent = row.HasnAgents
                matched = next((t for t in (agent.tags or []) if t in interests), agent.profession)
                push(CommunityService._peer_agent_item(
                    agent, owner_hasn_id=row.owner_hasn_id, owner_name=row.owner_nickname,
                    follower_count=row.follower_count,
                    match_reason=f'兴趣匹配:{matched}' if matched else '兴趣匹配', rank=60,
                ))
        rows = await CommunityService._discover_agent_rows(
            db, extra_where=[],
            order_by=[text('follower_count DESC'), HasnAgents.last_heartbeat_at.desc().nullslast()],
            limit=limit, exclude_owner_hasn_id=self_hasn_id,
            relation_gateway=relation_gateway,
        )
        for row in rows:
            push(CommunityService._peer_agent_item(
                row.HasnAgents, owner_hasn_id=row.owner_hasn_id, owner_name=row.owner_nickname,
                follower_count=row.follower_count, match_reason='活跃推荐', rank=40,
            ))

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
        agent_ids = [a.hasn_id for a in await identity.agents_of_owner(db, owner_hasn_id=hasn_id)]

        if not agent_ids:
            return {'items': [], 'next_cursor': None}

        # 查询这些 Agent 的待确认草稿（展示字段交给下方 `_enrich_authors` 批量投影回填）
        stmt = (
            select(HasnPosts)
            .where(
                HasnPosts.author_type == 'agent',
                HasnPosts.author_hasn_id.in_(agent_ids),
                HasnPosts.status == 'pending_review',
            )
            .order_by(HasnPosts.created_time.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        posts = result.scalars().all()

        items: list[dict[str, Any]] = []
        for post in posts:
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
                },
                'content': post.content,
                'tags': post.tags or [],
                'media': _present_media(post.media_json),
                'like_count': post.like_count,
                'comment_count': post.comment_count,
                'published_time': post.created_time.isoformat() if post.created_time else None,
                'is_liked': False,
                'is_collected': False,
            })

        await CommunityService._enrich_authors(db, [it['author'] for it in items if it.get('author')])
        return {
            'items': items,
            'next_cursor': posts[-1].post_id if posts else None,
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
        comment_policy: str | None = None,
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

        # 评论策略：未显式指定 → 回落主人默认（default_comment_policy 设置真生效）
        if comment_policy is None:
            comment_policy = await community_settings_service.get_default_comment_policy(db, hasn_id=owner_hasn_id)

        # TODO: 获取当前 active workspace
        workspace_kind = 'personal'
        workspace_id = str(user_id)

        status = 'published'
        circle = None
        if circle_id:
            circle, needs_review = await circle_service.assert_can_post(
                db,
                circle_id=circle_id,
                actor_hasn_id=author_hasn_id,
            )
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
        elif circle and status == 'pending_review':
            await circle_service.notify_pending_content(
                db,
                circle=circle,
                author_hasn_id=author_hasn_id,
                content_type='article',
                content_id=article_id,
            )

        placement_result = None
        if doc_placement:
            placement_result = await doc_service.place_article(
                db, article_id=article_id, article_title=title, actor_hasn_id=owner_hasn_id, owner_user_id=user_id,
                author_type='human', author_hasn_id=author_hasn_id, placement=doc_placement, allow_visibility=True,
            )
        if placement_result and status == 'published':
            await doc_service.notify_article_updated(
                db,
                article_id=article_id,
                actor_hasn_id=author_hasn_id,
            )

        # 已发布（非待审）才对关注者可见，实时通知其在线设备刷新社区镜像
        if status == 'published':
            await CommunityService._fanout_to_followers(db, author_hasn_id=author_hasn_id)

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

        # 查询文章
        article = (
            await db.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))
        ).scalar_one_or_none()

        if not article:
            raise errors.NotFoundError(msg='文章不存在')

        # status 闸：仅 published 对外可读；作者本人/责任主体（owner）可看自己的草稿与待审
        # （与 get_post 同一口径——分身发文默认 pending_review，主人需能点进详情审核）。
        # 此前完全没有 status/visibility 闸，任何人拿到 article_id 就能读别人的草稿与私密文。
        if article.status == 'deleted' or (
            article.status != 'published'
            and not is_own_content(
                viewer_hasn_id=hasn_id,
                author_hasn_id=article.author_hasn_id,
                owner_hasn_id=article.owner_hasn_id,
            )
        ):
            raise errors.NotFoundError(msg='文章不存在')

        # 可见性闸：published 文也必须过 visibility（public/followers/private/circle）。
        # 判据与帖子详情/翻译接口共用 content_visibility 唯一实现；拒绝一律 404，不做存在性探测。
        if article.status == 'published':
            decision = await evaluate_content_visibility(
                db,
                visibility=article.visibility,
                author_hasn_id=article.author_hasn_id,
                owner_hasn_id=article.owner_hasn_id,
                circle_id=article.circle_id,
                viewer_hasn_id=hasn_id,
            )
            if not decision.allowed:
                raise errors.NotFoundError(msg='文章不存在')

        # 查询作者信息（展示字段交给下方 `_enrich_authors` 二段式批量投影回填）
        author_info: dict[str, Any] = {'hasn_id': article.author_hasn_id, 'type': article.author_type}

        # 回填当前 viewer 对该文章的点赞/收藏态（doc-12 B-3，与 get_post 一致）
        liked_ids, collected_ids = await CommunityService._batch_reactions(
            db, hasn_id, 'article', [article.article_id]
        )
        is_liked = article.article_id in liked_ids
        is_collected = article.article_id in collected_ids

        # 评论权限预判（与 get_post 同一套 _check_can_comment，见其注释）。
        can_comment, comment_disabled_reason = await CommunityService._check_can_comment(
            db,
            policy=article.comment_policy,
            author_hasn_id=article.author_hasn_id,
            commenter_hasn_id=hasn_id,
        )

        await CommunityService._enrich_authors(db, [author_info])
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
            'can_comment': can_comment,
            'comment_disabled_reason': comment_disabled_reason,
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
        if article.status == 'published':
            from backend.app.hasn_community.service.doc_service import doc_service

            await doc_service.notify_article_updated(
                db,
                article_id=article.article_id,
                actor_hasn_id=article.author_hasn_id,
            )

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

        # 展示字段交给下方 `_enrich_authors` 二段式批量投影回填，不再逐表点查 + JOIN。
        author_info: dict[str, Any] = {'hasn_id': article.author_hasn_id, 'type': article.author_type}
        await CommunityService._enrich_authors(db, [author_info])

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
