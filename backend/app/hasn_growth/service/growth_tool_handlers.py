"""获客 AI-Native 工具 handler（云端 gateway_internal）。

设计原则（与 community/knowledge 一致）：获客是**纯云端业务应用**（CRM/获客/触达，无任何
本地文件/电脑操作），其 `hasn.growth.*` 工具一律走**云端 MCP**——由 `ai_native_runtime_gateway`
在 `transport=gateway_internal` 时进程内直调本文件 handler，handler 再直调 hasn_growth service。
**不经 hasn-node 本地 hasn-mcp 注册、不经 daemon Agent 工具代理**（那是 task/deck 等有本地理由的应用的模式）。

每个 handler 签名 `(db, agent: AgentTokenPayload, input_payload: dict) -> dict`：
- 身份恒取自 Agent JWT claims（`owner_user_id`/`agent_hasn_id`/`owner_hasn_id`），绝不从入参读身份；
- 读类恒脱敏 PII，历史 `growth:pii` scope 不再授权 Agent 明文；
- 企业化裁剪经 `GrowthScope`（经理见全部 / 销售见 assignee=自己），写类授权由 service 强制；
- 返回**裸 data**（gateway 负责信封/审计），与 community/knowledge handler 一致。

蓝本：`app/hasn_growth/api/v1/agent/growth.py`（同一套 service 调用，去 HTTP 壳）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.growth_profile_service import (
    growth_profile_service,
)
from backend.app.hasn_growth.service.growth_project_app_service import (
    growth_project_app_service,
)
from backend.app.hasn_growth.service.growth_project_provision_service import (
    enqueue_growth_provision_after_commit,
)
from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_boundary import assert_growth_pii_payload_safe
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.app.hasn_growth.service.scope_context import GrowthScope, resolve_growth_scope
from backend.app.mcp.artifact_registration import merge_resource_uri, register_app_resource_artifact
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload


def _reveal(agent: AgentTokenPayload) -> bool:
    """读类是否回明文 PII（默认脱敏）。

    历史上凭 JWT ``growth:pii`` claim 判定，但该 claim 对所有分身恒等（从不携带 pii），
    实际恒为脱敏；scopes claim 已随实施102 S0 退役 → 恒返回 False（脱敏），语义不变。
    如需按分身放明文，改走三态 capability_modes 授权（doc17），而非凭证 claim。
    """
    return False


async def _scope(db: AsyncSession, agent: AgentTokenPayload, view: str = 'team') -> GrowthScope:
    """分身代主人解析获客上下文：身份恒取自 JWT，assignee 键为主人 hasn_id。"""
    return await resolve_growth_scope(db, user_id=agent.owner_user_id, owner_hasn_id=agent.owner_hasn_id, view=view)


def _int(payload: dict[str, Any], key: str) -> int:
    return int(payload[key])


def _required_str(payload: dict[str, Any], key: str) -> str:
    """读取必填字符串入参；缺失或空白时显式拒绝。"""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise errors.RequestError(msg=f'{key} 不能为空')
    return value.strip()


async def _register_growth_project(
    db: AsyncSession,
    agent: AgentTokenPayload,
    project: dict[str, Any],
    *,
    source_tool: str,
) -> ArtifactRegistration | None:
    project_id = project.get('id')
    if not isinstance(project_id, str):
        return None
    return await register_app_resource_artifact(
        db,
        app_id='growth',
        resource_kind='growth.project',
        server_id=project_id,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        title=str(project.get('name') or '获客漏斗'),
        source_tool=source_tool,
    )


# ---------------- 项目（hasn.growth.project.*） ----------------


async def handle_growth_project_get(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    growth_project_id = input_payload.get('growth_project_id')
    if isinstance(growth_project_id, str) and growth_project_id.strip():
        return await growth_project_app_service.get_by_id(
            db,
            owner_hasn_id=agent.owner_hasn_id,
            growth_project_id=growth_project_id,
        )
    return await growth_project_app_service.get_for_platform(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        platform_project_id=_required_str(
            input_payload,
            'platform_project_id',
        ),
    )


async def handle_growth_project_create(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    result = await growth_project_app_service.enable(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        owner_user_id=agent.owner_user_id,
        platform_project_id=_required_str(
            input_payload,
            'platform_project_id',
        ),
        name=input_payload.get('name'),
        tagline=input_payload.get('tagline'),
        command_id=_required_str(input_payload, 'trace_id'),
        idempotency_key=_required_str(
            input_payload,
            'idempotency_key',
        ),
    )
    project = result['growth_project']
    enqueue_growth_provision_after_commit(db, project['id'])
    registration = await _register_growth_project(
        db,
        agent,
        project,
        source_tool='hasn.growth.project.create',
    )
    if registration is not None:
        project['uri'] = registration.resource_uri
        result['uri'] = registration.resource_uri
    return result


async def handle_growth_project_update(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    project = await growth_project_app_service.update(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        growth_project_id=_required_str(
            input_payload,
            'growth_project_id',
        ),
        name=input_payload.get('name'),
        tagline=input_payload.get('tagline'),
    )
    registration = await _register_growth_project(
        db,
        agent,
        project,
        source_tool='hasn.growth.project.update',
    )
    return merge_resource_uri(project, registration)


async def handle_growth_project_update_profile(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """分身只提交待确认画像建议；Owner 接受前不改写当前画像。"""
    document_ids = input_payload.get('knowledge_document_ids')
    if not isinstance(document_ids, list) or not all(
        isinstance(document_id, int) and document_id > 0
        for document_id in document_ids
    ):
        raise errors.RequestError(msg='knowledge_document_ids 必须是非空正整数数组')
    suggestion = await growth_profile_service.submit_suggestion(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        growth_project_id=_required_str(input_payload, 'growth_project_id'),
        expected_version=_int(input_payload, 'expected_version'),
        product_profile=dict(input_payload.get('product_profile') or {}),
        icp_profile=dict(input_payload.get('icp_profile') or {}),
        knowledge_document_ids=document_ids,
        trace_id=_required_str(input_payload, 'trace_id'),
        idempotency_key=_required_str(input_payload, 'idempotency_key'),
    )
    project = await growth_project_app_service.get_by_id(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        growth_project_id=_required_str(input_payload, 'growth_project_id'),
    )
    registration = await _register_growth_project(
        db,
        agent,
        project,
        source_tool='hasn.growth.project.update_profile',
    )
    return merge_resource_uri(
        {
            'suggestion': suggestion,
            'profile_version': project['profile_version'],
            'current_profile_unchanged': True,
        },
        registration,
    )


async def handle_growth_project_pause(
    db: AsyncSession,
    agent: AgentTokenPayload,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    project = await growth_project_app_service.pause(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        growth_project_id=_required_str(
            input_payload,
            'growth_project_id',
        ),
    )
    registration = await _register_growth_project(
        db,
        agent,
        project,
        source_tool='hasn.growth.project.pause',
    )
    return merge_resource_uri(project, registration)


# ---------------- 采集（hasn.growth.collect.*） ----------------


def _enqueue_collection_job_after_commit(db: AsyncSession, job_id: int) -> None:
    """注册 after_commit 钩子：本事务真正提交后才把采集 job 入 Celery 队列异步执行（方案A）。

    避免 enqueue-in-transaction race——采集 worker 是独立进程、用新 DB session，若在事务提交
    前 ``.delay()``，worker 可能读不到尚未提交的 job（报"任务不存在"）。after_commit 保证 job
    已落库可见才入队；事务回滚则钩子不触发（不会出现"入了队却无 job"的孤儿任务）。broker 不
    可达时 best-effort 记日志、不让已落库的工具调用失败（job 留 pending，owner 可在 UI 手动运行兜底）。
    """
    from sqlalchemy import event

    def _enqueue(_sync_session: Any) -> None:
        try:
            from backend.app.hasn_growth.tasks import lead_automation_run_job

            lead_automation_run_job.delay(job_id)
            log.info(f'[GrowthCollect] 采集 job 已入队异步执行: job_id={job_id}')
        except Exception as exc:
            log.warning(
                '[GrowthCollect] 采集 job 入队失败，已落库可手动运行：job_id=%s error_type=%s',
                job_id,
                exc.__class__.__name__,
            )

    event.listen(db.sync_session, 'after_commit', _enqueue, once=True)


async def handle_growth_collect_start(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    # 身份取自 JWT：记 owner 为采集发起者（统一池——采集入公共池，run_job 跑完为发起者建 lead_ref）。
    obj = CreateLeadJobParam.model_validate(input_payload)
    payload = obj.model_copy(update={'user_id': agent.owner_user_id})
    job = await lead_automation_business_service.create_job(db=db, obj=payload)
    # 方案A：建 pending job 后，事务提交时入 Celery 队列异步执行采集（firecrawl→清洗→去重→入库），
    # 不阻塞本 MCP 工具调用；分身随后用 collect.status 轮询真实进度。
    _enqueue_collection_job_after_commit(db, int(job['id']))
    return job


async def handle_growth_collect_status(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await lead_automation_business_service.get_job(
        db, job_id=_int(input_payload, 'job_id'), user_id=agent.owner_user_id
    )


async def handle_growth_lead_request(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """请求线索（用户端默认入口·2.1）：先查公共池命中即交付（零采集成本），缺口才后台补爬回流。

    身份取自 JWT。返回 {delivered, from_pool, backfill_job_id, requested, leads}；有补爬 job 时
    挂 after_commit 钩子入队（与 collect.start 同时序保护：提交前 worker 读不到未提交 job）。
    """
    result = await lead_pool_query_service.request_leads(
        db,
        user_id=agent.owner_user_id,
        limit=int(input_payload.get('limit', 20)),
        industry=input_payload.get('industry'),
        region=input_payload.get('region'),
        keyword=input_payload.get('keyword') or input_payload.get('q'),
        city=input_payload.get('city'),
        reveal_pii=_reveal(agent),
    )
    backfill_job_id = result.get('backfill_job_id')
    if backfill_job_id:
        _enqueue_collection_job_after_commit(db, int(backfill_job_id))
    return result


# ---------------- 企业数据读穿中台（hasn.growth.lookup/search/enrich_company） ----------------


async def handle_growth_lookup_company(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """按企业名/信用代码取全画像：查公共池命中即返回（省 qcc 费），未命中调网关 qcc → 入池 → 返回带 lead_id。

    身份取自 JWT；qcc 平台 key 由通用网关持有（绝不下发分身），配额按本 owner 归因（doc10 §7.2）。
    """
    from backend.app.hasn_growth.service.enterprise_lookup_service import enterprise_lookup_service

    return await enterprise_lookup_service.lookup_company(
        db,
        user_id=agent.owner_user_id,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        query=str(input_payload.get('query') or input_payload.get('q') or '').strip(),
        reveal_pii=_reveal(agent),
        force_refresh=bool(input_payload.get('force_refresh')),
        trace_id=input_payload.get('trace_id'),
    )


async def handle_growth_search_companies(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """按关键词/行业/地域找企业：先查池命中复用，不足时调网关 qcc 补 → 入池 → 返回带 lead_id 列表。"""
    from backend.app.hasn_growth.service.enterprise_lookup_service import enterprise_lookup_service

    return await enterprise_lookup_service.search_companies(
        db,
        user_id=agent.owner_user_id,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        query=input_payload.get('query') or input_payload.get('q'),
        industry=input_payload.get('industry'),
        region=input_payload.get('region'),
        city=input_payload.get('city'),
        limit=int(input_payload.get('limit', 5)),
        reveal_pii=_reveal(agent),
        trace_id=input_payload.get('trace_id'),
    )


async def handle_growth_enrich_company(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """按维度深度富化（风险/知识产权/经营/高管/变更历史）：查 meta 维度缓存 TTL 内命中即返回，
    未命中/过期调对应 qcc namespace → 入 contact.meta_data['enrichment'] 保真 → 返回。

    须 owner 已拥有该线索（先 lookup/search 获取）；维度全量入池对齐 doc09 §4.3。
    """
    from backend.app.hasn_growth.service.enterprise_lookup_service import enterprise_lookup_service

    dims = input_payload.get('dimensions')
    if isinstance(dims, str):
        dims = [d.strip() for d in dims.split(',') if d.strip()]
    return await enterprise_lookup_service.enrich_company(
        db,
        lead_contact_id=_int(input_payload, 'lead_contact_id'),
        user_id=agent.owner_user_id,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        dimensions=list(dims or []),
        tool=input_payload.get('tool'),
        force_refresh=bool(input_payload.get('force_refresh')),
        trace_id=input_payload.get('trace_id'),
    )


# ---------------- 线索 ----------------


async def handle_growth_lead_search(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    return await growth_funnel_service.search_leads(
        db,
        user_id=agent.owner_user_id,
        query=input_payload.get('q') or input_payload.get('query'),
        limit=int(input_payload.get('limit', 20)),
        reveal_pii=_reveal(agent),
        growth_project_id=input_payload.get('growth_project_id'),
    )


async def handle_growth_lead_get(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await growth_funnel_service.get_lead(
        db,
        user_id=agent.owner_user_id,
        lead_contact_id=_int(input_payload, 'lead_contact_id'),
        reveal_pii=_reveal(agent),
        growth_project_id=input_payload.get('growth_project_id'),
    )


async def handle_growth_lead_qualify(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    # 企业模式下晋级的客户落企业池、assignee=主人 hasn_id（个人模式落个人池）。
    scope = await _scope(db, agent)
    result = await growth_funnel_service.qualify_lead(
        db,
        user_id=agent.owner_user_id,
        lead_contact_id=_int(input_payload, 'lead_contact_id'),
        profile=input_payload.get('profile'),
        intent_score=input_payload.get('intent_score'),
        owner_agent_id=agent.agent_hasn_id,
        scope=scope,
    )
    registration = await _register_growth_customer(db, agent, result, source_tool='hasn.growth.lead.qualify')
    return merge_resource_uri(result, registration)


async def handle_growth_lead_dismiss(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await growth_funnel_service.dismiss_lead(
        db,
        user_id=agent.owner_user_id,
        lead_contact_id=_int(input_payload, 'lead_contact_id'),
        reason=_required_str(input_payload, 'reason'),
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
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        reveal_pii=_reveal(agent),
        scope=scope,
    )


async def handle_growth_customer_timeline(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    scope = await _scope(db, agent)
    return await growth_funnel_service.customer_timeline(
        db, user_id=agent.owner_user_id, customer_id=_int(input_payload, 'customer_id'), scope=scope
    )


async def handle_growth_customer_update_profile(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    result = await growth_funnel_service.update_customer_profile(
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
    registration = await _register_growth_customer(db, agent, result, source_tool='hasn.growth.customer.update_profile')
    return merge_resource_uri(result, registration)


async def handle_growth_activity_log(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    customer_id = _int(input_payload, 'customer_id')
    result = await growth_funnel_service.log_activity(
        db,
        user_id=agent.owner_user_id,
        customer_id=customer_id,
        kind=_required_str(input_payload, 'kind'),
        content=_required_str(input_payload, 'content'),
        opportunity_id=input_payload.get('opportunity_id'),
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
        scope=scope,
    )
    # 活动记在客户名下——产物仍是那位客户（活动本身没有独立可打开的资源域）。
    # 返回体是 activity，故 id 取自入参 customer_id；标题留空由登记侧沿用既有值。
    registration = await register_app_resource_artifact(
        db,
        app_id='growth',
        resource_kind='growth.customer',
        server_id=customer_id,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        title='客户资料',
        source_tool='hasn.growth.activity.log',
    )
    # doc36 §3.2：`uri` 指向那位**客户**（活动本身无独立资源域）——与登记的产物同一个东西，
    # 分身记完活动即知去哪儿看这位客户。
    return merge_resource_uri(result, registration)


async def _register_growth_customer(
    db: AsyncSession, agent: AgentTokenPayload, result: Any, *, source_tool: str
) -> ArtifactRegistration | None:
    """register-on-write：分身晋级/维护的客户登记为产物（doc31 铁律）。

    doc36 U2：返回 `ArtifactRegistration` 供写工具把 `uri` 放进返回体（§3.2 契约）。
    """
    if not isinstance(result, dict):
        return None
    customer_id = result.get('id')
    if not isinstance(customer_id, int):
        return None
    raw_title = str(result.get('company_name') or result.get('contact_name') or '').strip() or '客户资料'
    title = str(redact_pii_value(raw_title))
    assert_growth_pii_payload_safe({'title': title, 'customer_id': customer_id})
    return await register_app_resource_artifact(
        db,
        app_id='growth',
        resource_kind='growth.customer',
        server_id=customer_id,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        title=title,
        source_tool=source_tool,
    )


async def handle_growth_customer_reassign(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    # 分身代经理主人分配负责人；非经理由 service can_manage_assignment 拒。
    scope = await _scope(db, agent, view='team')
    return await growth_funnel_service.reassign_customer(
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        new_assignee=str(input_payload['assignee']),
        scope=scope,
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
        channel=_required_str(input_payload, 'channel'),
        content=_required_str(input_payload, 'content'),
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
            channel=str(data['channel']),
        )
    return data


async def handle_growth_outreach_status(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    scope = await _scope(db, agent)
    return await growth_outreach_service.list_customer_outreach(
        db,
        user_id=agent.owner_user_id,
        customer_id=_int(input_payload, 'customer_id'),
        limit=int(input_payload.get('limit', 50)),
        scope=scope,
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
        name=_required_str(input_payload, 'name'),
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
        stage=_required_str(input_payload, 'stage'),
        note=input_payload.get('note'),
        actor_kind='agent',
        actor_id=agent.agent_hasn_id,
        scope=scope,
    )
    await growth_notification_service.opportunity_stage_changed(
        db,
        agent=agent,
        opportunity_id=_int(input_payload, 'opportunity_id'),
        stage=_required_str(input_payload, 'stage'),
    )
    return data


async def handle_growth_deal_close(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    data = await growth_opportunity_service.close_deal(
        db,
        user_id=agent.owner_user_id,
        opportunity_id=_int(input_payload, 'opportunity_id'),
        result=_required_str(input_payload, 'result'),
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
        result=_required_str(input_payload, 'result'),
        amount=data.get('amount'),
    )
    return data


# ---------------- 报表 ----------------


async def handle_growth_report_funnel(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent, view=str(input_payload.get('view', 'team')))
    return await growth_report_service.funnel_overview(db, user_id=agent.owner_user_id, scope=scope)
