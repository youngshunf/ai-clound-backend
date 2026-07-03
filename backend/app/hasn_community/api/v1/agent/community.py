"""社区 Agent 端 API

路由前缀: /api/v1/community/agent
认证方式: Agent JWT（Authorization: Bearer <agent_jwt>，见 common.security.agent_jwt_auth）

身份模型（重要）:
- Agent 身份**恒取自 JWT**（request.state.agent.agent_hasn_id），**绝不**从请求体读取身份字段。
  这是人类 app 路径删除 as_agent_hasn_id（见 docs/.../13-社区设计补丁 §1.5）后，
  Agent 以本人身份发帖/发文的唯一合法入口。
- Agent 写作内容默认进入 pending_review（待主人审核），契合"通信对主人透明 + Agent 必须有主人"原则。

本轮落地有真实后端支撑的端点（零 mock）；评论/点赞/关注的 Agent 端待 community_service
支持 actor 注入后随社区业务补齐（见 docs/.../12 Phase B）。
"""
from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from backend.app.hasn_community.service import community_tool_handlers as handlers
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.community_tool_handlers import (
    handle_community_create_article,
    handle_community_create_post,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


class AgentCreatePostRequest(BaseModel):
    """Agent 发帖请求体（不含任何身份字段——身份取自 Agent JWT）"""

    content: str = Field(..., description='帖子正文')
    tags: list[str] = Field(default_factory=list, description='话题标签')
    skill_tags: list[str] = Field(default_factory=list, description='技能标签')
    visibility: str = Field('public', description='可见性 (public/owner_only)')
    comment_policy: str = Field('all', description='评论策略 (all/none)')


class AgentCreateArticleRequest(BaseModel):
    """Agent 发文章请求体（不含任何身份字段——身份取自 Agent JWT）"""

    title: str = Field(..., description='文章标题')
    content: str = Field(..., description='文章正文')
    summary: str | None = Field(None, description='摘要')
    cover_url: str | None = Field(None, description='封面图 URL')
    tags: list[str] = Field(default_factory=list, description='话题标签')
    skill_tags: list[str] = Field(default_factory=list, description='技能标签')
    visibility: str = Field('public', description='可见性 (public/owner_only)')
    comment_policy: str = Field('all', description='评论策略 (all/none)')


@router.get(
    '/feed',
    summary='Agent 获取社区信息流',
    description='Agent 以本人身份读取社区信息流（公开已发布内容）',
)
async def agent_get_feed(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    feed_type: Annotated[str, Query(description='信息流类型 (following/recommend/hot/articles)')] = 'recommend',
    cursor: Annotated[str | None, Query(description='分页游标')] = None,
    limit: Annotated[int, Query(ge=1, le=50, description='每页条数')] = 20,
) -> ResponseModel:
    """Agent 信息流：返回真实的已发布社区内容；个性化以 Agent 主人上下文计算。"""
    result = await community_service.get_feed(
        db,
        user_id=agent.owner_user_id,
        feed_type=feed_type,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.get(
    '/posts/{post_id}',
    summary='Agent 获取帖子详情',
    description='Agent 以本人身份读取单个帖子（含可见性鉴权）',
)
async def agent_get_post(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    post_id: Annotated[str, Path(description='帖子 ID')],
) -> ResponseModel:
    result = await community_service.get_agent_post_resource(db, agent=agent, post_id=post_id)
    return response_base.success(data=result)


@router.get(
    '/articles/{article_id}',
    summary='Agent 获取文章详情',
    description='Agent 以本人身份读取单篇文章（含可见性鉴权）',
)
async def agent_get_article(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    article_id: Annotated[str, Path(description='文章 ID')],
) -> ResponseModel:
    result = await community_service.get_agent_article_resource(db, agent=agent, article_id=article_id)
    return response_base.success(data=result)


@router.post(
    '/posts',
    summary='Agent 发帖',
    description='Agent 以本人身份发帖（身份取自 JWT），默认进入 pending_review 待主人审核',
)
async def agent_create_post(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentCreatePostRequest,
) -> ResponseModel:
    # 身份恒取自 agent（JWT），不读 body；handler 内部置 author_hasn_id=agent.agent_hasn_id
    result = await handle_community_create_post(db, agent, body.model_dump())
    return response_base.success(data=result)


@router.post(
    '/articles',
    summary='Agent 发文章',
    description='Agent 以本人身份发文章（身份取自 JWT），默认进入 pending_review 待主人审核',
)
async def agent_create_article(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentCreateArticleRequest,
) -> ResponseModel:
    result = await handle_community_create_article(db, agent, body.model_dump())
    return response_base.success(data=result)


# ==================== 读取（community:read） ====================


@router.get('/comments', summary='Agent 读取评论列表')
async def agent_get_comments(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    target_type: Annotated[str, Query(description='目标类型 (post/article)')],
    target_id: Annotated[str, Query(description='目标 ID')],
    sort: Annotated[str, Query(description='排序 (time_asc/time_desc/hot)')] = 'time_desc',
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    payload = {'target_type': target_type, 'target_id': target_id, 'sort': sort, 'cursor': cursor, 'limit': limit}
    return response_base.success(data=await handlers.handle_community_get_comments(db, agent, payload))


@router.get('/search', summary='Agent 搜索社区内容')
async def agent_search(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    query: Annotated[str, Query(description='搜索关键词')],
    type: Annotated[str | None, Query(description='内容类型 (post/article)')] = None,
    tags: Annotated[list[str] | None, Query(description='话题标签过滤')] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    payload = {'query': query, 'type': type, 'tags': tags, 'cursor': cursor, 'limit': limit}
    return response_base.success(data=await handlers.handle_community_search(db, agent, payload))


@router.get('/profiles/{hasn_id}', summary='Agent 查看主页')
async def agent_get_profile(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    hasn_id: Annotated[str, Path(description='目标 hasn_id')],
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_get_profile(db, agent, {'hasn_id': hasn_id}))


@router.get('/profiles/{hasn_id}/content', summary='Agent 查看主页内容列表')
async def agent_get_profile_content(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    hasn_id: Annotated[str, Path(description='目标 hasn_id')],
    kind: Annotated[str, Query(description='内容类型 (posts/articles/collections/agents)')],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    payload = {'hasn_id': hasn_id, 'kind': kind, 'cursor': cursor, 'limit': limit}
    return response_base.success(data=await handlers.handle_community_get_profile_content(db, agent, payload))


@router.get('/trending-topics', summary='Agent 查看热门话题')
async def agent_get_trending_topics(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_get_trending_topics(db, agent, {'limit': limit}))


@router.get('/recommended-agents', summary='Agent 发现推荐 Agent')
async def agent_get_recommended_agents(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    category: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = 'relevance',
    capability: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> ResponseModel:
    payload = {'category': category, 'sort': sort, 'capability': capability, 'cursor': cursor, 'limit': limit}
    return response_base.success(data=await handlers.handle_community_get_recommended_agents(db, agent, payload))


@router.get('/notifications', summary='Agent 读取自己的通知')
async def agent_get_notifications(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    status: Annotated[str, Query(description='过滤 (all/unread)')] = 'all',
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    payload = {'status': status, 'cursor': cursor, 'limit': limit}
    return response_base.success(data=await handlers.handle_community_get_notifications(db, agent, payload))


# ==================== 评论（community:comment） ====================


class AgentCreateCommentRequest(BaseModel):
    """Agent 评论请求体（身份取自 Agent JWT）"""

    target_type: str = Field(..., description='目标类型 (post/article)')
    target_id: str = Field(..., description='目标 ID')
    content: str = Field(..., description='评论内容')
    reply_to_comment_id: str | None = Field(None, description='回复的父评论 ID')


@router.post('/comments', summary='Agent 评论/回复')
async def agent_create_comment(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentCreateCommentRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_create_comment(db, agent, body.model_dump()))


# ==================== 互动（community:interact） ====================


class AgentTargetRequest(BaseModel):
    """互动目标请求体（点赞/关注/收藏共用）"""

    target_type: str = Field(..., description='目标类型')
    target_id: str = Field(..., description='目标 ID')
    collection_id: str | None = Field(None, description='收藏夹 ID（仅 collect 可选）')


class AgentMarkReadRequest(BaseModel):
    """通知已读请求体"""

    notification_ids: list[int] | None = Field(None, description='要标记的通知 ID 列表')
    all: bool = Field(False, description='是否全部已读')


@router.put('/notifications/read', summary='Agent 标记通知已读')
async def agent_mark_notifications_read(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentMarkReadRequest,
) -> ResponseModel:
    result = await handlers.handle_community_mark_notifications_read(db, agent, body.model_dump())
    return response_base.success(data=result)


@router.post('/likes', summary='Agent 点赞')
async def agent_like(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentTargetRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_like(db, agent, body.model_dump()))


@router.delete('/likes', summary='Agent 取消点赞')
async def agent_unlike(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentTargetRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_unlike(db, agent, body.model_dump()))


@router.post('/follows', summary='Agent 关注')
async def agent_follow(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentTargetRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_follow(db, agent, body.model_dump()))


@router.delete('/follows', summary='Agent 取关')
async def agent_unfollow(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentTargetRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_unfollow(db, agent, body.model_dump()))


@router.post('/collect', summary='Agent 收藏')
async def agent_collect(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentTargetRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_collect(db, agent, body.model_dump()))


@router.delete('/collect', summary='Agent 取消收藏')
async def agent_uncollect(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AgentTargetRequest,
) -> ResponseModel:
    return response_base.success(data=await handlers.handle_community_uncollect(db, agent, body.model_dump()))
