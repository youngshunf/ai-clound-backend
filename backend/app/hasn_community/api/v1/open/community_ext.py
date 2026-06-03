"""社区扩展 Open 端 API（话题 / 圈子 / 文档系统，公开只读）。

路由前缀: /api/v1/community/open
认证方式: 无（仅暴露 active/public 内容；私有/未解锁密码子树不泄露标题）。
见设计 15/16/17 与实施/95。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Query

from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.doc_service import doc_service
from backend.app.hasn_community.service.topic_service import topic_service
from backend.common.response.response_schema import ResponseModel, response_base

if TYPE_CHECKING:
    from backend.database.db import CurrentSession

router = APIRouter()


# ---------- 话题 ----------
@router.get('/topics/trending', summary='真实 trending 话题', response_model=ResponseModel)
async def open_topics_trending(db: CurrentSession, limit: Annotated[int, Query(ge=1, le=50)] = 10) -> ResponseModel:
    return response_base.success(data={'items': await topic_service.get_trending(db, limit=limit)})


@router.get('/topics/search', summary='话题搜索（前缀+模糊）', response_model=ResponseModel)
async def open_topics_search(db: CurrentSession, q: Annotated[str, Query(min_length=1)], limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data={'items': await topic_service.search_topics(db, q, limit=limit)})


@router.get('/topics/{ident}', summary='公开话题详情', response_model=ResponseModel)
async def open_topic_detail(db: CurrentSession, ident: str) -> ResponseModel:
    return response_base.success(data=await topic_service.get_topic(db, ident, public_only=True))


@router.get('/topics/{ident}/feed', summary='公开话题聚合流', response_model=ResponseModel)
async def open_topic_feed(db: CurrentSession, ident: str, sort: str = 'latest', cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data=await topic_service.get_topic_feed(db, ident, sort=sort, cursor=cursor, limit=limit, public_only=True))


# ---------- 圈子 ----------
@router.get('/circles/discover', summary='发现公开圈', response_model=ResponseModel)
async def open_circles_discover(db: CurrentSession, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data=await circle_service.discover(db, cursor=cursor, limit=limit))


@router.get('/circles/{ident}', summary='公开圈详情', response_model=ResponseModel)
async def open_circle_detail(db: CurrentSession, ident: str) -> ResponseModel:
    return response_base.success(data=await circle_service.get_circle(db, ident, public_only=True))


@router.get('/circles/{ident}/feed', summary='公开圈内容流', response_model=ResponseModel)
async def open_circle_feed(db: CurrentSession, ident: str, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    return response_base.success(data=await circle_service.get_circle_feed(db, ident, cursor=cursor, limit=limit, public_only=True))


# ---------- 文档系统 ----------
@router.get('/docs/spaces/{ident}', summary='公开文集详情', response_model=ResponseModel)
async def open_doc_space(db: CurrentSession, ident: str) -> ResponseModel:
    return response_base.success(data=await doc_service.get_space(db, ident, public_only=True))


@router.get('/docs/spaces/{ident}/tree', summary='公开目录树（仅 public 子树+锁定占位）', response_model=ResponseModel)
async def open_doc_tree(db: CurrentSession, ident: str, focus: str | None = None, grant_tokens: Annotated[list[str] | None, Query()] = None) -> ResponseModel:
    if grant_tokens is None:
        grant_tokens = []
    return response_base.success(data=await doc_service.get_tree(db, space_ident=ident, public_only=True, focus_article_id=focus, grant_tokens=grant_tokens))


@router.get('/docs/spaces/{ident}/articles/{article_id}', summary='通过文集读文章（受节点有效可见性）', response_model=ResponseModel)
async def open_doc_article(db: CurrentSession, ident: str, article_id: str, grant_tokens: Annotated[list[str] | None, Query()] = None) -> ResponseModel:
    if grant_tokens is None:
        grant_tokens = []
    return response_base.success(data=await doc_service.get_article_via_space(db, space_ident=ident, article_id=article_id, public_only=True, grant_tokens=grant_tokens))


@router.post('/docs/nodes/{node_id}/unlock', summary='提交密码换 grant_token', response_model=ResponseModel)
async def open_doc_unlock(db: CurrentSession, node_id: str, password: Annotated[str, Body(embed=True)]) -> ResponseModel:
    return response_base.success(data=await doc_service.unlock(db, node_id=node_id, password=password))
