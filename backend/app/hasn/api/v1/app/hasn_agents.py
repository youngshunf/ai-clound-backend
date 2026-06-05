"""HASN Agent  - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from backend.app.hasn.model import HasnAgents, HasnHumans
from backend.app.hasn.schema.hasn_agents import (
    AgentBundleInstallRequest,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentSkillInstallRequest,
    AgentSkillUninstallRequest,
    AgentSnapshot,
    AgentSyncRequest,
    AgentSyncResponse,
    CloudCreateAgentRequest,
    CloudCreateAgentResponse,
    CreateHasnAgentsParam,
    GetHasnAgentsDetail,
    UpdateAgentBindingRequest,
    UpdateAgentProfileRequest,
    UpdateAgentProfileResponse,
    UpdateHasnAgentsParam,
)
from backend.app.hasn.service.hasn_agents_service import agent_profile_service, hasn_agents_service
from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn.service.message_router import check_relation_permission
from backend.app.hasn.service.ws_router import ws_router
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/sync',
    summary='同步云端 HASN Agent Profile 快照',
    dependencies=[DependsJwtAuth],
)
async def sync_my_hasn_agents(
    request: Request,
    db: CurrentSession,
    owner_id: str,
    after_revision: int | None = None,
) -> ResponseSchemaModel[AgentSyncResponse]:
    result = await agent_profile_service.sync_agents(
        db=db,
        request=AgentSyncRequest(owner_id=owner_id, after_revision=after_revision),
        user_id=request.user.id,
    )
    return response_base.success(data=result)


class AgentReachabilityResponse(BaseModel):
    """某 Agent 对「当前请求 owner」的可达性快照。

    供 daemon 发送前预检：写入本地 `remote_agent_reachability` 镜像后，
    daemon `evaluate_remote` 据此判定是否放行跨 owner 发送。字段与该镜像
    一一对应。
    """

    agent_id: str
    owner_id: str = Field(description='Agent 归属人 hasn_id')
    social_exposure: str = Field(description='对当前请求方的社交可达性: hasn_social_enabled / owner_only')
    online_status: str = Field(description='online / offline')
    binding_active: bool = Field(description='Agent 运行时是否在线挂载')
    runtime_type: str | None = None
    node_id: str | None = None


@router.get(
    '/{agent_id}/reachability',
    summary='查询某 Agent 对当前 owner 的可达性（跨 owner 发送前预检）',
    dependencies=[DependsJwtAuth],
)
async def get_agent_reachability(
    agent_id: Annotated[str, Path(description='目标 Agent hasn_id')],
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseSchemaModel[AgentReachabilityResponse]:
    """复用权威关系门控 `check_relation_permission` 派生 `social_exposure`，叠加
    presence（ws_router）与运行时挂载，返回 daemon 镜像所需字段。

    与 `route_message` 的发送门控判定完全一致（同一函数），避免可达策略在两处
    维护而漂移；不可达时 daemon 仍 fail-closed 拒发。
    """
    requester_id = auth.get('effective_id', auth['hasn_id'])
    agent = (await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise errors.NotFoundError(msg='Agent 不存在')

    perm = await check_relation_permission(db, requester_id, agent_id, 'message')
    online = await ws_router.is_agent_online(agent_id)

    # runtime_type（hermes 等运行时适配器类型）：hasn_agents.runtime_summary_json
    # 列虽存在，但当前无任何写入方（实库恒为 {}），且该字段不参与 evaluate_remote
    # 门控（仅 social_exposure + online + binding）。故留空 None，由 daemon 镜像从
    # 本地 binding 按需补真实 runtime_type；不在此 fabricate。
    return response_base.success(
        data=AgentReachabilityResponse(
            agent_id=agent_id,
            owner_id=agent.owner_id,
            social_exposure='hasn_social_enabled' if perm.get('allowed') else 'owner_only',
            online_status='online' if online else 'offline',
            binding_active=bool(online),
            runtime_type=None,
            node_id=agent.node_id,
        )
    )


@router.post(
    '/cloud-create',
    summary='云端优先创建 HASN Agent Profile',
    dependencies=[DependsJwtAuth],
)
async def cloud_create_my_hasn_agent(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CloudCreateAgentRequest,
) -> ResponseSchemaModel[CloudCreateAgentResponse]:
    result = await agent_profile_service.create_cloud_first(db=db, request=obj, user_id=request.user.id)
    return response_base.success(data=result)


@router.get(
    '',
    summary='获取我的HASN Agent 列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_hasn_agentss(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnAgentsDetail]]:
    page_data = await hasn_agents_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Agent ',
    dependencies=[DependsJwtAuth],
)
async def create_my_hasn_agents(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnAgentsParam,
) -> ResponseModel:
    result = await hasn_agents_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN Agent 详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_hasn_agents(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN Agent  ID')],
) -> ResponseSchemaModel[GetHasnAgentsDetail]:
    hasn_agents = await hasn_agents_service.get(db=db, pk=pk)
    if hasn_agents.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该HASN Agent ')
    return response_base.success(data=hasn_agents)


@router.put(
    '/{pk}',
    summary='更新HASN Agent ',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_agents(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN Agent  ID')],
    obj: UpdateHasnAgentsParam,
) -> ResponseModel:
    user_id = request.user.id
    hasn_agents = await hasn_agents_service.get(db=db, pk=pk)
    if hasn_agents.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN Agent ')
    count = await hasn_agents_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


class ToggleSocialBody(BaseModel):
    enabled: bool = Field(description='目标 social_enabled 值')


@router.patch(
    '/by-hasn-id/{hasn_id}',
    summary='daemon 端 Agent Profile 部分更新（云端权威）',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_agent_profile(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: UpdateAgentProfileRequest,
) -> ResponseSchemaModel[UpdateAgentProfileResponse]:
    """daemon 用 hasn_id 更新 Agent profile：display_name/description/avatar 部分字段。

    云端先落库后返回最新 AgentSnapshot；daemon 据此回写本地镜像，保证 daemon 与云端一致。
    """
    user_id = request.user.id
    owner = (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    result = await agent_profile_service.update_profile_cloud_first(
        db,
        owner_id=owner,
        hasn_id=hasn_id,
        request=body,
        user_id=user_id,
    )
    return response_base.success(data=result)


@router.post(
    '/by-hasn-id/{hasn_id}/skills/install',
    summary='daemon 端为 Agent 装配市场技能（云端权威）',
    dependencies=[DependsJwtAuth],
)
async def install_agent_skill(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: AgentSkillInstallRequest,
) -> ResponseSchemaModel[UpdateAgentProfileResponse]:
    """把市场技能 skill_id 并入该 Agent 的权威技能清单（hasn_agents.skills）。

    云端校验：当前用户拥有该 owner + Agent 归属 + 技能 published/public。落库后 bump
    profile_revision 并 append 同步事件；daemon 据返回快照触发 re-provision，由 runtime
    下载技能包完成物化（云端只持有权威清单，不在此 push 文件）。
    """
    user_id = request.user.id
    owner = (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    result = await agent_profile_service.attach_skill_cloud_first(
        db,
        owner_id=owner,
        hasn_id=hasn_id,
        skill_id=body.skill_id,
        user_id=user_id,
    )
    return response_base.success(data=result)


@router.post(
    '/by-hasn-id/{hasn_id}/skills/uninstall',
    summary='daemon 端卸载 Agent 技能（云端权威）',
    dependencies=[DependsJwtAuth],
)
async def uninstall_agent_skill(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: AgentSkillUninstallRequest,
) -> ResponseSchemaModel[UpdateAgentProfileResponse]:
    """从该 Agent 的权威技能清单移除 skill_id。落库后 bump revision + 同步事件，
    daemon 据返回快照触发 re-provision。已下架技能也允许卸载。"""
    user_id = request.user.id
    owner = (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    result = await agent_profile_service.detach_skill_cloud_first(
        db,
        owner_id=owner,
        hasn_id=hasn_id,
        skill_id=body.skill_id,
        user_id=user_id,
    )
    return response_base.success(data=result)


@router.post(
    '/by-hasn-id/{hasn_id}/bundles/install',
    summary='daemon 端为 Agent 安装技能包 skill_pack（云端权威）',
    dependencies=[DependsJwtAuth],
)
async def install_agent_bundle(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: AgentBundleInstallRequest,
) -> ResponseSchemaModel[dict]:
    """为该 Agent 安装技能包：云端展开成员技能并入 hasn_agents.skills、记录包引用进
    hasn_agents.skill_bundles、bump profile_revision + 同步事件。返回 bundle 快照
    （含 hermes_yaml / 成员 skill_ids）供 daemon 回填本地 cache，由 runtime re-provision
    时物化 skill-bundles/*.yaml（云端只持有权威清单，不在此 push 文件）。
    """
    user_id = request.user.id
    owner = (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    result = await agent_profile_service.attach_bundle_cloud_first(
        db,
        owner_id=owner,
        hasn_id=hasn_id,
        package_id=body.package_id,
        version=body.version,
        user_id=user_id,
    )
    return response_base.success(data=result)


@router.patch(
    '/by-hasn-id/{hasn_id}/binding',
    summary='daemon 端更新 Agent binding 状态',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_agent_binding(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: UpdateAgentBindingRequest,
) -> ResponseSchemaModel[AgentSnapshot]:
    """daemon 在 activate binding 后调用，同步 binding_node_id 和 binding_status 到云端。"""
    result = await agent_profile_service.update_binding(
        db,
        hasn_id=hasn_id,
        request=body,
        user_id=request.user.id,
    )
    return response_base.success(data=result)


@router.post(
    '/{hasn_id}/social/toggle',
    summary='切换 Agent social_enabled (按 hasn_id, daemon 同步用)',
    dependencies=[DependsJwtAuth],
)
async def toggle_my_hasn_agent_social(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: ToggleSocialBody,
) -> ResponseModel:
    user_id = request.user.id
    owner = (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    agent = (
        await db.execute(
            sa.select(HasnAgents).where(
                HasnAgents.hasn_id == hasn_id,
                HasnAgents.owner_id == owner,
            )
        )
    ).scalar_one_or_none()
    if not agent:
        raise errors.NotFoundError(msg='Agent 不存在或不属于当前用户')
    agent.social_enabled = body.enabled
    if hasattr(agent, 'profile_revision'):
        agent.profile_revision = (agent.profile_revision or 1) + 1
    await db.flush()
    return response_base.success(
        data={
            'hasn_id': agent.hasn_id,
            'social_enabled': agent.social_enabled,
        }
    )


@router.delete(
    '/{pk}',
    summary='删除HASN Agent ',
    dependencies=[DependsJwtAuth],
)
async def delete_my_hasn_agents(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN Agent  ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_agents = await hasn_agents_service.get(db=db, pk=pk)
    if hasn_agents.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN Agent ')
    from backend.app.hasn.schema.hasn_agents import DeleteHasnAgentsParam

    count = await hasn_agents_service.delete(db=db, obj=DeleteHasnAgentsParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.post(
    '/by-hasn-id/{hasn_id}/heartbeat',
    summary='daemon 端上报 Agent 心跳',
    dependencies=[DependsJwtAuth],
)
async def report_agent_heartbeat(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description='Agent HASN ID, 如 a_xxx')],
    body: AgentHeartbeatRequest,
) -> ResponseSchemaModel[AgentHeartbeatResponse]:
    """daemon 定期调用，上报 agent 在线状态和心跳时间。"""
    result = await agent_profile_service.update_heartbeat(
        db,
        hasn_id=hasn_id,
        request=body,
        user_id=request.user.id,
    )
    return response_base.success(data=result)
