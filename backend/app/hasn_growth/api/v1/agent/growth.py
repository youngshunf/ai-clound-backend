"""获客 Agent API（设计 07 §3.4 agent scope）。

认证：Agent JWT（Authorization: Bearer <agent_jwt>）。身份恒取自 JWT claims（owner_user_id/agent_hasn_id），
绝不从请求体读身份。读类默认脱敏 PII（§10.2），仅持 growth:pii 增强 scope 才回明文。
这是 hasn-node hasn.growth.* 工具经 BackendGateway.for_agent 调用的云端落点（M4）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.schema.funnel import (
    CloseDealParam,
    CreateOpportunityParam,
    DismissLeadParam,
    LogActivityParam,
    QualifyLeadParam,
    SendOutreachParam,
    UpdateCustomerParam,
    UpdateStageParam,
)
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

# growth:pii 升级 scope → 读类回明文（默认脱敏）。
_PII_SCOPE = 'growth:pii'


def _reveal(agent: AgentTokenPayload) -> bool:
    return _PII_SCOPE in (agent.scopes or [])


# ---------------- 采集（收编子域包装：hasn.growth.collect.*） ----------------


@router.post('/collect', summary='[Agent] 发起采集任务（collect.start）', dependencies=[DependsAgentJwtAuth])
async def start_collect(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    obj: CreateLeadJobParam,
) -> ResponseModel:
    # 身份取自 JWT：恒落主人私有池（user_scope），分身无权操作公共池采集。
    payload = obj.model_copy(update={'user_id': agent.owner_user_id, 'lead_scope': 'user'})
    data = await lead_automation_business_service.create_job(db=db, obj=payload)
    return response_base.success(data=data)


@router.get('/collect/{job_id}', summary='[Agent] 采集任务状态（collect.status）', dependencies=[DependsAgentJwtAuth])
async def collect_status(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, job_id: int
) -> ResponseModel:
    data = await lead_automation_business_service.get_job(db, job_id=job_id, user_id=agent.owner_user_id)
    return response_base.success(data=data)


# ---------------- 线索 ----------------


@router.get('/leads', summary='[Agent] 检索线索', dependencies=[DependsAgentJwtAuth])
async def search_leads(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    data = await growth_funnel_service.search_leads(
        db, user_id=agent.owner_user_id, query=q, limit=limit, reveal_pii=_reveal(agent)
    )
    return response_base.success(data=data)


@router.get('/leads/{lead_contact_id}', summary='[Agent] 线索详情', dependencies=[DependsAgentJwtAuth])
async def get_lead(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, lead_contact_id: int
) -> ResponseModel:
    data = await growth_funnel_service.get_lead(
        db, user_id=agent.owner_user_id, lead_contact_id=lead_contact_id, reveal_pii=_reveal(agent)
    )
    return response_base.success(data=data)


@router.post('/leads/{lead_contact_id}/qualify', summary='[Agent] 线索晋级客户', dependencies=[DependsAgentJwtAuth])
async def qualify_lead(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    lead_contact_id: int,
    obj: QualifyLeadParam,
) -> ResponseModel:
    data = await growth_funnel_service.qualify_lead(
        db,
        user_id=agent.owner_user_id,
        lead_contact_id=lead_contact_id,
        profile=obj.profile,
        intent_score=obj.intent_score,
        owner_agent_id=agent.agent_hasn_id,
    )
    return response_base.success(data=data)


@router.post('/leads/{lead_contact_id}/dismiss', summary='[Agent] 线索不合格', dependencies=[DependsAgentJwtAuth])
async def dismiss_lead(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    lead_contact_id: int,
    obj: DismissLeadParam,
) -> ResponseModel:
    data = await growth_funnel_service.dismiss_lead(
        db, user_id=agent.owner_user_id, lead_contact_id=lead_contact_id, reason=obj.reason
    )
    return response_base.success(data=data)


# ---------------- 客户 ----------------


@router.get('/customers', summary='[Agent] 客户列表', dependencies=[DependsAgentJwtAuth])
async def list_customers(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    lifecycle_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    data = await growth_funnel_service.list_customers(
        db, user_id=agent.owner_user_id, lifecycle_status=lifecycle_status, limit=limit, reveal_pii=_reveal(agent)
    )
    return response_base.success(data=data)


@router.get('/customers/{customer_id}', summary='[Agent] 客户详情', dependencies=[DependsAgentJwtAuth])
async def get_customer(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, customer_id: int
) -> ResponseModel:
    data = await growth_funnel_service.get_customer(
        db, user_id=agent.owner_user_id, customer_id=customer_id, reveal_pii=_reveal(agent)
    )
    return response_base.success(data=data)


@router.get('/customers/{customer_id}/timeline', summary='[Agent] 客户时间线', dependencies=[DependsAgentJwtAuth])
async def customer_timeline(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession, customer_id: int
) -> ResponseModel:
    data = await growth_funnel_service.customer_timeline(db, user_id=agent.owner_user_id, customer_id=customer_id)
    return response_base.success(data=data)


@router.patch('/customers/{customer_id}', summary='[Agent] 更新客户画像', dependencies=[DependsAgentJwtAuth])
async def update_customer(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    customer_id: int,
    obj: UpdateCustomerParam,
) -> ResponseModel:
    data = await growth_funnel_service.update_customer_profile(
        db,
        user_id=agent.owner_user_id,
        customer_id=customer_id,
        profile=obj.profile,
        intent_score=obj.intent_score,
        tags=obj.tags,
        lifecycle_status=obj.lifecycle_status,
        followup_task_id=obj.followup_task_id,
    )
    return response_base.success(data=data)


@router.post('/customers/{customer_id}/activities', summary='[Agent] 记活动', dependencies=[DependsAgentJwtAuth])
async def log_activity(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    customer_id: int,
    obj: LogActivityParam,
) -> ResponseModel:
    data = await growth_funnel_service.log_activity(
        db,
        user_id=agent.owner_user_id,
        customer_id=customer_id,
        kind=obj.kind,
        content=obj.content,
        opportunity_id=obj.opportunity_id,
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
    )
    return response_base.success(data=data)


# ---------------- 触达 ----------------


@router.post('/outreach', summary='[Agent] 发起触达（进审批状态机）', dependencies=[DependsAgentJwtAuth])
async def send_outreach(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    obj: SendOutreachParam,
) -> ResponseModel:
    data = await growth_outreach_service.send_outreach(
        db,
        user_id=agent.owner_user_id,
        customer_id=obj.customer_id,
        channel=obj.channel,
        content=obj.content,
        agent_id=agent.agent_hasn_id,
        subject=obj.subject,
        intent_note=obj.intent_note,
        content_assets=obj.content_assets,
        opportunity_id=obj.opportunity_id,
    )
    # 触达待审批 → 通知主人去审批队列（仅 pending_approval；放行/拦截不打扰）。
    if data.get('status') == 'pending_approval':
        await growth_notification_service.outreach_pending_approval(
            db,
            agent=agent,
            message_id=int(data['id']),
            customer_id=obj.customer_id,
            channel=obj.channel,
        )
    return response_base.success(data=data)


@router.get('/outreach', summary='[Agent] 触达消息状态/回复（outreach.status）', dependencies=[DependsAgentJwtAuth])
async def outreach_status(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    customer_id: Annotated[int, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResponseModel:
    data = await growth_outreach_service.list_customer_outreach(
        db, user_id=agent.owner_user_id, customer_id=customer_id, limit=limit
    )
    return response_base.success(data=data)


# ---------------- 商机 / 成交 ----------------


@router.post('/opportunities', summary='[Agent] 立商机', dependencies=[DependsAgentJwtAuth])
async def create_opportunity(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    obj: CreateOpportunityParam,
) -> ResponseModel:
    data = await growth_opportunity_service.create_opportunity(
        db,
        user_id=agent.owner_user_id,
        customer_id=obj.customer_id,
        name=obj.name,
        amount=obj.amount,
        currency=obj.currency,
        stage=obj.stage,
        probability=obj.probability,
        created_by_kind='agent',
        actor_id=agent.agent_hasn_id,
    )
    return response_base.success(data=data)


@router.patch('/opportunities/{opportunity_id}/stage', summary='[Agent] 推进商机阶段', dependencies=[DependsAgentJwtAuth])
async def update_stage(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    opportunity_id: int,
    obj: UpdateStageParam,
) -> ResponseModel:
    data = await growth_opportunity_service.update_stage(
        db,
        user_id=agent.owner_user_id,
        opportunity_id=opportunity_id,
        stage=obj.stage,
        note=obj.note,
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
    )
    # 商机阶段变更 → 通知主人。
    await growth_notification_service.opportunity_stage_changed(
        db, agent=agent, opportunity_id=opportunity_id, stage=obj.stage, name=data.get('name')
    )
    return response_base.success(data=data)


@router.post('/opportunities/{opportunity_id}/close', summary='[Agent] 成交/流失登记', dependencies=[DependsAgentJwtAuth])
async def close_deal(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    opportunity_id: int,
    obj: CloseDealParam,
) -> ResponseModel:
    data = await growth_opportunity_service.close_deal(
        db,
        user_id=agent.owner_user_id,
        opportunity_id=opportunity_id,
        result=obj.result,
        amount=obj.amount,
        close_note=obj.close_note,
        lost_reason=obj.lost_reason,
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
    )
    # 成交/流失登记 → 通知主人（仅登记，不自动收款）。
    await growth_notification_service.deal_closed(
        db,
        agent=agent,
        opportunity_id=opportunity_id,
        result=obj.result,
        amount=data.get('amount'),
        name=data.get('name'),
    )
    return response_base.success(data=data)


# ---------------- 报表 ----------------


@router.get('/report/funnel', summary='[Agent] 漏斗总览', dependencies=[DependsAgentJwtAuth])
async def report_funnel(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth], db: CurrentSession
) -> ResponseModel:
    data = await growth_report_service.funnel_overview(db, user_id=agent.owner_user_id)
    return response_base.success(data=data)
