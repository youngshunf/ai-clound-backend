"""获客通知卡片（设计 07 §6.4 / 实施 91 M4 + M6）。

分身（agent）经获客工具触发的关键业务事件 → 给主人发统一通知卡片（`notification_service.emit`），
deep_link 进获客页（`/growth/*`，与 daemon owner 面路由对齐）。

事件分两类 source：
- **agent 调用同步可知**（M4 三事件：触达待审批 / 商机阶段变更 / 成交）→ source.kind='agent'（on_behalf_of=主人）。
- **系统/渠道事件驱动**（M6 两事件：客户回复 / 新线索批次）→ source.kind='system'（采集 worker 落库 /
  渠道 inbound 回流时触发，无 agent 同步上下文）。

通知为业务旁路：与 hasn_task 一致**内联 emit**（同事务），通知基础设施异常即如实失败（零 fake）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.notification.service.notification_service import notification_service
from backend.common.dataclasses import AgentTokenPayload


def _source(agent: AgentTokenPayload) -> dict[str, Any]:
    """统一 source：agent 代主人行动。"""
    return {
        'kind': 'agent',
        'id': agent.agent_hasn_id,
        'display_name': agent.agent_name,
        'on_behalf_of': agent.owner_hasn_id,
    }


def _system_source(owner_hasn_id: str) -> dict[str, Any]:
    """系统/渠道事件 source：无 agent 同步上下文（采集 worker / 渠道 inbound 回流）。"""
    return {'kind': 'system', 'id': 'growth', 'display_name': '获客', 'on_behalf_of': owner_hasn_id}


class GrowthNotificationService:
    """获客业务事件 → 主人通知卡片（M4 三同步事件）。"""

    @staticmethod
    async def outreach_pending_approval(
        db: AsyncSession, *, agent: AgentTokenPayload, message_id: int, customer_id: int, channel: str
    ) -> None:
        """触达待审批：分身请求发送触达，落 pending_approval → 提醒主人去审批队列处理。"""
        await notification_service.emit(
            db,
            recipient_id=agent.owner_hasn_id,
            source=_source(agent),
            category='reminder',
            type='growth.outreach.pending',  # type 列 varchar(30)，勿超长
            title=f'分身 {agent.agent_name} 想给客户发一条{channel}触达，待你审批',
            payload={
                'target': {'kind': 'outreach_message', 'id': str(message_id)},
                'customer_id': customer_id,
                'deep_link': '/growth/outreach/pending',
            },
            dedupe_key=f'growth.outreach.pending:{message_id}',
        )

    @staticmethod
    async def opportunity_stage_changed(
        db: AsyncSession, *, agent: AgentTokenPayload, opportunity_id: int, stage: str, name: str | None
    ) -> None:
        """商机阶段变更：分身推进/回退商机阶段 → 通知主人。"""
        label = f'「{name}」' if name else ''
        await notification_service.emit(
            db,
            recipient_id=agent.owner_hasn_id,
            source=_source(agent),
            category='commerce',
            type='growth.opp.stage_changed',  # type 列 varchar(30)，勿超长
            title=f'分身 {agent.agent_name} 把商机{label}推进到「{stage}」',
            payload={
                'target': {'kind': 'opportunity', 'id': str(opportunity_id)},
                'stage': stage,
                'deep_link': f'/growth/opportunities/{opportunity_id}',
            },
            dedupe_key=f'growth.opportunity.stage:{opportunity_id}:{stage}',
        )

    @staticmethod
    async def deal_closed(
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        opportunity_id: int,
        result: str,
        amount: Any | None,
        name: str | None,
    ) -> None:
        """成交/流失登记：分身登记成交结果 → 通知主人（仅登记，不自动收款）。"""
        label = f'「{name}」' if name else ''
        if result == 'won':
            amount_text = f'（金额 {amount}）' if amount is not None else ''
            title = f'🎉 分身 {agent.agent_name} 登记商机{label}成交{amount_text}'
        else:
            title = f'分身 {agent.agent_name} 登记商机{label}流失'
        await notification_service.emit(
            db,
            recipient_id=agent.owner_hasn_id,
            source=_source(agent),
            category='commerce',
            type='growth.deal.closed',
            title=title,
            payload={
                'target': {'kind': 'opportunity', 'id': str(opportunity_id)},
                'result': result,
                'deep_link': f'/growth/opportunities/{opportunity_id}',
            },
            dedupe_key=f'growth.deal.closed:{opportunity_id}',
        )


    # ---------- M6：系统/渠道事件驱动（无 agent 同步上下文） ----------

    @staticmethod
    async def inbound_reply_received(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        customer_id: int,
        customer_name: str | None,
        channel: str,
        excerpt: str | None = None,
    ) -> None:
        """客户回复：渠道 inbound 回流落库时通知主人去看 / 跟进（J3 即时跟进的人侧提醒）。"""
        who = f'客户「{customer_name}」' if customer_name else '一位客户'
        await notification_service.emit(
            db,
            recipient_id=owner_hasn_id,
            source=_system_source(owner_hasn_id),
            category='reminder',
            type='growth.reply.received',  # type 列 varchar(30)，勿超长
            title=f'{who}回复了你的{channel}触达',
            body=(excerpt or None),
            payload={
                'target': {'kind': 'customer', 'id': str(customer_id)},
                'channel': channel,
                'deep_link': f'/growth/customers/{customer_id}',
            },
            dedupe_key=f'growth.reply.received:{customer_id}',
        )

    @staticmethod
    async def leads_collected_batch(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        job_id: int,
        new_count: int,
        keyword: str | None = None,
    ) -> None:
        """新线索批次：采集 worker 完成一批采集落库时通知主人去筛（new_count<=0 不发，零噪声）。"""
        if new_count <= 0:
            return
        kw = f'「{keyword}」' if keyword else ''
        await notification_service.emit(
            db,
            recipient_id=owner_hasn_id,
            source=_system_source(owner_hasn_id),
            category='reminder',
            type='growth.leads.collected',  # type 列 varchar(30)，勿超长
            title=f'采集{kw}完成，新增 {new_count} 条线索待筛选',
            payload={
                'target': {'kind': 'lead_collection_job', 'id': str(job_id)},
                'new_count': new_count,
                'deep_link': '/growth/leads',
            },
            dedupe_key=f'growth.leads.collected:{job_id}',
        )


growth_notification_service = GrowthNotificationService()
