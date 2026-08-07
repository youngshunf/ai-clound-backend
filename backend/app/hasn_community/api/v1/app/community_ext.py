"""社区扩展用户端 API（话题 / 圈子 / 文档系统，Owner JWT）。

路由前缀: /api/v1/community/app。身份恒为登录 Owner 本人 human（认证凭证，不读请求体身份）。
见设计 15/16/17 与实施/95。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.doc_service import doc_service
from backend.app.hasn_community.service.topic_service import topic_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _human(db, request: Request):
    """解析登录 Owner 的 hasn_id（身份=认证凭证）。"""
    from backend.app.hasn_core import identity

    human = await identity.get_human_by_user_id(db, user_id=request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human, request.user.id


# ==================== 话题 ====================
class CreateTopicRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = None
    cover_url: str | None = None


class ResolveTopicsRequest(BaseModel):
    names: list[str] = Field(default_factory=list)


@router.get('/topics/following', summary='我关注的话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_topics_following(request: Request, db: CurrentSession) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data={'items': await topic_service.get_following(db, follower_hasn_id=human.hasn_id)})


@router.post('/topics', summary='用户自建话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_create_topic(request: Request, db: CurrentSessionTransaction, body: CreateTopicRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await topic_service.create_topic(db, name=body.name, description=body.description, cover_url=body.cover_url, created_by_hasn_id=human.hasn_id))


@router.post('/topics/resolve', summary='名称数组→话题（发布弹窗回显，缺则建）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_resolve_topics(request: Request, db: CurrentSessionTransaction, body: ResolveTopicsRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data={'items': await topic_service.resolve_topics(db, body.names, created_by=human.hasn_id)})


@router.get('/topics/{ident}', summary='话题详情（含 is_following）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_topic_detail(request: Request, db: CurrentSession, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await topic_service.get_topic(db, ident, viewer_hasn_id=human.hasn_id))


@router.get('/topics/{ident}/feed', summary='话题聚合流', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_topic_feed(request: Request, db: CurrentSession, ident: str, sort: str = 'latest', cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await topic_service.get_topic_feed(db, ident, sort=sort, cursor=cursor, limit=limit, viewer_hasn_id=human.hasn_id))


@router.post('/topics/{ident}/follow', summary='关注话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_follow_topic(request: Request, db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await topic_service.follow_topic(db, follower_hasn_id=human.hasn_id, topic_id=ident, following=True))


@router.delete('/topics/{ident}/follow', summary='取关话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_unfollow_topic(request: Request, db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await topic_service.follow_topic(db, follower_hasn_id=human.hasn_id, topic_id=ident, following=False))


# ==================== 圈子 ====================
class CreateCircleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = None
    cover_url: str | None = None
    avatar_url: str | None = None
    visibility: str = 'public'
    join_policy: str = 'approval'
    post_policy: str = 'members'


class UpdateCircleRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    cover_url: str | None = None
    avatar_url: str | None = None
    visibility: str | None = None
    join_policy: str | None = None
    post_policy: str | None = None


class ModerateMemberRequest(BaseModel):
    action: Literal['approve', 'reject', 'ban', 'set-role'] = Field(
        ...,
        description='approve/reject/ban/set-role',
    )
    role: Literal['admin', 'member'] | None = Field(None, description='set-role 时 admin/member')


class InviteRequest(BaseModel):
    invitee_hasn_id: str
    invitee_type: str = 'human'
    invitee_owner_hasn_id: str | None = None


class ModerateContentRequest(BaseModel):
    content_type: Literal['post', 'article'] = Field(..., description='post/article')
    action: Literal['approve', 'hide', 'delete'] = Field(..., description='approve/hide/delete')


@router.get('/circles/mine', summary='我加入/管理的圈', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_circles_mine(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await circle_service.list_mine(
            db,
            member_hasn_id=human.hasn_id,
            cursor=cursor,
            limit=limit,
        )
    )


@router.get('/circles/discover', summary='发现公开圈', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_circles_discover(
    request: Request,
    db: CurrentSession,
    sort: Literal['active', 'newest', 'members'] = 'active',
    join_policy: Literal['open', 'approval', 'invite'] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await circle_service.discover(
            db,
            viewer_hasn_id=human.hasn_id,
            sort=sort,
            join_policy=join_policy,
            cursor=cursor,
            limit=limit,
        )
    )


@router.post('/circles', summary='建圈（建者自动 owner 成员）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_create_circle(request: Request, db: CurrentSessionTransaction, body: CreateCircleRequest) -> ResponseModel:
    human, user_id = await _human(db, request)
    return response_base.success(data=await circle_service.create_circle(
        db, owner_hasn_id=human.hasn_id, owner_user_id=user_id, name=body.name, description=body.description,
        cover_url=body.cover_url, avatar_url=body.avatar_url, visibility=body.visibility, join_policy=body.join_policy, post_policy=body.post_policy,
    ))


@router.get('/circles/{ident}', summary='圈详情（含 my_role/my_status）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_circle_detail(request: Request, db: CurrentSession, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.get_circle(db, ident, viewer_hasn_id=human.hasn_id))


@router.put('/circles/{ident}', summary='改圈资料/策略（owner/admin）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_update_circle(request: Request, db: CurrentSessionTransaction, ident: str, body: UpdateCircleRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.update_circle(db, ident=ident, actor_hasn_id=human.hasn_id, **body.model_dump(exclude_none=True)))


@router.post('/circles/{ident}/join', summary='加入圈（按 join_policy）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_join_circle(request: Request, db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.join_circle(db, ident=ident, member_hasn_id=human.hasn_id, member_type='human', owner_hasn_id=human.hasn_id))


@router.delete('/circles/{ident}/leave', summary='退出圈', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_leave_circle(request: Request, db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.leave_circle(db, ident=ident, member_hasn_id=human.hasn_id))


@router.get('/circles/{ident}/members', summary='成员列表', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_circle_members(
    request: Request,
    db: CurrentSession,
    ident: str,
    status: Literal['active', 'pending', 'banned', 'left', 'all'] = 'active',
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResponseModel:
    await _human(db, request)
    return response_base.success(
        data=await circle_service.list_members(
            db,
            ident=ident,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    )


@router.post('/circles/{ident}/members/{member_hasn_id}/moderate', summary='成员治理（owner/admin）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_moderate_member(request: Request, db: CurrentSessionTransaction, ident: str, member_hasn_id: str, body: ModerateMemberRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.moderate_member(db, ident=ident, target_hasn_id=member_hasn_id, actor_hasn_id=human.hasn_id, action=body.action, role=body.role))


@router.post('/circles/{ident}/invite', summary='邀请入圈（owner/admin）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_invite(request: Request, db: CurrentSessionTransaction, ident: str, body: InviteRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.invite(db, ident=ident, actor_hasn_id=human.hasn_id, invitee_hasn_id=body.invitee_hasn_id, invitee_type=body.invitee_type, invitee_owner_hasn_id=body.invitee_owner_hasn_id))


@router.get('/circles/{ident}/feed', summary='圈内容流', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_circle_feed(request: Request, db: CurrentSession, ident: str, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.get_circle_feed(db, ident, cursor=cursor, limit=limit, viewer_hasn_id=human.hasn_id))


@router.get('/circles/{ident}/content/pending', summary='圈内待审内容', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_circle_pending_content(
    request: Request,
    db: CurrentSession,
    ident: str,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await circle_service.list_pending_content(
            db,
            ident=ident,
            actor_hasn_id=human.hasn_id,
            cursor=cursor,
            limit=limit,
        )
    )


@router.post('/circles/{ident}/content/{content_id}/moderate', summary='圈内内容治理（owner/admin）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_moderate_content(request: Request, db: CurrentSessionTransaction, ident: str, content_id: str, body: ModerateContentRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await circle_service.moderate_content(db, ident=ident, content_type=body.content_type, content_id=content_id, actor_hasn_id=human.hasn_id, action=body.action))


# ==================== 文档系统 ====================
class CreateSpaceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    cover_url: str | None = None
    default_visibility: str = 'private'
    default_password: str | None = None


class UpdateSpaceRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    cover_url: str | None = None
    default_visibility: str | None = None
    default_password: str | None = None


class CreateNodeRequest(BaseModel):
    node_type: str = Field(..., description='directory/article')
    title: str = Field(..., min_length=1, max_length=200)
    parent_node_id: str | None = None
    article_id: str | None = None
    visibility: str | None = None
    password: str | None = None


class UpdateNodeRequest(BaseModel):
    title: str | None = None
    visibility: str | None = Field(None, description='public/private/password/inherit')
    password: str | None = None


class MoveNodeRequest(BaseModel):
    new_parent_node_id: str | None = None


class ReorderRequest(BaseModel):
    ordered_node_ids: list[str] = Field(default_factory=list)


@router.post('/docs/spaces', summary='建文集', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_create_space(request: Request, db: CurrentSessionTransaction, body: CreateSpaceRequest) -> ResponseModel:
    human, user_id = await _human(db, request)
    return response_base.success(data=await doc_service.create_space(
        db, owner_hasn_id=human.hasn_id, author_type='human', author_hasn_id=human.hasn_id, owner_user_id=user_id,
        title=body.title, description=body.description, cover_url=body.cover_url, default_visibility=body.default_visibility, default_password=body.default_password,
    ))


@router.get('/docs/spaces/mine', summary='我的文集列表', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_spaces_mine(request: Request, db: CurrentSession) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data={'items': await doc_service.list_mine(db, owner_hasn_id=human.hasn_id)})


@router.get('/docs/spaces/discover', summary='发现公开文集（含作者信息）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_spaces_discover(request: Request, db: CurrentSession, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await doc_service.discover_public(
            db,
            viewer_hasn_id=human.hasn_id,
            cursor=cursor,
            limit=limit,
        )
    )


@router.get('/docs/spaces/subscribed', summary='我订阅的文集', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_spaces_subscribed(
    request: Request,
    db: CurrentSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await doc_service.list_subscribed(
            db,
            subscriber_hasn_id=human.hasn_id,
            cursor=cursor,
            limit=limit,
        )
    )


@router.get('/profiles/{hasn_id}/doc-spaces', summary='作者主页文集', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_profile_doc_spaces(
    request: Request,
    db: CurrentSession,
    hasn_id: str,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await doc_service.list_by_author(
            db,
            author_hasn_id=hasn_id,
            viewer_hasn_id=human.hasn_id,
            cursor=cursor,
            limit=limit,
        )
    )


@router.get('/docs/spaces/{ident}', summary='文集详情', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_doc_space(request: Request, db: CurrentSession, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.get_space(db, ident, viewer_hasn_id=human.hasn_id))


@router.put('/docs/spaces/{ident}', summary='改文集资料', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_update_space(request: Request, db: CurrentSessionTransaction, ident: str, body: UpdateSpaceRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.update_space(db, ident=ident, actor_hasn_id=human.hasn_id, **body.model_dump(exclude_none=True)))


@router.delete('/docs/spaces/{ident}', summary='删文集（软删）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_delete_space(request: Request, db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.delete_space(db, ident=ident, actor_hasn_id=human.hasn_id))


@router.post('/docs/spaces/{ident}/subscribe', summary='订阅文集', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_subscribe_space(
    request: Request,
    db: CurrentSessionTransaction,
    ident: str,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await doc_service.subscribe(
            db,
            ident=ident,
            subscriber_hasn_id=human.hasn_id,
        )
    )


@router.delete('/docs/spaces/{ident}/subscribe', summary='取消订阅文集', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_unsubscribe_space(
    request: Request,
    db: CurrentSessionTransaction,
    ident: str,
) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(
        data=await doc_service.unsubscribe(
            db,
            ident=ident,
            subscriber_hasn_id=human.hasn_id,
        )
    )


@router.get('/docs/spaces/{ident}/tree', summary='完整目录树（owner 视角）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_doc_tree(request: Request, db: CurrentSession, ident: str, focus: str | None = None) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.get_tree(db, space_ident=ident, viewer_hasn_id=human.hasn_id, focus_article_id=focus))


@router.post('/docs/spaces/{ident}/nodes', summary='建节点（目录或挂文章）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_create_node(request: Request, db: CurrentSessionTransaction, ident: str, body: CreateNodeRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.create_node(
        db, space_id=ident, actor_hasn_id=human.hasn_id, node_type=body.node_type, title=body.title,
        parent_node_id=body.parent_node_id, article_id=body.article_id, visibility=body.visibility, password=body.password,
    ))


@router.put('/docs/nodes/{node_id}', summary='改节点（改名/可见性/密码）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_update_node(request: Request, db: CurrentSessionTransaction, node_id: str, body: UpdateNodeRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.update_node(db, node_id=node_id, actor_hasn_id=human.hasn_id, title=body.title, visibility=body.visibility, password=body.password))


@router.post('/docs/nodes/reorder', summary='同级批量排序', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_reorder_nodes(request: Request, db: CurrentSessionTransaction, body: ReorderRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.reorder_nodes(db, actor_hasn_id=human.hasn_id, ordered_node_ids=body.ordered_node_ids))


@router.post('/docs/nodes/{node_id}/move', summary='移动节点（重算 path/depth）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_move_node(request: Request, db: CurrentSessionTransaction, node_id: str, body: MoveNodeRequest) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.move_node(db, node_id=node_id, actor_hasn_id=human.hasn_id, new_parent_node_id=body.new_parent_node_id))


@router.delete('/docs/nodes/{node_id}', summary='删节点（级联）', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def app_delete_node(request: Request, db: CurrentSessionTransaction, node_id: str, cascade: Annotated[bool, Query()] = True) -> ResponseModel:
    human, _ = await _human(db, request)
    return response_base.success(data=await doc_service.delete_node(db, node_id=node_id, actor_hasn_id=human.hasn_id, cascade=cascade))
