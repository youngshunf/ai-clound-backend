"""获客 AI-Native 工具 handler（云端 gateway_internal）。

设计原则（与 community/knowledge 一致）：获客是**纯云端业务应用**（CRM/获客/触达，无任何
本地文件/电脑操作），其 `hasn.growth.*` 工具一律走**云端 MCP**——由 `ai_native_runtime_gateway`
在 `transport=gateway_internal` 时进程内直调本文件 handler，handler 再直调 hasn_growth service。
**不经 hasn-node 本地 hasn-mcp 注册、不经 daemon Agent 工具代理**（那是 task/deck 等有本地理由的应用的模式）。

每个 handler 签名 `(db, agent: AgentTokenPayload, input_payload: dict) -> dict`：
- 身份恒取自 Agent JWT claims（`owner_user_id`/`agent_hasn_id`/`owner_hasn_id`），绝不从入参读身份；
- 读类默认脱敏 PII，仅持 `growth:pii` 增强 scope 才回明文（`_reveal`）；
- 企业化裁剪经 `GrowthScope`（经理见全部 / 销售见 assignee=自己），写类授权由 service 强制；
- 返回**裸 data**（gateway 负责信封/审计），与 community/knowledge handler 一致。

蓝本：`app/hasn_growth/api/v1/agent/growth.py`（同一套 service 调用，去 HTTP 壳）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.app.hasn_growth.service.scope_context import GrowthScope, resolve_growth_scope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

_PII_SCOPE = 'growth:pii'


def _reveal(agent: AgentTokenPayload) -> bool:
    """持 growth:pii 增强 scope → 读类回明文（默认脱敏）。"""
    return _PII_SCOPE in (agent.scopes or [])


async def _scope(db: AsyncSession, agent: AgentTokenPayload, view: str = 'team') -> GrowthScope:
    """分身代主人解析获客上下文：身份恒取自 JWT，assignee 键为主人 hasn_id。"""
    return await resolve_growth_scope(
        db, user_id=agent.owner_user_id, owner_hasn_id=agent.owner_hasn_id, view=view
    )


def _int(payload: dict[str, Any], key: str) -> int:
    return int(payload[key])


# ---------------- 采集（hasn.growth.collect.*） ----------------


async def handle_growth_collect_start(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    # 身份取自 JWT：恒落主人私有池（user_scope），分身无权操作公共池采集。
    obj = CreateLeadJobParam.model_validate(input_payload)
    payload = obj.model_copy(update={'user_id': agent.owner_user_id, 'lead_scope': 'user'})
    return await lead_automation_business_service.create_job(db=db, obj=payload)


async def handle_growth_collect_status(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await lead_automation_business_service.get_job(
        db, job_id=_int(input_payload, 'job_id'), user_id=agent.owner_user_id
    )


# ---------------- 线索 ----------------


async def handle_growth_lead_search(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await growth_funnel_service.search_leads(
        db,
        user_id=agent.owner_user_id,
        query=input_payload.get('q') or input_payload.get('query'),
        limit=int(input_payload.get('limit', 20)),
        reveal_pii=_reveal(agent),
    )


async def handle_growth_lead_get(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await growth_funnel_service.get_lead(
        db, user_id=agent.owner_user_id, lead_contact_id=_int(input_payload, 'lead_contact_id'),
        reveal_pii=_reveal(agent),
    )


async def handle_growth_lead_qualify(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    # 企业模式下晋级的客户落企业池、assignee=主人 hasn_id（个人模式落个人池）。
    scope = await _scope(db, agent)
    return await growth_funnel_service.qualify_lead(
        db,
        user_id=agent.owner_user_id,
        lead_contact_id=_int(input_payload, 'lead_contact_id'),
        profile=input_payload.get('profile'),
        intent_score=input_payload.get('intent_score'),
        owner_agent_id=agent.agent_hasn_id,
        scope=scope,
    )


async def handle_growth_lead_dismiss(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await growth_funnel_service.dismiss_lead(
        db, user_id=agent.owner_user_id, lead_contact_id=_int(input_payload, 'lead_contact_id'),
        reason=input_payload.get('reason'),
    )


# ---------------- 客户 ----------------


async def handle_growth_customer_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent, view=str(input_payload.get('view', 'team')))
    items = await growth_funnel_service.list_customers(
        db,
        user_id=agent.owner_user_id,
        lifecycle_status=input_payload.get('lifecycle_status'),
        assignee=input_payload.get('assignee'),
        limit=int(input_payload.get('limit', 20)),
        reveal_pii=_reveal(agent),
        scope=scope,
    )
    return {'items': items, 'scope': scope.to_meta()}


async def handle_growth_customer_get(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await growth_funnel_service.get_customer(
        db, user_id=agent.owner_user_id, customer_id=_int(input_payload, 'customer_id'),
        reveal_pii=_reveal(agent), scope=scope,
    )


async def handle_growth_customer_timeline(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await growth_funnel_service.customer_timeline(
        db, user_id=agent.owner_user_id, customer_id=_int(input_payload, 'customer_id'), scope=scope
    )


async def handle_growth_customer_update_profile(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await growth_funnel_service.update_customer_profile(
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        profile=input_payload.get('profile'),
        intent_score=input_payload.get('intent_score'),
        tags=input_payload.get('tags'),
        lifecycle_status=input_payload.get('lifecycle_status'),
        followup_task_id=input_payload.get('followup_task_id'),
        scope=scope,
    )


async def handle_growth_activity_log(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await growth_funnel_service.log_activity(
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        kind=input_payload.get('kind'),
        content=input_payload.get('content'),
        opportunity_id=input_payload.get('opportunity_id'),
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
        scope=scope,
    )


async def handle_growth_customer_reassign(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    # 分身代经理主人分配负责人；非经理由 service can_manage_assignment 拒。
    scope = await _scope(db, agent, view='team')
    return await growth_funnel_service.reassign_customer(
        db, user_id=agent.owner_user_id, customer_id=_int(input_payload, 'customer_id'),
        new_assignee=str(input_payload['assignee']), scope=scope,
    )


# ---------------- 触达 ----------------


async def handle_growth_outreach_send(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    data = await growth_outreach_service.send_outreach(
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        channel=input_payload.get('channel'),
        content=input_payload.get('content'),
        agent_id=agent.agent_hasn_id,
        subject=input_payload.get('subject'),
        intent_note=input_payload.get('intent_note'),
        content_assets=input_payload.get('content_assets'),
        opportunity_id=input_payload.get('opportunity_id'),
        scope=scope,
    )
    # 触达待审批 → 通知主人去审批队列（仅 pending_approval；放行/拦截不打扰）。
    if data.get('status') == 'pending_approval':
        await growth_notification_service.outreach_pending_approval(
            db,
            agent=agent,
            message_id=int(data['id']),
            customer_id=_int(input_payload, 'customer_id'),
            channel=input_payload.get('channel'),
        )
    return data


async def handle_growth_outreach_status(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await growth_outreach_service.list_customer_outreach(
        db, user_id=agent.owner_user_id, customer_id=_int(input_payload, 'customer_id'),
        limit=int(input_payload.get('limit', 50)), scope=scope,
    )


# ---------------- 商机 / 成交 ----------------


async def handle_growth_opportunity_create(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await growth_opportunity_service.create_opportunity(
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        name=input_payload.get('name'),
        amount=input_payload.get('amount'),
        # 镜像 CreateOpportunityParam 默认值（currency='CNY'/stage='contacted'）——service 不自带默认，
        # 工具入参省略时须补齐，否则 None 触发「非法商机阶段」。
        currency=input_payload.get('currency') or 'CNY',
        stage=input_payload.get('stage') or 'contacted',
        probability=input_payload.get('probability'),
        created_by_kind='agent',
        actor_id=agent.agent_hasn_id,
        scope=scope,
    )


async def handle_growth_opportunity_update_stage(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    data = await growth_opportunity_service.update_stage(
        db,
        user_id=agent.owner_user_id,
        opportunity_id=_int(input_payload, 'opportunity_id'),
        stage=input_payload.get('stage'),
        note=input_payload.get('note'),
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
        scope=scope,
    )
    await growth_notification_service.opportunity_stage_changed(
        db, agent=agent, opportunity_id=_int(input_payload, 'opportunity_id'),
        stage=input_payload.get('stage'), name=data.get('name'),
    )
    return data


async def handle_growth_deal_close(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    data = await growth_opportunity_service.close_deal(
        db,
        user_id=agent.owner_user_id,
        opportunity_id=_int(input_payload, 'opportunity_id'),
        result=input_payload.get('result'),
        amount=input_payload.get('amount'),
        close_note=input_payload.get('close_note'),
        lost_reason=input_payload.get('lost_reason'),
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
    )
    await growth_notification_service.deal_closed(
        db,
        agent=agent,
        opportunity_id=_int(input_payload, 'opportunity_id'),
        result=input_payload.get('result'),
        amount=data.get('amount'),
        name=data.get('name'),
    )
    return data


# ---------------- 报表 ----------------


async def handle_growth_report_funnel(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent, view=str(input_payload.get('view', 'team')))
    return await growth_report_service.funnel_overview(db, user_id=agent.owner_user_id, scope=scope)
