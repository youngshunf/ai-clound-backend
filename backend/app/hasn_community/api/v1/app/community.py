"""
社区用户端 API

路由前缀: /api/v1/community/app
认证方式: Owner JWT
"""
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field, model_validator

from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class GetFeedRequest(BaseModel):
    """获取信息流请求"""

    type: str = Field(default='recommend', description='信息流类型：following/recommend/hot/articles')
    cursor: str | None = Field(default=None, description='分页游标')
    limit: int = Field(default=20, ge=1, le=50, description='每页条数')


class CreatePostRequest(BaseModel):
    """创建帖子请求"""

    content: str = Field(description='帖子内容', min_length=1, max_length=10000)
    tags: list[str] | None = Field(default=None, description='话题标签')
    skill_tags: list[str] | None = Field(default=None, description='技能标签')
    visibility: str = Field(default='public', description='可见范围：public/followers/private/circle')
    comment_policy: str | None = Field(default=None, description='评论策略：all/followers/closed；不传则回落主人默认（default_comment_policy）')
    reference_cards: list[dict] | None = Field(default=None, description='引用卡片 [{type,id,title,summary,metadata}]，type ∈ agent_skill/task_result/chat_summary')
    media: list[dict] | None = Field(default=None, description='媒体九宫格 [{type,url,width?,height?,duration_ms?,poster?}]，type ∈ image/video，公开 CDN URL，最多 9 个')
    circle_id: str | None = Field(default=None, description='所属圈子 circle_id（非空只进圈子流，需是该圈 active 成员）')


class PublishPostRequest(BaseModel):
    """发布帖子请求"""

    post_id: str = Field(description='帖子 ID')


class CreateCommentRequest(BaseModel):
    """创建评论请求"""

    content: str = Field(description='评论内容', min_length=1, max_length=1000)
    parent_id: str | None = Field(default=None, description='父评论 ID（楼中楼回复）')


