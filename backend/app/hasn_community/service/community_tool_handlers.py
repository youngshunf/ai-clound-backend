from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_community.model import HasnArticles, HasnPosts
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.notification_service import notification_service
from backend.common.exception import errors
from backend.database.db import uuid4_str

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.security.agent_jwt import AgentTokenPayload

# get_profile_content 支持的内容类型
_PROFILE_CONTENT_KINDS = {'posts', 'articles', 'collections', 'agents'}


async def handle_community_get_feed(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """处理 community.get_feed 工具调用：复用真实 get_feed 取数。

    与 Agent REST `/api/v1/community/agent/feed` 同源：个性化以 Agent 主人上下文
    （owner_user_id）计算，feed_type=following 时按登录用户的关注流返回。
    绝不返回假数据/空填充掩盖未实现（零 Mock 零 Fake）。
    """
    feed_type = input_payload.get('feed_type', 'recommend')
    cursor = input_payload.get('cursor')
    limit = int(input_payload.get('limit', 20))

    result = await community_service.get_feed(
        db,
        user_id=agent.owner_user_id,
        feed_type=feed_type,
        cursor=cursor,
        limit=limit,
    )
    return {
        'posts': result.get('items', []),
        'cursor': result.get('next_cursor'),
        'has_more': result.get('next_cursor') is not None,
    }


async def handle_community_create_post(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """处理 community.create_post 工具调用

    Agent 发帖默认进入 pending_review 状态，需要主人审核后发布。
    circle_id 非空：校验 Agent 是该圈 active 成员（满足 post_policy），内容只进圈子流。
    """
    from backend.app.hasn_community.service.circle_service import circle_service
    from backend.app.hasn_community.service.topic_service import topic_service

    post_id = f"p_{uuid4_str()[:12]}"
    circle_id = input_payload.get('circle_id')
    if circle_id:
        await circle_service.assert_can_post(db, circle_id=circle_id, actor_hasn_id=agent.agent_hasn_id)

    post = HasnPosts(
        post_id=post_id,
        author_type='agent',
        author_hasn_id=agent.agent_hasn_id,
        author_user_id=None,
        owner_hasn_id=agent.owner_hasn_id,
        origin_workspace_kind='personal',
        origin_workspace_id=str(agent.owner_user_id),
        content=input_payload['content'],
        tags=input_payload.get('tags', []),
        skill_tags=input_payload.get('skill_tags', []),
        visibility=input_payload.get('visibility', 'public'),
        comment_policy=input_payload.get('comment_policy', 'all'),
        generation_type='agent',
        status='pending_review',  # Agent 发帖需要审核
        circle_id=circle_id,
    )

    db.add(post)
    await db.flush()
    await topic_service.rewrite_content_topics(db, content_type='post', content_id=post_id, owner_hasn_id=agent.owner_hasn_id, tags=input_payload.get('tags', []))
    await db.commit()
    await db.refresh(post)

    # 通知主人：Agent 草稿待确认（doc-13 §2.1.3）
    from backend.app.hasn_community.service.notification_service import notification_service

    await notification_service.notify_draft_pending(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        content_type='post',
        content_id=post.post_id,
        preview=post.content,
    )
    await db.commit()

    # 发完帖即给主人投一张「可点进详情」的卡片消息（落主人↔分身 IM 会话；best-effort，独立事务）
    from backend.app.hasn_community.service.community_card_notifier import notify_owner_post_card

    await notify_owner_post_card(
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        post_id=post.post_id,
        content=post.content,
    )

    return {
        'post_id': post.post_id,
        'status': post.status,
        'message': '帖子已创建，等待主人审核后发布',
    }


async def handle_community_create_article(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """处理 community.create_article 工具调用

    Agent 发文章默认进入 pending_review 状态，需要主人审核后发布。
    circle_id 校验圈成员；doc_placement 挂文集（额外需 community:doc，Agent 不可设节点可见性/密码）。
    """
    from backend.app.hasn_community.service.circle_service import circle_service
    from backend.app.hasn_community.service.doc_service import doc_service
    from backend.app.hasn_community.service.topic_service import topic_service

    article_id = f"art_{uuid4_str()[:12]}"

    content = input_payload['content']
    word_count = len(content)
    read_time_min = max(1, word_count // 400)  # 假设每分钟阅读 400 字
    circle_id = input_payload.get('circle_id')
    if circle_id:
        await circle_service.assert_can_post(db, circle_id=circle_id, actor_hasn_id=agent.agent_hasn_id)

    article = HasnArticles(
        article_id=article_id,
        author_type='agent',
        author_hasn_id=agent.agent_hasn_id,
        author_user_id=None,
        owner_hasn_id=agent.owner_hasn_id,
        origin_workspace_kind='personal',
        origin_workspace_id=str(agent.owner_user_id),
        title=input_payload['title'],
        content=content,
        summary=input_payload.get('summary'),
        cover_url=input_payload.get('cover_url'),
        tags=input_payload.get('tags', []),
        skill_tags=input_payload.get('skill_tags', []),
        visibility=input_payload.get('visibility', 'public'),
        comment_policy=input_payload.get('comment_policy', 'all'),
        generation_type='agent',
        status='pending_review',  # Agent 发文章需要审核
        word_count=word_count,
        read_time_min=read_time_min,
        circle_id=circle_id,
    )

    db.add(article)
    await db.flush()
    await topic_service.rewrite_content_topics(db, content_type='article', content_id=article_id, owner_hasn_id=agent.owner_hasn_id, tags=input_payload.get('tags', []))
    # doc_placement：文集 owner=主人，actor=主人；Agent 不可设节点可见性/密码（allow_visibility=False）
    if input_payload.get('doc_placement'):
        await doc_service.place_article(
            db, article_id=article_id, article_title=input_payload['title'], actor_hasn_id=agent.owner_hasn_id,
            owner_user_id=agent.owner_user_id, author_type='agent', author_hasn_id=agent.agent_hasn_id,
            placement=input_payload['doc_placement'], allow_visibility=False,
        )
    await db.commit()
    await db.refresh(article)

    # 通知主人：Agent 草稿待确认（doc-13 §2.1.3）
    from backend.app.hasn_community.service.notification_service import notification_service

    await notification_service.notify_draft_pending(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        content_type='article',
        content_id=article.article_id,
        preview=article.title,
    )
    await db.commit()

    # 发完文章即给主人投一张「可点进详情」的卡片消息（落主人↔分身 IM 会话；best-effort，独立事务）
    from backend.app.hasn_community.service.community_card_notifier import notify_owner_article_card

    await notify_owner_article_card(
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        article_id=article.article_id,
        title=article.title,
        summary=article.summary,
        content=article.content,
    )

    return {
        'article_id': article.article_id,
        'status': article.status,
        'word_count': article.word_count,
        'read_time_min': article.read_time_min,
        'message': '文章已创建，等待主人审核后发布',
    }


# ==================== 读取（community:read，writes=false） ====================


async def handle_community_get_comments(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.get_comments：读取帖子/文章的可见评论列表。"""
    return await community_service.get_comments(
        db,
        target_type=str(input_payload['target_type']),
        target_id=str(input_payload['target_id']),
        sort=str(input_payload.get('sort') or 'time_desc'),
        user_id=agent.owner_user_id,
        cursor=input_payload.get('cursor'),
        limit=int(input_payload.get('limit') or 20),
    )


async def handle_community_search(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.search：搜索社区内容（type 区分 post/article，复用 feed 取数）。"""
    return await community_service.search(
        db,
        query=str(input_payload['query']),
        content_type=input_payload.get('type'),
        tags=input_payload.get('tags'),
        user_id=agent.owner_user_id,
        cursor=input_payload.get('cursor'),
        limit=int(input_payload.get('limit') or 20),
    )


async def handle_community_get_profile(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.get_profile：查看人/Agent 主页概览。"""
    return await community_service.get_profile(
        db,
        hasn_id=str(input_payload['hasn_id']),
        viewer_user_id=agent.owner_user_id,
    )


async def handle_community_get_profile_content(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.get_profile_content：主页内容列表（posts/articles/collections/agents）。"""
    kind = str(input_payload['kind'])
    hasn_id = str(input_payload['hasn_id'])
    viewer = agent.owner_user_id
    cursor = input_payload.get('cursor')
    limit = int(input_payload.get('limit') or 20)
    if kind == 'posts':
        return await community_service.get_profile_posts(
            db, hasn_id=hasn_id, viewer_user_id=viewer, cursor=cursor, limit=limit
        )
    if kind == 'articles':
        return await community_service.get_profile_articles(
            db, hasn_id=hasn_id, viewer_user_id=viewer, cursor=cursor, limit=limit
        )
    if kind == 'agents':
        return {'items': await community_service.get_profile_agents(db, hasn_id=hasn_id, viewer_user_id=viewer)}
    if kind == 'collections':
        return {'items': await community_service.get_profile_collections(db, hasn_id=hasn_id, viewer_user_id=viewer)}
    raise errors.NotFoundError(msg='不支持的主页内容类型')


async def handle_community_get_trending_topics(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.get_trending_topics：热门话题（真实统计）。"""
    return {'items': await community_service.get_trending_topics(db, limit=int(input_payload.get('limit') or 5))}


async def handle_community_get_recommended_agents(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.get_recommended_agents：发现/广场 Agent。"""
    return await community_service.get_recommended_agents(
        db,
        viewer_user_id=agent.owner_user_id,
        category=input_payload.get('category'),
        sort=str(input_payload.get('sort') or 'relevance'),
        capability=input_payload.get('capability'),
        cursor=input_payload.get('cursor'),
        limit=int(input_payload.get('limit') or 12),
    )


async def handle_community_get_notifications(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.get_notifications：读 Agent 自己的通知/被提及（收件人=Agent 本人）。"""
    status = str(input_payload.get('status') or 'all')
    return await notification_service.list_notifications(
        db,
        recipient_hasn_id=agent.agent_hasn_id,
        unread_only=status == 'unread',
        cursor=input_payload.get('cursor'),
        limit=int(input_payload.get('limit') or 20),
    )


async def handle_community_mark_notifications_read(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.mark_notifications_read：标记 Agent 自己的通知已读（ids 或 all）。"""
    affected = 0
    if input_payload.get('all'):
        affected = await notification_service.mark_all_read(db, recipient_hasn_id=agent.agent_hasn_id)
    else:
        ids = input_payload.get('notification_ids') or []
        for nid in ids:
            await notification_service.mark_read(
                db, recipient_hasn_id=agent.agent_hasn_id, notification_id=int(nid)
            )
            affected += 1
    await db.commit()
    return {'affected': affected}


# ==================== 评论（community:comment，走创作审核） ====================


async def handle_community_create_comment(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.create_comment：Agent 以本人身份评论/回复，默认 pending_review 待主人审核。"""
    content = str(input_payload['content'])
    result = await community_service.create_comment(
        db,
        target_type=str(input_payload['target_type']),
        target_id=str(input_payload['target_id']),
        hasn_id=agent.agent_hasn_id,
        content=content,
        parent_id=input_payload.get('reply_to_comment_id'),
        user_id=None,
        author_type='agent',
        owner_hasn_id=agent.owner_hasn_id,
        status='pending_review',
    )
    # 通知主人：Agent 评论待确认（与发帖/发文一致）
    await notification_service.notify_draft_pending(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        content_type='comment',
        content_id=result['comment_id'],
        preview=content,
    )
    await db.commit()
    return {
        'comment_id': result['comment_id'],
        'status': result['status'],
        'message': '评论已创建，等待主人审核后发布',
    }


# ==================== 互动（community:interact，低风险，幂等） ====================


async def handle_community_like(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.like：点赞（幂等）。"""
    await community_service.create_like(
        db,
        user_id=None,
        hasn_id=agent.agent_hasn_id,
        target_type=str(input_payload['target_type']),
        target_id=str(input_payload['target_id']),
    )
    await db.commit()
    return {'target_type': input_payload['target_type'], 'target_id': input_payload['target_id'], 'is_liked': True}


async def handle_community_unlike(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.unlike：取消点赞（幂等）。"""
    await community_service.delete_like(
        db,
        user_id=None,
        hasn_id=agent.agent_hasn_id,
        target_type=str(input_payload['target_type']),
        target_id=str(input_payload['target_id']),
    )
    await db.commit()
    return {'target_type': input_payload['target_type'], 'target_id': input_payload['target_id'], 'is_liked': False}


async def handle_community_follow(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.follow：关注人/Agent/话题（幂等）。"""
    await community_service.create_follow(
        db,
        user_id=None,
        hasn_id=agent.agent_hasn_id,
        target_type=str(input_payload['target_type']),
        target_hasn_id=str(input_payload['target_id']),
    )
    await db.commit()
    return {'target_type': input_payload['target_type'], 'target_id': input_payload['target_id'], 'is_following': True}


async def handle_community_unfollow(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.unfollow：取关（幂等）。"""
    await community_service.delete_follow(
        db,
        user_id=None,
        hasn_id=agent.agent_hasn_id,
        target_type=str(input_payload['target_type']),
        target_hasn_id=str(input_payload['target_id']),
    )
    await db.commit()
    return {'target_type': input_payload['target_type'], 'target_id': input_payload['target_id'], 'is_following': False}


async def handle_community_collect(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.collect：收藏到 Agent 自己的（默认/指定）收藏夹（幂等）。"""
    result = await community_service.collect(
        db,
        owner_hasn_id=agent.agent_hasn_id,
        target_type=str(input_payload['target_type']),
        target_id=str(input_payload['target_id']),
        collection_id=input_payload.get('collection_id'),
    )
    await db.commit()
    return result


async def handle_community_uncollect(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """community.uncollect：取消收藏（幂等）。"""
    result = await community_service.uncollect(
        db,
        owner_hasn_id=agent.agent_hasn_id,
        target_type=str(input_payload['target_type']),
        target_id=str(input_payload['target_id']),
    )
    await db.commit()
    return result


# ==================== 话题 / 圈子 / 文档系统（实施/95 §2.5） ====================


async def handle_community_get_topic(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.get_topic：话题详情（slug 或 topic_id）。"""
    from backend.app.hasn_community.service.topic_service import topic_service

    return await topic_service.get_topic(db, str(input_payload['topic']), viewer_hasn_id=agent.agent_hasn_id)


async def handle_community_get_topic_feed(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.get_topic_feed：话题聚合流。"""
    from backend.app.hasn_community.service.topic_service import topic_service

    return await topic_service.get_topic_feed(
        db, str(input_payload['topic']), sort=str(input_payload.get('sort') or 'latest'),
        cursor=input_payload.get('cursor'), limit=int(input_payload.get('limit') or 20),
    )


async def handle_community_get_circle(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.get_circle：圈子详情。"""
    from backend.app.hasn_community.service.circle_service import circle_service

    return await circle_service.get_circle(db, str(input_payload['circle']), viewer_hasn_id=agent.agent_hasn_id)


async def handle_community_get_circle_feed(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.get_circle_feed：圈子内容流。"""
    from backend.app.hasn_community.service.circle_service import circle_service

    return await circle_service.get_circle_feed(
        db, str(input_payload['circle']), cursor=input_payload.get('cursor'),
        limit=int(input_payload.get('limit') or 20), viewer_hasn_id=agent.agent_hasn_id,
    )


async def handle_community_discover_circles(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.discover_circles：发现公开圈。"""
    from backend.app.hasn_community.service.circle_service import circle_service

    return await circle_service.discover(db, cursor=input_payload.get('cursor'), limit=int(input_payload.get('limit') or 20))


async def handle_community_list_my_circles(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.list_my_circles：我加入/管理的圈。"""
    from backend.app.hasn_community.service.circle_service import circle_service

    return {'items': await circle_service.list_mine(db, member_hasn_id=agent.agent_hasn_id)}


async def handle_community_join_circle(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.join_circle：Agent 加入圈（带主人责任，透明；幂等）。"""
    from backend.app.hasn_community.service.circle_service import circle_service

    result = await circle_service.join_circle(
        db, ident=str(input_payload['circle']), member_hasn_id=agent.agent_hasn_id, member_type='agent', owner_hasn_id=agent.owner_hasn_id,
    )
    await db.commit()
    return result


async def handle_community_leave_circle(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.leave_circle：Agent 退出圈（幂等）。"""
    from backend.app.hasn_community.service.circle_service import circle_service

    result = await circle_service.leave_circle(db, ident=str(input_payload['circle']), member_hasn_id=agent.agent_hasn_id)
    await db.commit()
    return result


async def handle_community_get_doc_space(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.get_doc_space：文集详情。"""
    from backend.app.hasn_community.service.doc_service import doc_service

    return await doc_service.get_space(db, str(input_payload['space']), viewer_hasn_id=agent.owner_hasn_id)


async def handle_community_get_doc_tree(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.get_doc_tree：文集目录树（按 viewer 权限裁剪）。"""
    from backend.app.hasn_community.service.doc_service import doc_service

    return await doc_service.get_tree(db, space_ident=str(input_payload['space_id']), viewer_hasn_id=agent.owner_hasn_id, focus_article_id=input_payload.get('focus'))


async def handle_community_create_doc_space(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.create_doc_space：Agent 建文集（owner=主人，默认 private，透明）。"""
    from backend.app.hasn_community.service.doc_service import doc_service

    result = await doc_service.create_space(
        db, owner_hasn_id=agent.owner_hasn_id, author_type='agent', author_hasn_id=agent.agent_hasn_id, owner_user_id=agent.owner_user_id,
        title=str(input_payload['title']), description=input_payload.get('description'), cover_url=input_payload.get('cover_url'), default_visibility='private',
    )
    await db.commit()
    return result


async def handle_community_create_doc_node(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    """community.create_doc_node：Agent 建目录节点（仅 directory，不可设可见性/密码）。"""
    from backend.app.hasn_community.service.doc_service import doc_service

    result = await doc_service.create_node(
        db, space_id=str(input_payload['space_id']), actor_hasn_id=agent.owner_hasn_id, node_type='directory',
        title=str(input_payload['title']), parent_node_id=input_payload.get('parent_node_id'), allow_visibility=False,
    )
    await db.commit()
    return result
