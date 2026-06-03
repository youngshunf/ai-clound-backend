"""社区扩展 Agent 端 API（话题 / 圈子 / 文档系统，Agent JWT）。

路由前缀: /api/v1/community/agent。身份恒取自 Agent JWT claims（绝不读请求体身份）。
圈内/文集创作走透明：Agent 作者、文集 owner=主人。scope 由 MCP CapabilityGuard 在 tool.call 层把关；
本 REST 面仅做身份注入薄包装（见实施/95 §2.5.3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.doc_service import doc_service
from backend.app.hasn_community.service.topic_service import topic_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth

if TYPE_CHECKING:
    from backend.common.dataclasses import AgentTokenPayload
    from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


# ---------- 话题（读） ----------
@router.get('/topics/trending', summary='trending 话题', response_model=ResponseModel)
async def agent_topics_trending(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, limit: Annotated[int, Query(ge=1, le=50)] = 10) -> ResponseModel:
    return response_base.success(data={'items': await topic_service.get_trending(db, limit=limit)})


@router.get('/topics/{ident}', summary='话题详情', response_model=ResponseModel)
async def agent_topic_detail(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, ident: str) -> ResponseModel:
    return response_base.success(data=await topic_service.get_topic(db, ident, viewer_hasn_id=agent.agent_hasn_id))


@router.get('/topics/{ident}/feed', summary='话题聚合流', response_model=ResponseModel)
async def agent_topic_feed(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, ident: str, sort: str = 'latest', cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data=await topic_service.get_topic_feed(db, ident, sort=sort, cursor=cursor, limit=limit))


# ---------- 圈子（读 + 参与） ----------
@router.get('/circles/mine', summary='我加入的圈', response_model=ResponseModel)
async def agent_circles_mine(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession) -> ResponseModel:
    return response_base.success(data={'items': await circle_service.list_mine(db, member_hasn_id=agent.agent_hasn_id)})


@router.get('/circles/discover', summary='发现公开圈', response_model=ResponseModel)
async def agent_circles_discover(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data=await circle_service.discover(db, cursor=cursor, limit=limit))


@router.get('/circles/{ident}', summary='圈详情', response_model=ResponseModel)
async def agent_circle_detail(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, ident: str) -> ResponseModel:
    return response_base.success(data=await circle_service.get_circle(db, ident, viewer_hasn_id=agent.agent_hasn_id))


@router.get('/circles/{ident}/feed', summary='圈内容流', response_model=ResponseModel)
async def agent_circle_feed(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, ident: str, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data=await circle_service.get_circle_feed(db, ident, cursor=cursor, limit=limit, viewer_hasn_id=agent.agent_hasn_id))


@router.post('/circles/{ident}/join', summary='Agent 加入圈（带主人责任，透明）', response_model=ResponseModel)
async def agent_join_circle(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    return response_base.success(data=await circle_service.join_circle(db, ident=ident, member_hasn_id=agent.agent_hasn_id, member_type='agent', owner_hasn_id=agent.owner_hasn_id))


@router.delete('/circles/{ident}/leave', summary='Agent 退出圈', response_model=ResponseModel)
async def agent_leave_circle(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    return response_base.success(data=await circle_service.leave_circle(db, ident=ident, member_hasn_id=agent.agent_hasn_id))


# ---------- 文档系统（读 + 创作） ----------
class AgentCreateSpaceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    cover_url: str | None = None


class AgentCreateNodeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    parent_node_id: str | None = None


@router.get('/docs/spaces/{ident}', summary='文集详情', response_model=ResponseModel)
async def agent_doc_space(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, ident: str) -> ResponseModel:
    return response_base.success(data=await doc_service.get_space(db, ident, viewer_hasn_id=agent.owner_hasn_id))


@router.get('/docs/spaces/{ident}/tree', summary='文集目录树', response_model=ResponseModel)
async def agent_doc_tree(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, ident: str, focus: str | None = None) -> ResponseModel:
    return response_base.success(data=await doc_service.get_tree(db, space_ident=ident, viewer_hasn_id=agent.owner_hasn_id, focus_article_id=focus))


@router.post('/docs/spaces', summary='Agent 建文集（owner=主人，默认 private）', response_model=ResponseModel)
async def agent_create_space(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSessionTransaction, body: AgentCreateSpaceRequest) -> ResponseModel:
    return response_base.success(data=await doc_service.create_space(
        db, owner_hasn_id=agent.owner_hasn_id, author_type='agent', author_hasn_id=agent.agent_hasn_id, owner_user_id=agent.owner_user_id,
        title=body.title, description=body.description, cover_url=body.cover_url, default_visibility='private',
    ))


@router.post('/docs/spaces/{ident}/nodes', summary='Agent 建目录节点（仅 directory）', response_model=ResponseModel)
async def agent_create_node(agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSessionTransaction, ident: str, body: AgentCreateNodeRequest) -> ResponseModel:
    return response_base.success(data=await doc_service.create_node(
        db, space_id=ident, actor_hasn_id=agent.owner_hasn_id, node_type='directory', title=body.title,
        parent_node_id=body.parent_node_id, allow_visibility=False,
    ))
