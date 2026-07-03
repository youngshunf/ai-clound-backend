"""社区 Open 端 API（公开只读）

路由前缀: /api/v1/community/open
认证方式: 无（公开只读）

仅返回已公开内容：帖子/文章仅 `status=published`（文章另要求 `visibility=public`），
评论仅 `status=visible`。仅暴露有真实后端支撑的只读端点；个人主页摘要、热门话题、
推荐 Agent 等当前为占位实现（stub），未在此暴露，待 community_service 真实化后
随 doc-12 Phase B 接入（禁止暴露 stub 假数据）。
"""
from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.app.hasn_community.service.community_service import community_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/feed', summary='公开社区信息流')
async def open_get_feed(
    db: CurrentSession,
    feed_type: Annotated[str, Query(description='信息流类型 (recommend/hot/articles)')] = 'recommend',
    cursor: Annotated[str | None, Query(description='分页游标')] = None,
    limit: Annotated[int, Query(ge=1, le=50, description='每页条数')] = 20,
) -> ResponseModel:
    """公开信息流：返回 status=published 的帖子（匿名，无个性化）。"""
    result = await community_service.get_feed(db, feed_type=feed_type, cursor=cursor, limit=limit)
    return response_base.success(data=result)


@router.get('/posts/{post_id}', summary='公开帖子详情')
async def open_get_post(
    db: CurrentSession,
    post_id: Annotated[str, Path(description='帖子 ID')],
) -> ResponseModel:
    result = await community_service.get_post(db, post_id=post_id)
    return response_base.success(data=result)


@router.get('/posts/{post_id}/comments', summary='公开帖子评论列表')
async def open_get_post_comments(
    db: CurrentSession,
    post_id: Annotated[str, Path(description='帖子 ID')],
    sort: Annotated[str, Query(description='排序 (time_asc/time_desc/hot)')] = 'time_desc',
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    result = await community_service.get_comments(
        db, target_type='post', target_id=post_id, sort=sort, cursor=cursor, limit=limit
    )
    return response_base.success(data=result)


@router.get('/articles/{article_id}', summary='公开文章详情')
async def open_get_article(
    db: CurrentSession,
    article_id: Annotated[str, Path(description='文章 ID')],
) -> ResponseModel:
    """公开文章详情：仅 status=published 且 visibility=public。"""
    result = await community_service.get_public_article(db, article_id=article_id)
    return response_base.success(data=result)


@router.get('/articles/{article_id}/comments', summary='公开文章评论列表')
async def open_get_article_comments(
    db: CurrentSession,
    article_id: Annotated[str, Path(description='文章 ID')],
    sort: Annotated[str, Query(description='排序 (time_asc/time_desc/hot)')] = 'time_desc',
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    result = await community_service.get_comments(
        db, target_type='article', target_id=article_id, sort=sort, cursor=cursor, limit=limit
    )
    return response_base.success(data=result)
