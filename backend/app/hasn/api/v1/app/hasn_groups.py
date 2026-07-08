"""HASN 群组 - 用户端 API（建群 / 群管理）。

认证: DependsJwtAuth（当前登录主人）。身份解析为 owner 的 hasn_id。
事实源: docs/hasn-node设计文档/03-Runtime调度/06-群聊派发与Agent参与设计.md G1。
统一信封: 全部 response_base.success（fork 仓硬规则）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Path, Request
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_group_service import hasn_group_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class GroupMemberInput(BaseModel):
    hasn_id: str = Field(..., description='成员 HASN ID（h_/a_）')


class CreateGroupBody(BaseModel):
    title: str = Field(..., description='群名称（1..80）')
    members: list[GroupMemberInput] = Field(default_factory=list, description='初始成员（不含创建者）')
    agent_policy: str = Field('free', description='分身发言策略 free/mention_only/silent/no_agent')
    avatar_url: str | None = Field(None, description='群头像 URL')
    join_policy: str = Field('invite_only', description='加入策略 invite_only/open/approval')


class AddMembersBody(BaseModel):
    members: list[GroupMemberInput] = Field(..., description='要加入的成员')


class UpdateGroupBody(BaseModel):
    title: str | None = Field(None, description='新群名称')
    avatar_url: str | None = Field(None, description='新群头像')
    agent_policy: str | None = Field(None, description='新分身发言策略')
    join_policy: str | None = Field(None, description='新加入策略 invite_only/open/approval')
    allow_member_invite_agent: bool | None = Field(None, description='是否允许普通成员拉分身进群（doc10）')


class SetCharterBody(BaseModel):
    charter: str | None = Field(None, description='分身群内发言准则（≤4000 字，null/空串=清除）')


async def _caller_hasn_id(request: Request, db: CurrentSession) -> str:
    caller = getattr(request.user, 'hasn_id', None)
    if caller:
        return caller
    from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao

    human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if human:
        return human.hasn_id
    raise errors.AuthorizationError(msg='用户未绑定 HASN ID')


@router.post('', summary='建群', dependencies=[DependsJwtAuth])
async def create_group(
    request: Request,
    db: CurrentSessionTransaction,
    body: Annotated[CreateGroupBody, Body()],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.create_group(
        db=db,
        owner_hasn_id=caller,
        title=body.title,
        members=[m.model_dump() for m in body.members],
        agent_policy=body.agent_policy,
        avatar_url=body.avatar_url,
        join_policy=body.join_policy,
    )
    return response_base.success(data=data)


@router.get('', summary='我的群列表', dependencies=[DependsJwtAuth])
async def list_my_groups(request: Request, db: CurrentSession) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    items = await hasn_group_service.list_my_groups(db=db, hasn_id=caller)
    return response_base.success(data={'items': items})


@router.get('/{group_id}/preview', summary='群公开元信息（非成员可读·群名片预览）', dependencies=[DependsJwtAuth])
async def preview_group(
    request: Request,
    db: CurrentSession,
    group_id: Annotated[str, Path(description='群组公开 ID g:NNNNNN')],
) -> ResponseSchemaModel[dict]:
    # doc22 群名片：非成员也可读到群名/头像/人数/加入策略等公开字段（不含完整名册）。
    # 额外返回 is_member/my_role，供预览页对 viewer 分叉「加入群聊 / 进入群聊」按钮。
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.get_group_public_meta(db=db, viewer_hasn_id=caller, group_id=group_id)
    return response_base.success(data=data)


@router.post('/{group_id}/join', summary='申请加入群聊（尊重群加入策略）', dependencies=[DependsJwtAuth])
async def join_group(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID g:NNNNNN')],
) -> ResponseSchemaModel[dict]:
    # doc22 群名片：非成员从群预览页点「加入群聊」。尊重群加入策略——
    # open（自由加入）直接入群返回 joined=True；invite_only/approval 落待审返回 joined=False。
    # 与分身工具 hasn.group.join 共用 HasnGroupService.join_group 单一实现，零 fake。
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.join_group(db, applicant_hasn_id=caller, group_id=group_id)
    return response_base.success(data=data)


@router.get('/{group_id}', summary='群详情 + 名册', dependencies=[DependsJwtAuth])
async def get_group(
    request: Request,
    db: CurrentSession,
    group_id: Annotated[str, Path(description='群组公开 ID g:NNNNNN')],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.get_group_detail(db=db, hasn_id=caller, group_id=group_id)
    return response_base.success(data=data)


@router.post('/{group_id}/members', summary='加成员', dependencies=[DependsJwtAuth])
async def add_members(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    body: Annotated[AddMembersBody, Body()],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.add_members(
        db=db, actor_hasn_id=caller, group_id=group_id, members=[m.model_dump() for m in body.members]
    )
    return response_base.success(data=data)


@router.delete('/{group_id}/members/{member_id}', summary='移除成员 / 退群', dependencies=[DependsJwtAuth])
async def remove_group_member(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    member_id: Annotated[str, Path(description='成员 HASN ID')],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.remove_member(
        db=db, actor_hasn_id=caller, group_id=group_id, member_id=member_id
    )
    return response_base.success(data=data)


@router.patch('/{group_id}', summary='改群设置', dependencies=[DependsJwtAuth])
async def update_group(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    body: Annotated[UpdateGroupBody, Body()],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.update_group(
        db=db,
        actor_hasn_id=caller,
        group_id=group_id,
        title=body.title,
        avatar_url=body.avatar_url,
        agent_policy=body.agent_policy,
        join_policy=body.join_policy,
        allow_member_invite_agent=body.allow_member_invite_agent,
    )
    return response_base.success(data=data)


@router.put(
    '/{group_id}/members/{agent_hasn_id}/charter',
    summary='设置分身群内发言准则（仅分身主人）',
    dependencies=[DependsJwtAuth],
)
async def set_agent_charter(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    agent_hasn_id: Annotated[str, Path(description='分身 HASN ID a_...')],
    body: Annotated[SetCharterBody, Body()],
) -> ResponseSchemaModel[dict]:
    # doc10 §4：分身群内发言准则——仅分身主人可读写；随派发注入 runtime system_prompt。
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.set_agent_charter(
        db=db, actor_hasn_id=caller, group_id=group_id, agent_hasn_id=agent_hasn_id, charter=body.charter
    )
    return response_base.success(data=data)


@router.post(
    '/{group_id}/agent-invites/{invite_id}/accept',
    summary='同意拉分身邀请（仅分身主人）',
    dependencies=[DependsJwtAuth],
)
async def accept_agent_invite(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    invite_id: Annotated[int, Path(description='邀请 ID')],
) -> ResponseSchemaModel[dict]:
    # doc10 §3.2：非主人拉分身进群需主人同意——主人点卡片「同意」→ 分身即时入群。
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.accept_agent_invite(
        db=db, actor_hasn_id=caller, group_id=group_id, invite_id=invite_id
    )
    return response_base.success(data=data)


@router.post(
    '/{group_id}/agent-invites/{invite_id}/decline',
    summary='拒绝拉分身邀请（仅分身主人）',
    dependencies=[DependsJwtAuth],
)
async def decline_agent_invite(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    invite_id: Annotated[int, Path(description='邀请 ID')],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.decline_agent_invite(
        db=db, actor_hasn_id=caller, group_id=group_id, invite_id=invite_id
    )
    return response_base.success(data=data)


@router.post(
    '/{group_id}/agent-invites/{invite_id}/cancel',
    summary='撤回拉分身邀请（仅发起人）',
    dependencies=[DependsJwtAuth],
)
async def cancel_agent_invite(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
    invite_id: Annotated[int, Path(description='邀请 ID')],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.cancel_agent_invite(
        db=db, actor_hasn_id=caller, group_id=group_id, invite_id=invite_id
    )
    return response_base.success(data=data)


@router.delete('/{group_id}', summary='解散群', dependencies=[DependsJwtAuth])
async def disband_group(
    request: Request,
    db: CurrentSessionTransaction,
    group_id: Annotated[str, Path(description='群组公开 ID')],
) -> ResponseSchemaModel[dict]:
    caller = await _caller_hasn_id(request, db)
    data = await hasn_group_service.disband_group(db=db, actor_hasn_id=caller, group_id=group_id)
    return response_base.success(data=data)