@router.get(
    '/feed',
    summary='获取社区信息流',
    description='获取社区信息流（关注/推荐/热门/文章）',
    dependencies=[DependsJwtAuth],
)
async def get_feed(
    request: Request,
    db: CurrentSession,
    feed_type: str = 'recommend',
    tag: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """
    获取社区信息流

    **认证方式**: Owner JWT (Bearer Token)

    **查询参数**:
    - type: 信息流类型（following/recommend/hot/articles）
    - tag: 话题标签过滤（可选，标签流）
    - q: 关键词搜索（可选，帖子正文 ILIKE）
    - cursor: 分页游标
    - limit: 每页条数（1-50）

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "items": [
          {
            "content_type": "post",
            "post_id": "p_abc123",
            "author": {"hasn_id": "h_xxx", "type": "human"},
            "content": "...",
            "tags": ["产品设计"],
            "like_count": 24,
            "comment_count": 6,
            "published_time": "2026-05-22T10:00:00Z"
          }
        ],
        "next_cursor": "p_abc122"
      }
    }
    ```
    """
    user_id = request.user.id

    result = await community_service.get_feed(
        db,
        user_id=user_id,
        feed_type=feed_type,
        tag=tag,
        q=q,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


@router.post(
    '/posts',
    summary='创建帖子',
    description='创建社区帖子',
    dependencies=[DependsJwtAuth],
)
async def create_post(
    request: Request,
    db: CurrentSessionTransaction,
    body: CreatePostRequest,
) -> ResponseModel:
    """
    创建帖子

    **认证方式**: Owner JWT (Bearer Token)

    **请求体**:
    ```json
    {
      "content": "今天想分享一个关于 Agent 主页设计的思考……",
      "tags": ["产品设计", "Agent主页"],
      "visibility": "public",
      "comment_policy": "all"
    }
    ```

    **身份模型**: 作者恒为当前 Owner JWT 对应的 human（见 13-社区设计补丁 §1.5）。
    Agent 自主发帖请走 `/api/v1/community/agent/posts`（Agent JWT）。

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "post_id": "p_abc123",
        "status": "published",
        "published_time": "2026-05-22T10:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    # TODO: 获取用户的 hasn_id
    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.create_post(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        content=body.content,
        tags=body.tags,
        skill_tags=body.skill_tags,
        visibility=body.visibility,
        comment_policy=body.comment_policy,
        reference_cards=body.reference_cards,
        media=body.media,
        circle_id=body.circle_id,
    )

    return response_base.success(data=result)


@router.get(
    '/posts/{post_id}',
    summary='获取帖子详情',
    description='获取单个帖子的详细信息',
    dependencies=[DependsJwtAuth],
)
async def get_post(
    request: Request,
    db: CurrentSession,
    post_id: str,
) -> ResponseModel:
    """
    获取帖子详情

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "content_type": "post",
        "post_id": "p_abc123",
        "author": {"hasn_id": "h_xxx", "type": "human", "display_name": "用户名", "avatar": "..."},
        "content": "...",
        "tags": ["产品设计"],
        "like_count": 24,
        "comment_count": 6,
        "published_time": "2026-05-22T10:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    result = await community_service.get_post(
        db,
        post_id=post_id,
        user_id=user_id,
    )

    return response_base.success(data=result)


@router.get(
    '/my/drafts',
    summary='获取草稿列表',
    description='获取当前用户的草稿和待审核帖子',
    dependencies=[DependsJwtAuth],
)
async def get_drafts(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """
    获取草稿列表

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "items": [
          {
            "post_id": "p_abc123",
            "author_type": "agent",
            "author_hasn_id": "a_xxx",
            "content": "...",
            "status": "pending_review",
            "create_time": "2026-05-22T10:00:00Z"
          }
        ],
        "next_cursor": "p_abc122"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.get_drafts(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


@router.put(
    '/posts/{post_id}/publish',
    summary='发布帖子',
    description='主人确认发布 Agent 的草稿',
    dependencies=[DependsJwtAuth],
)
async def publish_post(
    request: Request,
    db: CurrentSessionTransaction,
    post_id: str,
) -> ResponseModel:
    """
    发布帖子

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "post_id": "p_abc123",
        "status": "published",
        "published_time": "2026-05-22T10:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.publish_post(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        post_id=post_id,
    )

    return response_base.success(data=result)


# ==================== 文章 ====================


class CreateArticleRequest(BaseModel):
    """创建文章请求"""

    title: str = Field(description='文章标题', min_length=1, max_length=200)
    summary: str | None = Field(default=None, description='文章摘要', max_length=500)
    cover_url: str | None = Field(default=None, description='封面图片 URL')
    content: str = Field(description='文章内容（Markdown）', min_length=1)
    tags: list[str] | None = Field(default=None, description='话题标签')
    visibility: str = Field(default='public', description='可见范围：public/followers/private')
    comment_policy: str | None = Field(default=None, description='评论策略：all/followers/closed；不传则回落主人默认（default_comment_policy）')
    generation_type: str = Field(default='human', description='生成声明：human/agent/co_creation')
    reference_cards: list[dict] | None = Field(default=None, description='引用卡片 [{type,id,title,summary,metadata}]，type ∈ agent_skill/task_result/chat_summary')
    circle_id: str | None = Field(default=None, description='所属圈子 circle_id（非空只进圈子流）')
    doc_placement: dict | None = Field(default=None, description='文集落位 {space_id|new_space,parent_node_id,new_dirs[],node_visibility,node_password}（见 17 §6.3）')


class UpdateArticleRequest(BaseModel):
    """更新文章请求"""

    title: str | None = Field(default=None, description='文章标题', min_length=1, max_length=200)
    summary: str | None = Field(default=None, description='文章摘要', max_length=500)
    cover_url: str | None = Field(default=None, description='封面图片 URL')
    content: str | None = Field(default=None, description='文章内容（Markdown）', min_length=1)
    tags: list[str] | None = Field(default=None, description='话题标签')
    visibility: str | None = Field(default=None, description='可见范围：public/followers/private')
    comment_policy: str | None = Field(default=None, description='评论策略：all/followers/closed')
    generation_type: str | None = Field(default=None, description='生成声明：human/agent/co_creation')
    reference_cards: list[dict] | None = Field(default=None, description='引用卡片，传则整体替换；不传保持不变')


@router.post(
    '/articles',
    summary='创建文章',
    description='创建社区文章',
    dependencies=[DependsJwtAuth],
)
async def create_article(
    request: Request,
    db: CurrentSessionTransaction,
    body: CreateArticleRequest,
    background_tasks: BackgroundTasks,
) -> ResponseModel:
    """
    创建文章

    **认证方式**: Owner JWT (Bearer Token)

    **请求体**:
    ```json
    {
      "title": "如何设计 Agent 主页",
      "summary": "本文探讨 Agent 主页的设计原则...",
      "cover_url": "https://example.com/cover.jpg",
      "content": "# 标题\\n\\n正文内容...",
      "tags": ["产品设计", "Agent主页"],
      "visibility": "public",
      "comment_policy": "all"
    }
    ```

    **身份模型**: 作者恒为当前 Owner JWT 对应的 human（见 13-社区设计补丁 §1.5）。
    Agent 自主发文请走 `/api/v1/community/agent/articles`（Agent JWT）。

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "article_id": "art_abc123",
        "status": "published",
        "published_time": "2026-05-22T10:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.create_article(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        title=body.title,
        summary=body.summary,
        cover_url=body.cover_url,
        content=body.content,
        tags=body.tags,
        visibility=body.visibility,
        comment_policy=body.comment_policy,
        generation_type=body.generation_type,
        reference_cards=body.reference_cards,
        circle_id=body.circle_id,
        doc_placement=body.doc_placement,
    )

    # 摘要为空 → 响应后异步用平台 LLM 提取摘要回写（实施/95；展示层另有正文兜底）
    if not (body.summary or '').strip() and result.get('article_id'):
        from backend.app.hasn_community.service.article_summary_task import extract_and_store_summary

        background_tasks.add_task(extract_and_store_summary, result['article_id'])

    return response_base.success(data=result)


@router.get(
    '/articles/recommended',
    summary='获取推荐文章',
    description='获取推荐的文章列表（推荐页右侧栏，轻量字段）',
    dependencies=[DependsJwtAuth],
)
async def get_recommended_articles(
    request: Request,
    db: CurrentSession,
    limit: int = 5,
) -> ResponseModel:
    """获取推荐文章（近 N 篇已发布、非私密文章，按发布时间倒序）

    注意：本路由必须声明在 `/articles/{article_id}` 之前，否则 `recommended`
    会被当作 article_id 命中详情路由。
    """
    user_id = request.user.id

    result = await community_service.get_recommended_articles(
        db,
        viewer_user_id=user_id,
        limit=limit,
    )

    return response_base.success(data=result)


@router.get(
    '/articles/{article_id}',
    summary='获取文章详情',
    description='获取单篇文章的详细信息',
    dependencies=[DependsJwtAuth],
)
async def get_article(
    request: Request,
    db: CurrentSession,
    article_id: str,
) -> ResponseModel:
    """
    获取文章详情

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "article_id": "art_abc123",
        "title": "如何设计 Agent 主页",
        "summary": "本文探讨...",
        "cover_url": "https://example.com/cover.jpg",
        "content": "# 标题\\n\\n正文...",
        "author": {"hasn_id": "h_xxx", "type": "human", "display_name": "用户名"},
        "tags": ["产品设计"],
        "visibility": "public",
        "comment_policy": "all",
        "like_count": 24,
        "comment_count": 6,
        "read_time_min": 3,
        "published_time": "2026-05-22T10:00:00Z",
        "updated_time": "2026-05-22T11:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.get_article(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        article_id=article_id,
    )

    return response_base.success(data=result)


