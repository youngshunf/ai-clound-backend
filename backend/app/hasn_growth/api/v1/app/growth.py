"""获客用户端（owner）API（设计 07 §3.4 app scope + §8.2 审批队列）。

认证：Owner JWT。列表、详情和发送素材默认只返回脱敏数据；单个联系方式明文仅能通过
专用 reveal 端点短时读取。主人可审批触达队列（approve/edit/reject）、标记已发送
（manual_assist）、查看漏斗总览/分布。WebUI 经 daemon 薄代理调用本面（铁律）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

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
    EditOutreachParam,
    LogActivityParam,
    ManualAttestOutreachParam,
    MarkSentParam,
    OptoutParam,
    QualifyLeadParam,
    RejectOutreachParam,
    RequestLeadsParam,
    UpdateStageParam,
    VersionedApproveOutreachParam,
    VersionedRejectOutreachParam,
)
from backend.app.hasn_growth.schema.project_app import EnableGrowthProjectBody
from backend.app.hasn_growth.schema.project_lead import (
    ProjectLeadAssignBody,
    ProjectLeadBatchBody,
    ProjectLeadStatusBody,
)
from backend.app.hasn_growth.schema.project_profile import (
    AdoptGrowthPlaybookBody,
    BindGrowthKnowledgeBody,
    ReviewGrowthProfileSuggestionBody,
)
from backend.app.hasn_growth.service import dispatch_service
from backend.app.hasn_growth.service.contact_privacy_service import contact_privacy_service
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.growth_landing_service import growth_landing_service
from backend.app.hasn_growth.service.growth_profile_service import (
    growth_profile_service,
)
from backend.app.hasn_growth.service.growth_project_app_service import growth_project_app_service
from backend.app.hasn_growth.service.growth_project_provision_service import (
    enqueue_growth_provision_after_commit,
    growth_project_provision_service,
)
from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.playbook_service import playbook_service
from backend.app.hasn_growth.service.project_customer_service import (
    project_customer_service,
)
from backend.app.hasn_growth.service.project_lead_service import project_lead_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.app.hasn_growth.service.scope_context import resolve_growth_scope
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.utils.trace_id import get_request_trace_id

router = APIRouter()


async def _resolve_owner_hasn_id(db: CurrentSession, request: Request) -> str:
    """只信任 Owner JWT 解析出的主人身份，不接收客户端自报 owner。"""
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    if not scope.owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号尚未绑定主人身份')
    return scope.owner_hasn_id


@router.get(
    '/projects/by-platform/{platform_project_id}',
    summary='[Owner] 当前平台项目的获客漏斗',
    dependencies=[DependsJwtAuth],
)
async def get_growth_project_by_platform(
    request: Request,
    db: CurrentSession,
    platform_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.get_for_platform(
        db,
        owner_hasn_id=owner_hasn_id,
        platform_project_id=platform_project_id,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}',
    summary='[Owner] 按云端 ID 读取获客漏斗',
    dependencies=[DependsJwtAuth],
)
async def get_growth_project(
    request: Request,
    db: CurrentSession,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.get_by_id(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects',
    summary='[Owner] 为平台项目启用获客漏斗',
    dependencies=[DependsJwtAuth],
)
async def enable_growth_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: EnableGrowthProjectBody,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.enable(
        db,
        owner_hasn_id=owner_hasn_id,
        owner_user_id=request.user.id,
        platform_project_id=obj.platform_project_id,
        name=obj.name,
        tagline=obj.tagline,
        command_id=str(obj.trace_id),
        idempotency_key=obj.idempotency_key,
    )
    enqueue_growth_provision_after_commit(
        db,
        data['growth_project']['id'],
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/pause',
    summary='[Owner] 暂停获客自动动作',
    dependencies=[DependsJwtAuth],
)
async def pause_growth_project(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.pause(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/archive',
    summary='[Owner] 归档获客项目',
    dependencies=[DependsJwtAuth],
)
async def archive_growth_project(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.archive(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/restore',
    summary='[Owner] 恢复归档获客项目为暂停态',
    dependencies=[DependsJwtAuth],
)
async def restore_growth_project(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.restore(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/resume',
    summary='[Owner] 显式恢复获客自动动作',
    dependencies=[DependsJwtAuth],
)
async def resume_growth_project(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_project_app_service.resume(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/provision/retry',
    summary='[Owner] 从失败步骤重试获客基础资源开通',
    dependencies=[DependsJwtAuth],
)
async def retry_growth_project_provision(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    growth = await growth_project_provision_service.retry(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    enqueue_growth_provision_after_commit(db, growth.id)
    data = await growth_project_app_service.get_by_id(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth.id,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/profile',
    summary='[Owner] 读取已确认画像、版本历史与待确认建议',
    dependencies=[DependsJwtAuth],
)
async def get_growth_project_profile(
    request: Request,
    db: CurrentSession,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_profile_service.project_summary(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/overview',
    summary='[Owner] 当前获客项目经营总览',
    dependencies=[DependsJwtAuth],
)
async def get_growth_project_overview(
    request: Request,
    db: CurrentSession,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    project = await growth_project_app_service.get_by_id(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    report = await growth_report_service.project_overview(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(
        data={
            'project': project,
            'report': report,
        }
    )


@router.put(
    '/projects/{growth_project_id}/knowledge',
    summary='[Owner] 绑定或改绑同项目 Knowledge',
    dependencies=[DependsJwtAuth],
)
async def bind_growth_project_knowledge(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
    obj: BindGrowthKnowledgeBody,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_profile_service.bind_knowledge(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
        kb_id=obj.kb_id,
        expected_profile_version=obj.expected_profile_version,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/knowledge/reconcile',
    summary='[Owner] 对账修复 Growth 与 Knowledge 绑定',
    dependencies=[DependsJwtAuth],
)
async def reconcile_growth_project_knowledge(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_profile_service.reconcile_knowledge_binding(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/landing',
    summary='[Owner] 读取落地页依赖、站点、绑定与留资状态',
    dependencies=[DependsJwtAuth],
)
async def get_growth_project_landing(
    request: Request,
    db: CurrentSession,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_landing_service.status(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/landing/reconcile',
    summary='[Owner] 对账并绑定该 Growth 来源的 Publish 站点',
    dependencies=[DependsJwtAuth],
)
async def reconcile_growth_project_landing(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_landing_service.reconcile(
        db,
        owner_hasn_id=owner_hasn_id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/profile/suggestions/{suggestion_id}/review',
    summary='[Owner] 接受或拒绝画像建议',
    dependencies=[DependsJwtAuth],
)
async def review_growth_profile_suggestion(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
    suggestion_id: int,
    obj: ReviewGrowthProfileSuggestionBody,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_profile_service.review_suggestion(
        db,
        owner_hasn_id=owner_hasn_id,
        owner_user_id=request.user.id,
        growth_project_id=growth_project_id,
        suggestion_id=suggestion_id,
        decision=obj.decision,
    )
    return response_base.success(data=data)


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


# ---------------- 项目线索（项目关联行是状态、评分与来源的权威） ----------------


@router.post(
    '/projects/{growth_project_id}/leads/import',
    summary='[Owner] 稳定批次导入项目线索',
    dependencies=[DependsJwtAuth],
)
async def import_project_leads(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    obj: ProjectLeadBatchBody,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await project_lead_service.ingest_batch(
        db,
        growth_project_id=growth_project_id,
        batch_id=obj.batch_id,
        items=obj.items,
        scope=scope,
        actor_kind='owner',
        actor_id=scope.owner_hasn_id or str(request.user.id),
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/leads',
    summary='[Owner] 分页读取项目线索',
    dependencies=[DependsJwtAuth],
)
async def list_project_leads(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    freshness: Annotated[str | None, Query()] = None,
    view: Annotated[str, Query()] = 'team',
    assignee: Annotated[str | None, Query(max_length=40)] = None,
) -> ResponseModel:
    scope = await resolve_growth_scope(
        db,
        user_id=request.user.id,
        view=view,
    )
    data = await project_lead_service.list_project_leads(
        db,
        growth_project_id=growth_project_id,
        scope=scope,
        page=page,
        size=size,
        status=status,
        query=q,
        min_score=min_score,
        freshness=freshness,
        assignee=assignee,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/leads/{project_lead_id}/qualify',
    summary='[Owner] 项目线索晋级客户并建立接续任务',
    dependencies=[DependsJwtAuth],
)
async def qualify_project_lead(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    project_lead_id: int,
    obj: QualifyLeadParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    if not scope.owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号尚未绑定主人身份')
    data = await project_lead_service.qualify_project_lead(
        db,
        growth_project_id=growth_project_id,
        project_lead_id=project_lead_id,
        scope=scope,
        profile=obj.profile,
        intent_score=obj.intent_score,
        actor_kind='owner',
        actor_id=scope.owner_hasn_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/leads/{project_lead_id}/status',
    summary='[Owner] 忽略或恢复项目线索',
    dependencies=[DependsJwtAuth],
)
async def change_project_lead_status(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    project_lead_id: int,
    obj: ProjectLeadStatusBody,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await project_lead_service.change_lead_status(
        db,
        growth_project_id=growth_project_id,
        project_lead_id=project_lead_id,
        action=obj.action,
        reason=obj.reason,
        scope=scope,
    )
    return response_base.success(data=data)


@router.put(
    '/projects/{growth_project_id}/leads/{project_lead_id}/assignee',
    summary='[Owner] 分配项目线索负责人（仅企业经理）',
    dependencies=[DependsJwtAuth],
)
async def assign_project_lead(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    project_lead_id: int,
    obj: ProjectLeadAssignBody,
) -> ResponseModel:
    scope = await resolve_growth_scope(
        db,
        user_id=request.user.id,
        view='team',
    )
    data = await project_lead_service.assign_lead(
        db,
        growth_project_id=growth_project_id,
        project_lead_id=project_lead_id,
        assignee=obj.assignee,
        scope=scope,
    )
    return response_base.success(data=data)


# ---------------- 兼容线索池（旧调用面，项目页不再使用） ----------------


@router.get('/leads', summary='[Owner] 线索池检索', dependencies=[DependsJwtAuth])
async def list_leads(
    request: Request,
    db: CurrentSession,
    q: Annotated[str | None, Query()] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    growth_project_id: Annotated[UUID | None, Query()] = None,
) -> ResponseModel:
    data = await growth_funnel_service.search_leads(
        db,
        user_id=request.user.id,
        query=q,
        min_score=min_score,
        limit=limit,
        reveal_pii=False,
        growth_project_id=(str(growth_project_id) if growth_project_id is not None else None),
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
async def get_lead(
    request: Request,
    db: CurrentSession,
    lead_contact_id: int,
    growth_project_id: Annotated[UUID | None, Query()] = None,
) -> ResponseModel:
    data = await growth_funnel_service.get_lead(
        db,
        user_id=request.user.id,
        lead_contact_id=lead_contact_id,
        reveal_pii=False,
        growth_project_id=(str(growth_project_id) if growth_project_id is not None else None),
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


@router.get(
    '/projects/{growth_project_id}/customers',
    summary='[Owner] 项目客户分页列表',
    dependencies=[DependsJwtAuth],
)
async def list_project_customers(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    lifecycle_status: Annotated[str | None, Query()] = None,
    view: Annotated[str, Query(description='企业视图意图 team/mine')] = 'team',
    assignee: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await project_customer_service.list_customers(
        db,
        growth_project_id=growth_project_id,
        scope=scope,
        page=page,
        size=size,
        lifecycle_status=lifecycle_status,
        assignee=assignee,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/customers/{customer_id}',
    summary='[Owner] 项目客户脱敏详情',
    dependencies=[DependsJwtAuth],
)
async def get_project_customer(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    customer_id: int,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await project_customer_service.get_customer(
        db,
        growth_project_id=growth_project_id,
        customer_id=customer_id,
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/customers/{customer_id}/detail',
    summary='[Owner] 项目客户接续详情',
    dependencies=[DependsJwtAuth],
)
async def get_project_customer_detail(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    customer_id: int,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await project_customer_service.get_customer_detail(
        db,
        growth_project_id=growth_project_id,
        customer_id=customer_id,
        scope=scope,
    )
    return response_base.success(data=data)


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


@router.get(
    '/projects/{growth_project_id}/outreach/pending',
    summary='[Owner] 项目待审批触达队列',
    dependencies=[DependsJwtAuth],
)
async def list_project_pending(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.list_pending_approvals(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        limit=limit,
        offset=offset,
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/outreach/team-overview',
    summary='[Owner] 项目团队待审批聚合（仅企业经理）',
    dependencies=[DependsJwtAuth],
)
async def project_team_approval_overview(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
) -> ResponseModel:
    scope = await resolve_growth_scope(
        db,
        user_id=request.user.id,
        view='team',
    )
    data = await growth_outreach_service.team_approval_overview(
        db,
        user_id=request.user.id,
        scope=scope,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/outreach/{message_id}/approve',
    summary='[Owner] 按内容版本批准项目触达',
    dependencies=[DependsJwtAuth],
)
async def approve_project_outreach(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    message_id: int,
    obj: VersionedApproveOutreachParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.approve_outreach(
        db,
        user_id=request.user.id,
        message_id=message_id,
        approver_user_id=request.user.id,
        edited_content=obj.edited_content,
        expected_content_version=obj.expected_content_version,
        growth_project_id=growth_project_id,
        scope=scope,
    )
    return response_base.success(data=data)


@router.patch(
    '/projects/{growth_project_id}/outreach/{message_id}',
    summary='[Owner] 改稿并使旧批准失效',
    dependencies=[DependsJwtAuth],
)
async def edit_project_outreach(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    message_id: int,
    obj: EditOutreachParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.edit_outreach(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        message_id=message_id,
        expected_content_version=obj.expected_content_version,
        content=obj.content,
        subject=obj.subject,
        channel=obj.channel,
        content_assets=obj.content_assets,
        scope=scope,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/outreach/{message_id}/reject',
    summary='[Owner] 按内容版本拒绝项目触达',
    dependencies=[DependsJwtAuth],
)
async def reject_project_outreach(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    message_id: int,
    obj: VersionedRejectOutreachParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.reject_outreach(
        db,
        user_id=request.user.id,
        message_id=message_id,
        approver_user_id=request.user.id,
        reason=obj.reason,
        expected_content_version=obj.expected_content_version,
        growth_project_id=growth_project_id,
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/outreach/{message_id}/send-material',
    summary='[Owner] 读取已冻结的人工发送素材',
    dependencies=[DependsJwtAuth],
)
async def project_send_material(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    message_id: int,
    expected_content_version: Annotated[int, Query(ge=1)],
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_outreach_service.build_send_material(
        db,
        user_id=request.user.id,
        message_id=message_id,
        expected_content_version=expected_content_version,
        growth_project_id=growth_project_id,
        scope=scope,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/outreach/{message_id}/manual-attest',
    summary='[Owner] 记录人工发送证明（不等于渠道送达）',
    dependencies=[DependsJwtAuth],
)
async def attest_project_manual_send(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    message_id: int,
    obj: ManualAttestOutreachParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    actor_id = await _resolve_owner_hasn_id(db, request)
    data = await growth_outreach_service.attest_manual_send(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        message_id=message_id,
        expected_content_version=obj.expected_content_version,
        actor_id=actor_id,
        channel_actual=obj.channel_actual,
        proof=obj.proof,
        idempotency_key=obj.idempotency_key,
        scope=scope,
    )
    return response_base.success(data=data)


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
        expected_content_version=obj.expected_content_version,
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
        expected_content_version=obj.expected_content_version,
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


@router.post(
    '/projects/{growth_project_id}/opportunities',
    summary='[Owner] 项目内立商机',
    dependencies=[DependsJwtAuth],
)
async def create_project_opportunity(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    obj: CreateOpportunityParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.create_opportunity(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        customer_id=obj.customer_id,
        name=obj.name,
        amount=obj.amount,
        currency=obj.currency,
        stage=obj.stage,
        probability=obj.probability,
        idempotency_key=obj.idempotency_key,
        created_by_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/opportunities',
    summary='[Owner] 项目商机列表',
    dependencies=[DependsJwtAuth],
)
async def list_project_opportunities(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    customer_id: Annotated[int | None, Query()] = None,
    stage: Annotated[str | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,  # ruff: ignore[boolean-default-value-positional-argument]
    view: Annotated[str, Query(description='企业视图意图 team/mine')] = 'team',
    assignee: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id, view=view)
    data = await growth_opportunity_service.list_opportunities(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        customer_id=customer_id,
        stage=stage,
        open_only=open_only,
        assignee=assignee,
        limit=limit,
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/opportunities/{opportunity_id}',
    summary='[Owner] 项目商机摘要',
    dependencies=[DependsJwtAuth],
)
async def get_project_opportunity(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    opportunity_id: int,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.get_opportunity(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        opportunity_id=opportunity_id,
        scope=scope,
    )
    return response_base.success(data=data)


@router.get(
    '/projects/{growth_project_id}/opportunities/{opportunity_id}/detail',
    summary='[Owner] 项目商机接续详情',
    dependencies=[DependsJwtAuth],
)
async def get_project_opportunity_detail(
    request: Request,
    db: CurrentSession,
    growth_project_id: UUID,
    opportunity_id: int,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.get_opportunity_detail(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        opportunity_id=opportunity_id,
        scope=scope,
    )
    return response_base.success(data=data)


@router.patch(
    '/projects/{growth_project_id}/opportunities/{opportunity_id}/stage',
    summary='[Owner] 项目商机阶段变更',
    dependencies=[DependsJwtAuth],
)
async def update_project_opportunity_stage(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    opportunity_id: int,
    obj: UpdateStageParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.update_stage(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        opportunity_id=opportunity_id,
        stage=obj.stage,
        note=obj.note,
        expected_version=obj.expected_version,
        idempotency_key=obj.idempotency_key,
        actor_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/opportunities/{opportunity_id}/close',
    summary='[Owner] 项目商机成交或流失登记',
    dependencies=[DependsJwtAuth],
)
async def close_project_deal(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: UUID,
    opportunity_id: int,
    obj: CloseDealParam,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.close_deal(
        db,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        opportunity_id=opportunity_id,
        result=obj.result,
        amount=obj.amount,
        currency=obj.currency,
        close_note=obj.close_note,
        lost_reason=obj.lost_reason,
        expected_version=obj.expected_version,
        idempotency_key=obj.idempotency_key,
        actor_kind='owner',
        actor_id=str(request.user.id),
        scope=scope,
    )
    return response_base.success(data=data)


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
        idempotency_key=obj.idempotency_key,
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
    open_only: Annotated[bool, Query()] = False,  # ruff: ignore[boolean-default-value-positional-argument]
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


@router.get(
    '/opportunities/{opportunity_id}',
    summary='[Owner] 商机详情',
    dependencies=[DependsJwtAuth],
)
async def get_opportunity(
    request: Request,
    db: CurrentSession,
    opportunity_id: int,
) -> ResponseModel:
    scope = await resolve_growth_scope(db, user_id=request.user.id)
    data = await growth_opportunity_service.get_opportunity(
        db,
        user_id=request.user.id,
        opportunity_id=opportunity_id,
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
        expected_version=obj.expected_version,
        idempotency_key=obj.idempotency_key,
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
        currency=obj.currency,
        close_note=obj.close_note,
        lost_reason=obj.lost_reason,
        expected_version=obj.expected_version,
        idempotency_key=obj.idempotency_key,
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


@router.get(
    '/projects/{growth_project_id}/playbooks',
    summary='[Owner] 当前项目的可采用打法与采用状态',
    dependencies=[DependsJwtAuth],
)
async def list_growth_project_playbooks(
    request: Request,
    db: CurrentSession,
    growth_project_id: str,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await playbook_service.recommend_for_project(
        db,
        owner_hasn_id=owner_hasn_id,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
    )
    return response_base.success(data=data)


@router.post(
    '/projects/{growth_project_id}/playbooks/{playbook_id}/adopt',
    summary='[Owner] 显式采用并冻结打法版本',
    dependencies=[DependsJwtAuth],
)
async def adopt_growth_project_playbook(
    request: Request,
    db: CurrentSessionTransaction,
    growth_project_id: str,
    playbook_id: int,
    obj: AdoptGrowthPlaybookBody,
) -> ResponseModel:
    owner_hasn_id = await _resolve_owner_hasn_id(db, request)
    data = await playbook_service.adopt_for_project(
        db,
        owner_hasn_id=owner_hasn_id,
        user_id=request.user.id,
        growth_project_id=growth_project_id,
        playbook_id=playbook_id,
        expected_playbook_version=obj.expected_playbook_version,
        configuration=obj.configuration,
    )
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
