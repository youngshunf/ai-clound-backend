"""获客用户端（owner）API（设计 07 §3.4 app scope + §8.2 审批队列）。

认证：Owner JWT。主人查看自己的 CRM（明文 PII，自己的数据）、审批触达队列（approve/edit/reject）、
标记已发送（manual_assist）、看漏斗总览/分布。WebUI 经 daemon 薄代理调用本面（铁律）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.app.hasn_growth.schema.funnel import (
    ApproveOutreachParam,
    CloseDealParam,
    CreateOpportunityParam,
    LogActivityParam,
    MarkSentParam,
    OptoutParam,
    RejectOutreachParam,
    UpdateStageParam,
)
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


# ---------------- CRM 读（主人看自己数据，回明文） ----------------


@router.get('/customers', summary='[Owner] 客户列表', dependencies=[DependsJwtAuth])
async def list_customers(
    request: Request,
    db: CurrentSession,
    lifecycle_status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> ResponseModel:
    data = await growth_funnel_service.list_customers(
        db, user_id=request.user.id, lifecycle_status=lifecycle_status, limit=limit, reveal_pii=True
    )
    return response_base.success(data=data)


@router.get('/customers/{customer_id}', summary='[Owner] 客户详情', dependencies=[DependsJwtAuth])
async def get_customer(request: Request, db: CurrentSession, customer_id: int) -> ResponseModel:
    data = await growth_funnel_service.get_customer(db, user_id=request.user.id, customer_id=customer_id, reveal_pii=True)
    return response_base.success(data=data)


@router.get('/customers/{customer_id}/timeline', summary='[Owner] 客户时间线', dependencies=[DependsJwtAuth])
async def customer_timeline(request: Request, db: CurrentSession, customer_id: int) -> ResponseModel:
    data = await growth_funnel_service.customer_timeline(db, user_id=request.user.id, customer_id=customer_id)
    return response_base.success(data=data)


@router.post('/customers/{customer_id}/activities', summary='[Owner] 记活动', dependencies=[DependsJwtAuth])
async def log_activity(
    request: Request, db: CurrentSessionTransaction, customer_id: int, obj: LogActivityParam
) -> ResponseModel:
    data = await growth_funnel_service.log_activity(
        db,
        user_id=request.user.id,
        customer_id=customer_id,
        kind=obj.kind,
        content=obj.content,
        opportunity_id=obj.opportunity_id,
        actor_kind='owner',
        actor_id=str(request.user.id),
    )
    return response_base.success(data=data)


# ---------------- 触达审批队列（§8.2，业务态，不走 ask_gate） ----------------


@router.get('/outreach/pending', summary='[Owner] 待审批触达队列', dependencies=[DependsJwtAuth])
async def list_pending(
    request: Request,
    db: CurrentSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ResponseModel:
    data = await growth_outreach_service.list_pending_approvals(
        db, user_id=request.user.id, limit=limit, offset=offset
    )
    return response_base.success(data=data)


@router.get('/customers/{customer_id}/outreach', summary='[Owner] 客户触达历史', dependencies=[DependsJwtAuth])
async def list_customer_outreach(request: Request, db: CurrentSession, customer_id: int) -> ResponseModel:
    data = await growth_outreach_service.list_customer_outreach(db, user_id=request.user.id, customer_id=customer_id)
    return response_base.success(data=data)


@router.post('/outreach/{message_id}/approve', summary='[Owner] 批准触达（可改话术）', dependencies=[DependsJwtAuth])
async def approve_outreach(
    request: Request, db: CurrentSessionTransaction, message_id: int, obj: ApproveOutreachParam
) -> ResponseModel:
    data = await growth_outreach_service.approve_outreach(
        db, user_id=request.user.id, message_id=message_id, approver_user_id=request.user.id, edited_content=obj.edited_content
    )
    return response_base.success(data=data)


@router.post('/outreach/{message_id}/reject', summary='[Owner] 拒绝触达', dependencies=[DependsJwtAuth])
async def reject_outreach(
    request: Request, db: CurrentSessionTransaction, message_id: int, obj: RejectOutreachParam
) -> ResponseModel:
    data = await growth_outreach_service.reject_outreach(
        db, user_id=request.user.id, message_id=message_id, approver_user_id=request.user.id, reason=obj.reason
    )
    return response_base.success(data=data)


@router.get(
    '/outreach/{message_id}/send-material',
    summary='[Owner] manual_assist 复制发送素材包（明文联系方式，pii_read 审计）',
    dependencies=[DependsJwtAuth],
)
async def send_material(request: Request, db: CurrentSessionTransaction, message_id: int) -> ResponseModel:
    data = await growth_outreach_service.build_send_material(
        db, user_id=request.user.id, message_id=message_id, actor_user_id=request.user.id
    )
    return response_base.success(data=data)


@router.post('/outreach/{message_id}/sent', summary='[Owner] 标记已发送（manual_assist）', dependencies=[DependsJwtAuth])
async def mark_sent(
    request: Request, db: CurrentSessionTransaction, message_id: int, obj: MarkSentParam
) -> ResponseModel:
    data = await growth_outreach_service.mark_sent(
        db, user_id=request.user.id, message_id=message_id, channel_actual=obj.channel_actual
    )
    return response_base.success(data=data)


# ---------------- 商机 / 成交（主人也可手动） ----------------


@router.post('/opportunities', summary='[Owner] 立商机', dependencies=[DependsJwtAuth])
async def create_opportunity(
    request: Request, db: CurrentSessionTransaction, obj: CreateOpportunityParam
) -> ResponseModel:
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
    )
    return response_base.success(data=data)


@router.get('/opportunities', summary='[Owner] 商机列表', dependencies=[DependsJwtAuth])
async def list_opportunities(
    request: Request,
    db: CurrentSession,
    customer_id: int | None = Query(default=None),
    open_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> ResponseModel:
    data = await growth_opportunity_service.list_opportunities(
        db, user_id=request.user.id, customer_id=customer_id, open_only=open_only, limit=limit
    )
    return response_base.success(data=data)


@router.patch('/opportunities/{opportunity_id}/stage', summary='[Owner] 推进商机阶段', dependencies=[DependsJwtAuth])
async def update_stage(
    request: Request, db: CurrentSessionTransaction, opportunity_id: int, obj: UpdateStageParam
) -> ResponseModel:
    data = await growth_opportunity_service.update_stage(
        db, user_id=request.user.id, opportunity_id=opportunity_id, stage=obj.stage, note=obj.note,
        actor_kind='owner', actor_id=str(request.user.id),
    )
    return response_base.success(data=data)


@router.post('/opportunities/{opportunity_id}/close', summary='[Owner] 成交/流失登记', dependencies=[DependsJwtAuth])
async def close_deal(
    request: Request, db: CurrentSessionTransaction, opportunity_id: int, obj: CloseDealParam
) -> ResponseModel:
    data = await growth_opportunity_service.close_deal(
        db, user_id=request.user.id, opportunity_id=opportunity_id, result=obj.result, amount=obj.amount,
        close_note=obj.close_note, lost_reason=obj.lost_reason, actor_kind='owner', actor_id=str(request.user.id),
    )
    return response_base.success(data=data)


# ---------------- 退订登记 ----------------


@router.post('/optout', summary='[Owner] 登记客户退订', dependencies=[DependsJwtAuth])
async def register_optout(request: Request, db: CurrentSessionTransaction, obj: OptoutParam) -> ResponseModel:
    data = await growth_outreach_service.register_optout(
        db, user_id=request.user.id, channel=obj.channel, address=obj.address, reason=obj.reason, source='owner'
    )
    return response_base.success(data=data)


# ---------------- 报表 ----------------


@router.get('/report/funnel', summary='[Owner] 漏斗总览', dependencies=[DependsJwtAuth])
async def report_funnel(request: Request, db: CurrentSession) -> ResponseModel:
    data = await growth_report_service.funnel_overview(db, user_id=request.user.id)
    return response_base.success(data=data)


@router.get('/report/stages', summary='[Owner] 商机阶段分布', dependencies=[DependsJwtAuth])
async def report_stages(request: Request, db: CurrentSession) -> ResponseModel:
    data = await growth_report_service.stage_distribution(db, user_id=request.user.id)
    return response_base.success(data=data)


@router.get('/report/lifecycle', summary='[Owner] 客户生命周期分布', dependencies=[DependsJwtAuth])
async def report_lifecycle(request: Request, db: CurrentSession) -> ResponseModel:
    data = await growth_report_service.lifecycle_distribution(db, user_id=request.user.id)
    return response_base.success(data=data)