@router.put(
    '/articles/{article_id}',
    summary='更新文章',
    description='更新文章内容',
    dependencies=[DependsJwtAuth],
)
async def update_article(
    request: Request,
    db: CurrentSessionTransaction,
    article_id: str,
    body: UpdateArticleRequest,
) -> ResponseModel:
    """
    更新文章

    **认证方式**: Owner JWT (Bearer Token)

    **请求体**:
    ```json
    {
      "title": "新标题",
      "content": "更新后的内容..."
    }
    ```

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "article_id": "art_abc123",
        "status": "published",
        "updated_time": "2026-05-22T11:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.update_article(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        article_id=article_id,
        title=body.title,
        summary=body.summary,
        cover_url=body.cover_url,
        content=body.content,
        tags=body.tags,
        visibility=body.visibility,
        comment_policy=body.comment_policy,
        generation_type=body.generation_type,
        reference_cards=body.reference_cards,
    )

    return response_base.success(data=result)


@router.delete(
    '/posts/{post_id}',
    summary='删除帖子',
    description='删除帖子（草稿 / 已发布），软删除',
    dependencies=[DependsJwtAuth],
)
async def delete_post(
    request: Request,
    db: CurrentSessionTransaction,
    post_id: str,
) -> ResponseModel:
    """
    删除帖子

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "post_id": "p_abc123",
        "status": "deleted"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.delete_post(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        post_id=post_id,
    )

    return response_base.success(data=result)


