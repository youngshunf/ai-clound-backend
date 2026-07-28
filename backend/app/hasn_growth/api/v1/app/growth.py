"""获客用户端（owner）API（设计 07 §3.4 app scope + §8.2 审批队列）。

认证：Owner JWT。列表、详情和发送素材默认只返回脱敏数据；单个联系方式明文仅能通过
专用 reveal 端点短时读取。主人可审批触达队列（approve/edit/reject）、标记已发送
（manual_assist）、查看漏斗总览/分布。WebUI 经 daemon 薄代理调用本面（铁律）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from backend.app.hasn_growth.schema.contact_privacy import RevealContactChannelParam
from backend.app.hasn_growth.schema.funnel import (
    ApproveOutreachParam,
    AssignOwnerParam,
    ChannelSettingParam,
    CloseDealParam,
    CreateLeadParam,
    CreateOpportunityParam,
    DismissLeadParam,
    LogActivityParam,
    MarkSentParam,
    OptoutParam,
    QualifyLeadParam,
    RejectOutreachParam,
    RequestLeadsParam,
    UpdateStageParam,
)
from backend.app.hasn_growth.service import dispatch_service
from backend.app.hasn_growth.service.contact_privacy_service import contact_privacy_service
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.playbook_service import playbook_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.app.hasn_growth.service.scope_context import resolve_growth_scope
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.utils.trace_id import get_request_trace_id

router = APIRouter()


@router.post(
    '/contacts/channels/{channel_id}/reveal',
    summary='[Owner] 短时查看单个联系人渠道明文',
    dependencies=[DependsJwtAuth],
)
async def reveal_contact_channel(
    request: Request,
    response: Response,
    db: CurrentSession,
    channel_id: int,
    obj: RevealContactChannelParam,
) -> ResponseModel:
    """仅向当前 Owner 返回本主体下单个渠道，并禁止通用缓存保存响应。"""
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await contact_privacy_service.reveal_channel(
        db,
        channel_id=channel_id,
        actor_type='owner',
        actor_id=scope.owner_hasn_id or str(request.user.id),
        scope=scope,
        purpose=obj.purpose,
        trace_id=get_request_trace_id(),
    )
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response_base.success(data=data)


# ---------------- 线索池（主人看自己池子，可手动晋级/淘汰，默认脱敏） ----------------


@router.get('/leads', summary='[Owner] 线索池检索', dependencies=[DependsJwtAuth])
async def list_leads(
    request: Request,
    db: CurrentSession,
    q: Annotated[str | None, Query()] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    data = await growth_funnel_service.search_leads(
        db, user_id=request.user.id, query=q, min_score=min_score, limit=limit, reveal_pii=False
    )
    return response_base.success(data=data)


@router.post('/leads', summary='[Owner] 手动新建线索', dependencies=[DependsJwtAuth])
async def create_lead(request: Request, db: CurrentSessionTransaction, obj: CreateLeadParam) -> ResponseModel:
    # AI-native 宗旨：UI 给人操作。主人可手动建线索（owner 私有池），与分身 collect 采集互补。
    data = await growth_funnel_service.create_manual_lead(
        db,
        user_id=request.user.id,
        company_name=obj.company_name,
        contact_name=obj.contact_name,
        email=obj.email,
        phone=obj.phone,
        website=obj.website,
        industry=obj.industry,
        city=obj.city,
        note=obj.note,
        confidence_score=obj.intent_score,
    )
    return response_base.success(data=data)


@router.post('/leads/request', summary='[Owner] 请求线索（只看池·doc10）', dependencies=[DependsJwtAuth])
async def request_leads(request: Request, db: CurrentSessionTransaction, obj: RequestLeadsParam) -> ResponseModel:
    """**只看池**轻量入口（doc10 起降级）：只查公共池命中并交付脱敏摘要，不再触发旧爬虫补缺。

    找**新**线索（池中没有的）的主路已改为**派获客分身**（daemon POST /api/v1/growth/dispatch → 分身用
    hasn.growth.search_companies/lookup_company 读穿工具，未命中自动经 qcc 回流公共池，分身无需分辨来源）。
    主人显式建采集任务仍走 collect.start。
    """
    result = await lead_pool_query_service.request_leads(
        db,
        user_id=request.user.id,
        limit=obj.limit,
        industry=obj.industry,
        region=obj.region,
        keyword=obj.keyword,
        city=obj.city,
        reveal_pii=False,
    )
    return response_base.success(data=result)


@router.get('/leads/{lead_contact_id}', summary='[Owner] 线索详情', dependencies=[DependsJwtAuth])
async def get_lead(request: Request, db: CurrentSession, lead_contact_id: int) -> ResponseModel:
    data = await growth_funnel_service.get_lead(
        db, user_id=request.user.id, lead_contact_id=lead_contact_id, reveal_pii=False
    )
    return response_base.success(data=data)


@router.post('/leads/{lead_contact_id}/qualify', summary='[Owner] 线索晋级客户', dependencies=[DependsJwtAuth])
async def qualify_lead(
    request: Request, db: CurrentSessionTransaction, lead_contact_id: int, obj: QualifyLeadParam
) -> ResponseModel:
    # owner 手动晋级：无 owner_agent_id（非分身操作），后续触达由分身/主人按需发起。
    data = await growth_funnel_service.qualify_lead(
        db,
        user_id=request.user.id,
        lead_contact_id=lead_contact_id,
        profile=obj.profile,
        intent_score=obj.intent_score,
        owner_agent_id=None,
    )
    return response_base.success(data=data)


@router.post('/leads/{lead_contact_id}/dismiss', summary='[Owner] 线索不合格', dependencies=[DependsJwtAuth])
async def dismiss_lead(
    request: Request, db: CurrentSessionTransaction, lead_contact_id: int, obj: DismissLeadParam
) -> ResponseModel:
    data = await growth_funnel_service.dismiss_lead(
        db, user_id=request.user.id, lead_contact_id=lead_contact_id, reason=obj.reason
    )
    return response_base.success(data=data)


# ---------------- CRM 读（主人看自己数据，默认脱敏） ----------------


@router.get('/customers', summary='[Owner] 客户列表', dependencies=[DependsJwtAuth])
async def list_customers(
    request: Request,
    db: CurrentSession,
    lifecycle_status: Annotated[str | None, Query()] = None,
    view: Annotated[str, Query(description='企业视图意图 team/mine（个人模式无效；销售恒只见自己）')] = 'team',
    assignee: Annotated[str | None, Query(description='企业经理按负责人 hasn_id 过滤（个人/销售传入无害）')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await growth_funnel_service.list_customers(
        db,
        user_id=request.user.id,
        lifecycle_status=lifecycle_status,
        assignee=assignee,
        limit=limit,
        reveal_pii=False,
        scope=scope,
    )
    return response_base.success(data={'items': data, 'scope': scope.to_meta()})


@router.get('/customers/{customer_id}', summary='[Owner] 客户详情', dependencies=[DependsJwtAuth])
async def get_customer(request: Request, db: CurrentSession, customer_id: int) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_funnel_service.get_customer(
        db, user_id=request.user.id, customer_id=customer_id, reveal_pii=False, scope=scope
    )
    return response_base.success(data=data)


@router.get('/customers/{customer_id}/timeline', summary='[Owner] 客户时间线', dependencies=[DependsJwtAuth])
async def customer_timeline(request: Request, db: CurrentSession, customer_id: int) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_funnel_service.customer_timeline(
        db, user_id=request.user.id, customer_id=customer_id, scope=scope
    )
    return response_base.success(data=data)


@router.post('/customers/{customer_id}/activities', summary='[Owner] 记活动', dependencies=[DependsJwtAuth])
async def log_activity(
    request: Request, db: CurrentSessionTransaction, customer_id: int, obj: LogActivityParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_funnel_service.log_activity(
        db,
        user_id=request.user.id,
        customer_id=customer_id,
        kind=obj.kind,
        content=obj.content,
        opportunity_id=obj.opportunity_id,
        actor_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


@router.post(
    '/customers/{customer_id}/reassign',
    summary='[Owner] 分配/转移负责人（仅企业经理）',
    name='growth_app_reassign_customer',
    dependencies=[DependsJwtAuth],
)
async def reassign_customer(
    request: Request, db: CurrentSessionTransaction, customer_id: int, obj: AssignOwnerParam
) -> ResponseModel:
    # 经理按企业全量操作（view=team）；非经理由 service can_manage_assignment 拒。
    scope = await resolve_growth_scope(db, user_id=request.user.id, view='team')
    data = await growth_funnel_service.reassign_customer(
        db, user_id=request.user.id, customer_id=customer_id, new_assignee=obj.assignee, scope=scope
    )
    return response_base.success(data=data)


# ---------------- 触达审批队列（§8.2，业务态，不走 ask_gate） ----------------


@router.get('/outreach/pending', summary='[Owner] 待审批触达队列', dependencies=[DependsJwtAuth])
async def list_pending(
    request: Request,
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    # 审批恒按 assignee=自己（_approval_scope 强制 view=mine）：经理也只批自己名下，不代审。
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.list_pending_approvals(
        db, user_id=request.user.id, limit=limit, offset=offset, scope=scope
    )
    return response_base.success(data=data)


@router.get(
    '/outreach/team-overview',
    summary='[Owner] 团队待审批聚合（仅企业经理）',
    name='growth_app_team_approval_overview',
    dependencies=[DependsJwtAuth],
)
async def team_approval_overview(request: Request, db: CurrentSession) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view='team')
    data = await growth_outreach_service.team_approval_overview(db, user_id=request.user.id, scope=scope)
    return response_base.success(data=data)


@router.get('/customers/{customer_id}/outreach', summary='[Owner] 客户触达历史', dependencies=[DependsJwtAuth])
async def list_customer_outreach(request: Request, db: CurrentSession, customer_id: int) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.list_customer_outreach(
        db, user_id=request.user.id, customer_id=customer_id, scope=scope
    )
    return response_base.success(data=data)


@router.post('/outreach/{message_id}/approve', summary='[Owner] 批准触达（可改话术）', dependencies=[DependsJwtAuth])
async def approve_outreach(
    request: Request, db: CurrentSessionTransaction, message_id: int, obj: ApproveOutreachParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.approve_outreach(
        db,
        user_id=request.user.id,
        message_id=message_id,
        approver_user_id=request.user.id,
        edited_content=obj.edited_content,
        scope=scope,
    )
    return response_base.success(data=data)


@router.post('/outreach/{message_id}/reject', summary='[Owner] 拒绝触达', dependencies=[DependsJwtAuth])
async def reject_outreach(
    request: Request, db: CurrentSessionTransaction, message_id: int, obj: RejectOutreachParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.reject_outreach(
        db,
        user_id=request.user.id,
        message_id=message_id,
        approver_user_id=request.user.id,
        reason=obj.reason,
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/outreach/{message_id}/send-material',
    summary='[Owner] manual_assist 复制发送非 PII 素材包',
    dependencies=[DependsJwtAuth],
)
async def send_material(request: Request, db: CurrentSession, message_id: int) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.build_send_material(
        db, user_id=request.user.id, message_id=message_id, scope=scope
    )
    return response_base.success(data=data)


@router.post(
    '/outreach/{message_id}/sent', summary='[Owner] 标记已发送（manual_assist）', dependencies=[DependsJwtAuth]
)
async def mark_sent(
    request: Request, db: CurrentSessionTransaction, message_id: int, obj: MarkSentParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.mark_sent(
        db, user_id=request.user.id, message_id=message_id, channel_actual=obj.channel_actual, scope=scope
    )
    return response_base.success(data=data)


# ---------------- 商机 / 成交（主人也可手动） ----------------


@router.post('/opportunities', summary='[Owner] 立商机', dependencies=[DependsJwtAuth])
async def create_opportunity(
    request: Request, db: CurrentSessionTransaction, obj: CreateOpportunityParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.create_opportunity(
        db,
        user_id=request.user.id,
        customer_id=obj.customer_id,
        name=obj.name,
        amount=obj.amount,
        currency=obj.currency,
        stage=obj.stage,
        probability=obj.probability,
        created_by_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


@router.get('/opportunities', summary='[Owner] 商机列表', dependencies=[DependsJwtAuth])
async def list_opportunities(
    request: Request,
    db: CurrentSession,
    customer_id: Annotated[int | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
    view: Annotated[str, Query(description='企业视图意图 team/mine')] = 'team',
    assignee: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await growth_opportunity_service.list_opportunities(
        db,
        user_id=request.user.id,
        customer_id=customer_id,
        open_only=open_only,
        assignee=assignee,
        limit=limit,
        scope=scope,
    )
    return response_base.success(data=data)


@router.patch('/opportunities/{opportunity_id}/stage', summary='[Owner] 推进商机阶段', dependencies=[DependsJwtAuth])
async def update_stage(
    request: Request, db: CurrentSessionTransaction, opportunity_id: int, obj: UpdateStageParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.update_stage(
        db,
        user_id=request.user.id,
        opportunity_id=opportunity_id,
        stage=obj.stage,
        note=obj.note,
        actor_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


@router.post('/opportunities/{opportunity_id}/close', summary='[Owner] 成交/流失登记', dependencies=[DependsJwtAuth])
async def close_deal(
    request: Request, db: CurrentSessionTransaction, opportunity_id: int, obj: CloseDealParam
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.close_deal(
        db,
        user_id=request.user.id,
        opportunity_id=opportunity_id,
        result=obj.result,
        amount=obj.amount,
        close_note=obj.close_note,
        lost_reason=obj.lost_reason,
        actor_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


# ---------------- 退订登记 ----------------


@router.post('/optout', summary='[Owner] 登记客户退订', dependencies=[DependsJwtAuth])
async def register_optout(request: Request, db: CurrentSessionTransaction, obj: OptoutParam) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.register_optout(
        db,
        user_id=request.user.id,
        channel=obj.channel,
        address=obj.address,
        reason=obj.reason,
        source='owner',
        scope=scope,
    )
    return response_base.success(data=data)


# ---------------- 渠道设置（J1 微信自动发送开关，发送 worker 据此放行） ----------------


@router.get('/channel-setting', summary='[Owner] 渠道设置（微信自动发送开关）', dependencies=[DependsJwtAuth])
async def get_channel_setting(request: Request, db: CurrentSession) -> ResponseModel:
    data = await dispatch_service.get_channel_setting(db, user_id=request.user.id)
    return response_base.success(data=data)


@router.put(
    '/channel-setting', summary='[Owner] 设置微信自动发送（J1，UI 二次确认后写入）', dependencies=[DependsJwtAuth]
)
async def update_channel_setting(
    request: Request, db: CurrentSessionTransaction, obj: ChannelSettingParam
) -> ResponseModel:
    await dispatch_service.set_wechat_auto_send(db, user_id=request.user.id, confirmed=obj.wechat_auto_send_confirmed)
    data = await dispatch_service.get_channel_setting(db, user_id=request.user.id)
    return response_base.success(data=data)


# ---------------- 打法管理（只读：内置 ∪ 本人自定义） ----------------


@router.get(
    '/playbooks', summary='[Owner] 打法列表（内置 + 自定义 + 企业，目标/节奏/语气/止损）', dependencies=[DependsJwtAuth]
)
async def list_playbooks(request: Request, db: CurrentSession) -> ResponseModel:
    # 企业上下文成员额外可见本企业 playbook（GE3 自播种产物）；个人上下文 enterprise_id 为 None。
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await playbook_service.list_for_owner(db, user_id=request.user.id, enterprise_id=scope.enterprise_id)
    return response_base.success(data=data)


# ---------------- 报表 ----------------


@router.get('/report/funnel', summary='[Owner] 漏斗总览', dependencies=[DependsJwtAuth])
async def report_funnel(request: Request, db: CurrentSession, view: Annotated[str, Query()] = 'team') -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await growth_report_service.funnel_overview(db, user_id=request.user.id, scope=scope)
    return response_base.success(data=data)


@router.get('/report/stages', summary='[Owner] 商机阶段分布', dependencies=[DependsJwtAuth])
async def report_stages(request: Request, db: CurrentSession, view: Annotated[str, Query()] = 'team') -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await growth_report_service.stage_distribution(db, user_id=request.user.id, scope=scope)
    return response_base.success(data=data)


@router.get('/report/lifecycle', summary='[Owner] 客户生命周期分布', dependencies=[DependsJwtAuth])
async def report_lifecycle(
    request: Request, db: CurrentSession, view: Annotated[str, Query()] = 'team'
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await growth_report_service.lifecycle_distribution(db, user_id=request.user.id, scope=scope)
    return response_base.success(data=data)
