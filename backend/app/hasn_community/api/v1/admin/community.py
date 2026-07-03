"""社区 Admin 端 API（管理端，只读审核可见性）

路由前缀: /api/v1/community/admin
认证方式: Admin JWT（DependsJwtAuth）

本轮提供只读审核能力：列出/查看**全状态**（draft/pending_review/published/hidden/deleted）
内容，供管理员发现并核查问题内容。

审核**写操作**（隐藏/删除/恢复、举报队列处理）需 RBAC 权限码（如 `community:post:moderate`）
+ menu SQL + 状态流转产品规则，留后续（见
decisions/engineering/2026-05-29-backend-module-split-hasn-community.md）。禁止占位假实现。
"""
from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.app.hasn_community.service.admin_query_service import community_admin_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/posts', summary='管理端帖子列表（全状态）', dependencies=[DependsJwtAuth])
async def admin_list_posts(
    db: CurrentSession,
    status: Annotated[str | None, Query(description='draft/pending_review/published/hidden/deleted')] = None,
    author_hasn_id: Annotated[str | None, Query(description='按作者 hasn_id 过滤')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    result = await community_admin_service.admin_list_posts(
        db, status=status, author_hasn_id=author_hasn_id, limit=limit, offset=offset
    )
    return response_base.success(data=result)


@router.get('/posts/{post_id}', summary='管理端帖子详情（任意状态）', dependencies=[DependsJwtAuth])
async def admin_get_post(
    db: CurrentSession,
    post_id: Annotated[str, Path(description='帖子 ID')],
) -> ResponseModel:
    result = await community_admin_service.admin_get_post(db, post_id=post_id)
    return response_base.success(data=result)


@router.get('/articles', summary='管理端文章列表（全状态）', dependencies=[DependsJwtAuth])
async def admin_list_articles(
    db: CurrentSession,
    status: Annotated[str | None, Query(description='draft/pending_review/published/hidden/deleted')] = None,
    author_hasn_id: Annotated[str | None, Query(description='按作者 hasn_id 过滤')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    result = await community_admin_service.admin_list_articles(
        db, status=status, author_hasn_id=author_hasn_id, limit=limit, offset=offset
    )
    return response_base.success(data=result)


@router.get(
    '/articles/{article_id}', summary='管理端文章详情（任意状态）', dependencies=[DependsJwtAuth]
)
async def admin_get_article(
    db: CurrentSession,
    article_id: Annotated[str, Path(description='文章 ID')],
) -> ResponseModel:
    result = await community_admin_service.admin_get_article(db, article_id=article_id)
    return response_base.success(data=result)


@router.get('/comments', summary='管理端评论列表（全状态）', dependencies=[DependsJwtAuth])
async def admin_list_comments(
    db: CurrentSession,
    status: Annotated[str | None, Query(description='visible/hidden/deleted')] = None,
    target_type: Annotated[str | None, Query(description='post/article')] = None,
    target_id: Annotated[str | None, Query(description='目标 post_id 或 article_id')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    result = await community_admin_service.admin_list_comments(
        db, status=status, target_type=target_type, target_id=target_id, limit=limit, offset=offset
    )
    return response_base.success(data=result)