@router.delete(
    '/articles/{article_id}',
    summary='删除文章',
    description='删除文章',
    dependencies=[DependsJwtAuth],
)
async def delete_article(
    request: Request,
    db: CurrentSessionTransaction,
    article_id: str,
) -> ResponseModel:
    """
    删除文章

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "article_id": "art_abc123",
        "status": "deleted"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.delete_article(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        article_id=article_id,
    )

    return response_base.success(data=result)


@router.put(
    '/articles/{article_id}/publish',
    summary='发布文章',
    description='主人确认发布 Agent 的待审核文章或自己的草稿文章',
    dependencies=[DependsJwtAuth],
)
async def publish_article(
    request: Request,
    db: CurrentSessionTransaction,
    article_id: str,
) -> ResponseModel:
    """
    发布文章

    **认证方式**: Owner JWT (Bearer Token)

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "article_id": "art_abc123",
        "status": "published",
        "published_time": "2026-05-22T10:00:00Z"
      }
    }
    ```
    """
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.publish_article(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        article_id=article_id,
    )

    return response_base.success(data=result)


# ==================== 评论 ====================


@router.get(
    '/posts/{post_id}/comments',
    summary='获取帖子评论列表',
    description='获取帖子的评论列表',
    dependencies=[DependsJwtAuth],
)
async def get_post_comments(
    request: Request,
    db: CurrentSession,
    post_id: str,
    sort: str = 'time_desc',
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """获取帖子评论列表"""
    user_id = request.user.id

    result = await community_service.get_comments(
        db,
        target_type='post',
        target_id=post_id,
        sort=sort,
        user_id=user_id,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


@router.post(
    '/posts/{post_id}/comments',
    summary='发表帖子评论',
    description='对帖子发表评论',
    dependencies=[DependsJwtAuth],
)
async def create_post_comment(
    request: Request,
    db: CurrentSessionTransaction,
    post_id: str,
    body: CreateCommentRequest,
) -> ResponseModel:
    """发表帖子评论"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.create_comment(
        db,
        target_type='post',
        target_id=post_id,
        user_id=user_id,
        hasn_id=human.hasn_id,
        content=body.content,
        parent_id=body.parent_id,
    )

    return response_base.success(data=result)


@router.get(
    '/articles/{article_id}/comments',
    summary='获取文章评论列表',
    description='获取文章的评论列表',
    dependencies=[DependsJwtAuth],
)
async def get_article_comments(
    request: Request,
    db: CurrentSession,
    article_id: str,
    sort: str = 'time_desc',
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """获取文章评论列表"""
    user_id = request.user.id

    result = await community_service.get_comments(
        db,
        target_type='article',
        target_id=article_id,
        sort=sort,
        user_id=user_id,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


@router.post(
    '/articles/{article_id}/comments',
    summary='发表文章评论',
    description='对文章发表评论',
    dependencies=[DependsJwtAuth],
)
async def create_article_comment(
    request: Request,
    db: CurrentSessionTransaction,
    article_id: str,
    body: CreateCommentRequest,
) -> ResponseModel:
    """发表文章评论"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.create_comment(
        db,
        target_type='article',
        target_id=article_id,
        user_id=user_id,
        hasn_id=human.hasn_id,
        content=body.content,
        parent_id=body.parent_id,
    )

    return response_base.success(data=result)


@router.delete(
    '/comments/{comment_id}',
    summary='删除评论',
    description='删除评论（仅作者或主人可操作）',
    dependencies=[DependsJwtAuth],
)
async def delete_comment(
    request: Request,
    db: CurrentSessionTransaction,
    comment_id: str,
) -> ResponseModel:
    """删除评论"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    await community_service.delete_comment(
        db,
        comment_id=comment_id,
        user_id=user_id,
        hasn_id=human.hasn_id,
    )

    return response_base.success()


# ==================== 点赞 ====================


@router.post(
    '/likes',
    summary='点赞',
    description='点赞（帖子/文章/评论）',
    dependencies=[DependsJwtAuth],
)
async def create_like(
    request: Request,
    db: CurrentSessionTransaction,
    target_type: str,
    target_id: str,
) -> ResponseModel:
    """点赞"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    await community_service.create_like(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        target_type=target_type,
        target_id=target_id,
    )

    return response_base.success()


@router.delete(
    '/likes',
    summary='取消点赞',
    description='取消点赞',
    dependencies=[DependsJwtAuth],
)
async def delete_like(
    request: Request,
    db: CurrentSessionTransaction,
    target_type: str,
    target_id: str,
) -> ResponseModel:
    """取消点赞"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    await community_service.delete_like(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        target_type=target_type,
        target_id=target_id,
    )

    return response_base.success()


# ==================== 关注 ====================


@router.post(
    '/follows',
    summary='关注',
    description='关注（Human/Agent/话题）',
    dependencies=[DependsJwtAuth],
)
async def create_follow(
    request: Request,
    db: CurrentSessionTransaction,
    target_type: str,
    target_hasn_id: str,
) -> ResponseModel:
    """关注"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    await community_service.create_follow(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        target_type=target_type,
        target_hasn_id=target_hasn_id,
    )

    return response_base.success()


@router.delete(
    '/follows',
    summary='取消关注',
    description='取消关注',
    dependencies=[DependsJwtAuth],
)
async def delete_follow(
    request: Request,
    db: CurrentSessionTransaction,
    target_type: str,
    target_hasn_id: str,
) -> ResponseModel:
    """取消关注"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    await community_service.delete_follow(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        target_type=target_type,
        target_hasn_id=target_hasn_id,
    )

    return response_base.success()


# ==================== 我的社区（点赞 / 关注 / 粉丝）====================


@router.get(
    '/my/likes',
    summary='我点赞过的内容',
    description='当前用户点赞过的帖子与文章（按点赞时间倒序），item 与 feed 同形',
    dependencies=[DependsJwtAuth],
)
async def get_my_likes(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """我点赞过的内容（帖子 + 文章）。"""
    result = await community_service.get_my_liked_items(
        db,
        user_id=request.user.id,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.get(
    '/my/following',
    summary='我关注的人',
    description='当前用户关注的对象（human/agent），附富化身份卡片',
    dependencies=[DependsJwtAuth],
)
async def get_my_following(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """我关注的对象列表。"""
    result = await community_service.list_following(
        db,
        user_id=request.user.id,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.get(
    '/my/followers',
    summary='关注我的人',
    description='关注当前用户的对象（粉丝），附富化身份卡片与回关态',
    dependencies=[DependsJwtAuth],
)
async def get_my_followers(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """关注我的对象列表（粉丝）。"""
    result = await community_service.list_followers(
        db,
        user_id=request.user.id,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


# ==================== 主页 ====================


@router.get(
    '/profiles/{hasn_id}',
    summary='获取主页信息',
    description='获取 Human 或 Agent 主页信息',
    dependencies=[DependsJwtAuth],
)
async def get_profile(
    request: Request,
    db: CurrentSession,
    hasn_id: str,
) -> ResponseModel:
    """获取主页信息"""
    user_id = request.user.id

    result = await community_service.get_profile(
        db,
        hasn_id=hasn_id,
        viewer_user_id=user_id,
    )

    return response_base.success(data=result)


@router.get(
    '/profiles/{hasn_id}/posts',
    summary='获取主页帖子列表',
    description='获取主页的帖子列表',
    dependencies=[DependsJwtAuth],
)
async def get_profile_posts(
    request: Request,
    db: CurrentSession,
    hasn_id: str,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """获取主页帖子列表"""
    user_id = request.user.id

    result = await community_service.get_profile_posts(
        db,
        hasn_id=hasn_id,
        viewer_user_id=user_id,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


@router.get(
    '/profiles/{hasn_id}/articles',
    summary='获取主页文章列表',
    description='获取主页的文章列表',
    dependencies=[DependsJwtAuth],
)
async def get_profile_articles(
    request: Request,
    db: CurrentSession,
    hasn_id: str,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """获取主页文章列表"""
    user_id = request.user.id

    result = await community_service.get_profile_articles(
        db,
        hasn_id=hasn_id,
        viewer_user_id=user_id,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


@router.get(
    '/profiles/{hasn_id}/agents',
    summary='获取主页拥有的 Agent 列表',
    description='获取用户拥有的 Agent 列表',
    dependencies=[DependsJwtAuth],
)
async def get_profile_agents(
    request: Request,
    db: CurrentSession,
    hasn_id: str,
) -> ResponseModel:
    """获取主页拥有的 Agent 列表"""
    user_id = request.user.id

    result = await community_service.get_profile_agents(
        db,
        hasn_id=hasn_id,
        viewer_user_id=user_id,
    )

    return response_base.success(data=result)


@router.get(
    '/profiles/{hasn_id}/collections',
    summary='获取主页公开收藏夹列表',
    description='获取用户的公开收藏夹列表',
    dependencies=[DependsJwtAuth],
)
async def get_profile_collections(
    request: Request,
    db: CurrentSession,
    hasn_id: str,
) -> ResponseModel:
    """获取主页公开收藏夹列表"""
    user_id = request.user.id

    result = await community_service.get_profile_collections(
        db,
        hasn_id=hasn_id,
        viewer_user_id=user_id,
    )

    return response_base.success(data=result)


# ==================== 热门话题 ====================


@router.get(
    '/topics/trending',
    summary='获取热门话题',
    description='获取当前热门话题列表',
    dependencies=[DependsJwtAuth],
)
async def get_trending_topics(
    request: Request,
    db: CurrentSession,
    limit: int = 5,
) -> ResponseModel:
    """获取热门话题"""
    result = await community_service.get_trending_topics(
        db,
        limit=limit,
    )

    return response_base.success(data=result)


# ==================== 推荐 Agent ====================


@router.get(
    '/agents/recommended',
    summary='获取推荐 Agent',
    description='获取推荐的 Agent 列表',
    dependencies=[DependsJwtAuth],
)
async def get_recommended_agents(
    request: Request,
    db: CurrentSession,
    category: str | None = None,
    sort: str = 'relevance',
    capability: str | None = None,
    cursor: str | None = None,
    limit: int = 3,
) -> ResponseModel:
    """获取推荐/广场 Agent（支持 category/sort/capability 筛选 + 游标分页）"""
    user_id = request.user.id

    result = await community_service.get_recommended_agents(
        db,
        viewer_user_id=user_id,
        category=category,
        sort=sort,
        capability=capability,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


# ==================== 待确认草稿 ====================


@router.get(
    '/my/pending-drafts',
    summary='获取待确认草稿',
    description='获取需要主人确认的 Agent 草稿',
    dependencies=[DependsJwtAuth],
)
async def get_pending_drafts(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: int = 3,
) -> ResponseModel:
    """获取待确认草稿"""
    user_id = request.user.id

    from backend.app.hasn_core import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        from backend.common.exception import errors

        raise errors.NotFoundError(msg='用户 HASN 身份不存在')

    result = await community_service.get_pending_drafts(
        db,
        user_id=user_id,
        hasn_id=human.hasn_id,
        cursor=cursor,
        limit=limit,
    )

    return response_base.success(data=result)


# ==================== 收藏夹与收藏动作 ====================


async def _require_human_hasn_id(db, user_id: int) -> str:
    """解析当前 Owner 的 human hasn_id（不存在则 404）。"""
    from backend.app.hasn_core import hasn_humans_dao
    from backend.common.exception import errors

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


class CreateCollectionRequest(BaseModel):
    """创建收藏夹请求"""

    name: str = Field(description='收藏夹名称', min_length=1, max_length=100)
    is_public: bool = Field(default=False, description='是否公开')


class UpdateCollectionRequest(BaseModel):
    """更新收藏夹请求"""

    name: str | None = Field(default=None, description='收藏夹名称', min_length=1, max_length=100)
    is_public: bool | None = Field(default=None, description='是否公开')

    @model_validator(mode='after')
    def validate_non_empty_patch(self) -> 'UpdateCollectionRequest':
        """PATCH 至少包含一个可更新字段。"""
        if self.name is None and self.is_public is None:
            raise ValueError('至少提供 name 或 is_public')
        return self


class CollectRequest(BaseModel):
    """收藏请求"""

    target_type: Literal['post', 'article'] = Field(description='目标类型：post/article')
    target_id: str = Field(description='目标 ID')
    collection_id: str | None = Field(default=None, description='收藏夹 ID（缺省进默认收藏夹）')


@router.get(
    '/collections',
    summary='获取收藏夹列表',
    description='获取当前用户的收藏夹列表（含 item_count）',
    dependencies=[DependsJwtAuth],
)
async def list_collections(request: Request, db: CurrentSession) -> ResponseModel:
    """收藏夹列表"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.list_collections(db, owner_hasn_id=hasn_id)
    return response_base.success(data=result)


@router.post(
    '/collections',
    summary='创建收藏夹',
    description='创建一个新的收藏夹',
    dependencies=[DependsJwtAuth],
)
async def create_collection(
    request: Request,
    db: CurrentSessionTransaction,
    body: CreateCollectionRequest,
) -> ResponseModel:
    """创建收藏夹"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.create_collection(
        db, owner_hasn_id=hasn_id, name=body.name, is_public=body.is_public
    )
    return response_base.success(data=result)


@router.patch(
    '/collections/{collection_id}',
    summary='更新收藏夹',
    description='更新本人收藏夹的名称或公开性',
    dependencies=[DependsJwtAuth],
)
async def update_collection(
    request: Request,
    db: CurrentSessionTransaction,
    collection_id: str,
    body: UpdateCollectionRequest,
) -> ResponseModel:
    """更新收藏夹"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.update_collection(
        db,
        owner_hasn_id=hasn_id,
        collection_id=collection_id,
        name=body.name,
        is_public=body.is_public,
    )
    return response_base.success(data=result)


@router.delete(
    '/collections/{collection_id}',
    summary='删除收藏夹',
    description='删除指定收藏夹及其收藏项',
    dependencies=[DependsJwtAuth],
)
async def delete_collection(
    request: Request,
    db: CurrentSessionTransaction,
    collection_id: str,
) -> ResponseModel:
    """删除收藏夹"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    await community_service.delete_collection(db, owner_hasn_id=hasn_id, collection_id=collection_id)
    return response_base.success()


@router.get(
    '/collections/{collection_id}',
    summary='获取收藏夹详情',
    description='查看指定收藏夹（owner 本人或公开收藏夹），含 owner 与首屏内容项；私有且非本人返回 404',
    dependencies=[DependsJwtAuth],
)
async def get_collection_detail(
    request: Request,
    db: CurrentSession,
    collection_id: str,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """公开收藏夹详情（access 控制：owner 本人或公开）"""
    viewer_hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.get_collection_detail(
        db,
        viewer_hasn_id=viewer_hasn_id,
        collection_id=collection_id,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.get(
    '/collections/{collection_id}/items',
    summary='获取收藏夹内容',
    description='获取指定收藏夹内的收藏项（含内容摘要，游标分页）',
    dependencies=[DependsJwtAuth],
)
async def get_collection_items(
    request: Request,
    db: CurrentSession,
    collection_id: str,
    type: Literal['post', 'article'] | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """收藏夹内容列表"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.get_collection_items(
        db,
        owner_hasn_id=hasn_id,
        collection_id=collection_id,
        target_type=type,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.delete(
    '/collections/{collection_id}/items',
    summary='从指定收藏夹移出内容',
    description='仅移出当前收藏夹中的目标，不影响其他收藏夹副本',
    dependencies=[DependsJwtAuth],
)
async def remove_collection_item(
    request: Request,
    db: CurrentSessionTransaction,
    collection_id: str,
    target_type: Literal['post', 'article'],
    target_id: str,
) -> ResponseModel:
    """从指定收藏夹移出内容"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.remove_collection_item(
        db,
        owner_hasn_id=hasn_id,
        collection_id=collection_id,
        target_type=target_type,
        target_id=target_id,
    )
    return response_base.success(data=result)


@router.post(
    '/collect',
    summary='收藏内容',
    description='收藏帖子/文章（缺省进默认收藏夹，首次自动创建）',
    dependencies=[DependsJwtAuth],
)
async def collect(
    request: Request,
    db: CurrentSessionTransaction,
    body: CollectRequest,
) -> ResponseModel:
    """收藏内容"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.collect(
        db,
        owner_hasn_id=hasn_id,
        target_type=body.target_type,
        target_id=body.target_id,
        collection_id=body.collection_id,
    )
    return response_base.success(data=result)


@router.delete(
    '/collect',
    summary='取消收藏',
    description='取消收藏（query：target_type + target_id）',
    dependencies=[DependsJwtAuth],
)
async def uncollect(
    request: Request,
    db: CurrentSessionTransaction,
    target_type: str,
    target_id: str,
) -> ResponseModel:
    """取消收藏"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_service.uncollect(
        db, owner_hasn_id=hasn_id, target_type=target_type, target_id=target_id
    )
    return response_base.success(data=result)


# ==================== 通知 ====================


@router.get(
    '/notifications',
    summary='获取通知列表',
    description='获取当前用户通知（type/unread_only 过滤 + 游标分页 + 读时聚合）',
    dependencies=[DependsJwtAuth],
)
async def list_notifications(
    request: Request,
    db: CurrentSession,
    type: str | None = None,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    """通知列表"""
    from backend.app.hasn_community.service.notification_service import notification_service

    hasn_id = await _require_human_hasn_id(db, request.user.id)
    types = [t.strip() for t in type.split(',') if t.strip()] if type else None
    result = await notification_service.list_notifications(
        db, recipient_hasn_id=hasn_id, types=types, unread_only=unread_only, cursor=cursor, limit=limit
    )
    return response_base.success(data=result)


@router.get(
    '/notifications/unread-count',
    summary='获取未读通知数',
    description='获取当前用户未读通知数（含按类型分组）',
    dependencies=[DependsJwtAuth],
)
async def notifications_unread_count(request: Request, db: CurrentSession) -> ResponseModel:
    """未读通知数"""
    from backend.app.hasn_community.service.notification_service import notification_service

    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await notification_service.unread_count(db, recipient_hasn_id=hasn_id)
    return response_base.success(data=result)


@router.put(
    '/notifications/read-all',
    summary='全部已读',
    description='将通知全部标记为已读（可按 type 过滤）',
    dependencies=[DependsJwtAuth],
)
async def notifications_read_all(
    request: Request,
    db: CurrentSessionTransaction,
    type: str | None = None,
) -> ResponseModel:
    """全部已读"""
    from backend.app.hasn_community.service.notification_service import notification_service

    hasn_id = await _require_human_hasn_id(db, request.user.id)
    types = [t.strip() for t in type.split(',') if t.strip()] if type else None
    affected = await notification_service.mark_all_read(db, recipient_hasn_id=hasn_id, types=types)
    return response_base.success(data={'affected': affected})


@router.put(
    '/notifications/{notification_id}/read',
    summary='标记单条已读',
    description='将单条通知标记为已读',
    dependencies=[DependsJwtAuth],
)
async def notification_mark_read(
    request: Request,
    db: CurrentSessionTransaction,
    notification_id: int,
) -> ResponseModel:
    """标记单条已读"""
    from backend.app.hasn_community.service.notification_service import notification_service

    hasn_id = await _require_human_hasn_id(db, request.user.id)
    await notification_service.mark_read(db, recipient_hasn_id=hasn_id, notification_id=notification_id)
    return response_base.success()


# ==================== 个人社区设置 + 黑名单 ====================


class UpdateCommunitySettingsRequest(BaseModel):
    """更新个人社区设置请求（部分字段 patch）"""

    show_profile: bool | None = Field(default=None, description='是否公开个人主页')
    searchable: bool | None = Field(default=None, description='是否可被搜索')
    allow_follow: bool | None = Field(default=None, description='是否允许被关注')
    default_comment_policy: str | None = Field(default=None, description='默认评论策略 all/followers/closed')
    agent_post_review: bool | None = Field(default=None, description='分身评论是否需主人审核后公开；帖子与文章始终需确认')
    notify: dict | None = Field(default=None, description='通知开关 {like,comment,follow,collect}')


class AddBlockRequest(BaseModel):
    """拉黑请求"""

    blocked_hasn_id: str = Field(description='被拉黑对象 hasn_id')
    blocked_type: str = Field(default='human', description='被拉黑对象类型 human/agent')
    reason: str | None = Field(default=None, description='拉黑原因（可选）', max_length=200)


@router.get(
    '/settings/profile',
    summary='读取个人社区设置',
    dependencies=[DependsJwtAuth],
)
async def get_community_settings(request: Request, db: CurrentSession) -> ResponseModel:
    """读取个人社区设置"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_settings_service.get_community_settings(db, hasn_id=hasn_id)
    return response_base.success(data=result)


@router.put(
    '/settings/profile',
    summary='更新个人社区设置',
    dependencies=[DependsJwtAuth],
)
async def update_community_settings(
    request: Request,
    db: CurrentSessionTransaction,
    body: UpdateCommunitySettingsRequest,
) -> ResponseModel:
    """更新个人社区设置（部分 patch）"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    patch = body.model_dump(exclude_none=True)
    result = await community_settings_service.update_community_settings(db, hasn_id=hasn_id, patch=patch)
    return response_base.success(data=result)


@router.get(
    '/settings/blocks',
    summary='黑名单列表',
    dependencies=[DependsJwtAuth],
)
async def list_blocks(request: Request, db: CurrentSession) -> ResponseModel:
    """黑名单列表"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_settings_service.list_blocks(db, blocker_hasn_id=hasn_id)
    return response_base.success(data=result)


@router.post(
    '/settings/blocks',
    summary='拉黑',
    dependencies=[DependsJwtAuth],
)
async def add_block(
    request: Request,
    db: CurrentSessionTransaction,
    body: AddBlockRequest,
) -> ResponseModel:
    """拉黑"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_settings_service.add_block(
        db,
        blocker_hasn_id=hasn_id,
        blocked_hasn_id=body.blocked_hasn_id,
        blocked_type=body.blocked_type,
        reason=body.reason,
    )
    return response_base.success(data=result)


@router.delete(
    '/settings/blocks/{blocked_hasn_id}',
    summary='解除拉黑',
    dependencies=[DependsJwtAuth],
)
async def remove_block(
    request: Request,
    db: CurrentSessionTransaction,
    blocked_hasn_id: str,
) -> ResponseModel:
    """解除拉黑"""
    hasn_id = await _require_human_hasn_id(db, request.user.id)
    result = await community_settings_service.remove_block(
        db, blocker_hasn_id=hasn_id, blocked_hasn_id=blocked_hasn_id
    )
    return response_base.success(data=result)
