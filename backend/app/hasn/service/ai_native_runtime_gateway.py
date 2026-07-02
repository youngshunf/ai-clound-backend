from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from fastapi.security.utils import get_authorization_scheme_param

from backend.app.hasn.model import HasnAiNativeAppAudit
from backend.app.hasn.service import app_catalog_service
from backend.app.hasn.service.agent_capability_guard import capability_guard
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.instance_resolver import (
    FACE_TOOL,
    TRANSPORT_CLOUD_RELAY,
    TRANSPORT_GATEWAY_INTERNAL,
    InstanceResolutionError,
    instance_resolver,
)
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.app.hasn_community.service.community_service import community_service
from backend.app.mcp.tools.input_binding import ToolInputError, bind_tool_input
from backend.common.exception import errors
from backend.common.security.agent_jwt import jwt_decode_agent
from backend.common.security.scope_policy import MODE_ASK, MODE_DENY
from backend.database.redis import redis_client

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.ai_native_runtime import (
        AiNativeAuditQuery,
        AiNativeRuntimeCapabilitiesRequest,
        AiNativeToolCallRequest,
    )
    from backend.common.dataclasses import AgentTokenPayload


class _RuntimeDenialError(Exception):
    def __init__(self, *, code: str, message: str, workspace: dict[str, Any]) -> None:
        self.code = code
        self.message = message
        self.workspace = workspace


# 社区工具入参声明式校验规则（gateway 二道防线；MCP tool.call 层另有 schema-on-error 主校验）。
# required_str=必填非空字符串字段；enums=字段→允许值集合（必填且须命中）；maxlen=字段长度上界。
# 未列工具默认通过。
_COMMUNITY_INPUT_RULES: dict[str, dict[str, Any]] = {
    'community.get_feed': {'enums': {'type': {'following', 'recommend', 'hot', 'articles'}}},
    'community.get_post': {'required_str': ['post_id']},
    'community.get_article': {'required_str': ['article_id']},
    'community.get_comments': {'required_str': ['target_id'], 'enums': {'target_type': {'post', 'article'}}},
    'community.search': {'required_str': ['query']},
    'community.get_profile': {'required_str': ['hasn_id']},
    'community.get_profile_content': {
        'required_str': ['hasn_id'],
        'enums': {'kind': {'posts', 'articles', 'collections', 'agents'}},
    },
    'community.get_trending_topics': {},
    'community.get_recommended_agents': {},
    'community.get_notifications': {},
    'community.mark_notifications_read': {},
    'community.create_post': {'required_str': ['content'], 'maxlen': {'content': 10000}},
    'community.create_article': {
        'required_str': ['title', 'content'],
        'maxlen': {'title': 200, 'content': 100000},
    },
    'community.create_comment': {
        'required_str': ['target_id', 'content'],
        'enums': {'target_type': {'post', 'article'}},
        'maxlen': {'content': 5000},
    },
    'community.like': {'required_str': ['target_id'], 'enums': {'target_type': {'post', 'article', 'comment'}}},
    'community.unlike': {'required_str': ['target_id'], 'enums': {'target_type': {'post', 'article', 'comment'}}},
    'community.follow': {'required_str': ['target_id'], 'enums': {'target_type': {'human', 'agent', 'topic'}}},
    'community.unfollow': {'required_str': ['target_id'], 'enums': {'target_type': {'human', 'agent', 'topic'}}},
    'community.collect': {'required_str': ['target_id'], 'enums': {'target_type': {'post', 'article'}}},
    'community.uncollect': {'required_str': ['target_id'], 'enums': {'target_type': {'post', 'article'}}},
    # 话题 / 圈子 / 文档系统（实施/95 §2.5）
    'community.get_topic': {'required_str': ['topic']},
    'community.get_topic_feed': {'required_str': ['topic'], 'enums': {'sort': {'latest', 'hot'}}},
    'community.get_circle': {'required_str': ['circle']},
    'community.get_circle_feed': {'required_str': ['circle']},
    'community.discover_circles': {},
    'community.list_my_circles': {},
    'community.join_circle': {'required_str': ['circle']},
    'community.leave_circle': {'required_str': ['circle']},
    'community.get_doc_space': {'required_str': ['space']},
    'community.get_doc_tree': {'required_str': ['space_id']},
    'community.create_doc_space': {'required_str': ['title'], 'maxlen': {'title': 200}},
    'community.create_doc_node': {'required_str': ['space_id', 'title'], 'maxlen': {'title': 200}},
}


class AiNativeRuntimeGateway:
    def __init__(self) -> None:
        # gateway_internal 工具的 handler 注册表（懒构建并缓存）。
        # 键 = manifest tool.handler 字段；值 = async (db, agent, input_payload) -> dict。
        # 取代旧的 `if app_id == ...` 硬编码分发（设计 11 §6.2）。
        self._internal_handlers_cache: dict[str, Any] | None = None

    async def get_capabilities(
        self, db: AsyncSession, *, request: Request, body: AiNativeRuntimeCapabilitiesRequest
    ) -> dict[str, Any]:
        agent = self._require_agent(request)
        workspace = await self._resolve_workspace(db, agent=agent, requested_workspace=body.workspace)
        manifest = await ai_native_app_registry.ensure_builtin_published(db, 'knowledge')

        # RF-CLOUD（设计 §4.5 方案1）：knowledge 不再声明可调用 cloud `tools`（已下沉本地），
        # 但保留 `capabilities`（含 required_scopes）。工作台知识库发现统一以 capability 为事实源，
        # 不再读 `tools[0]`（否则 tools 清空后 IndexError）。
        capability = manifest['manifest_json']['capabilities'][0]
        # 应用平台 v3 P3（设计 17 决策①）：挂载概念废除（`hasn_workspace_app` 退役）——
        # published 即可发现，不再有企业 override「显式 disabled 隐藏」一档；商业化准入由
        # _entitlement_denial 在调用面把关（发现面保持可见，付费墙在 invoke 时弹）。
        if not self._can_discover_tool(workspace=workspace, manifest=manifest, capability=capability):
            return self._capabilities_payload(workspace=workspace, agent=agent, manifest=manifest, tools=[])
        # 维度① 能力授权（D3 活取，唯一判定走 CapabilityGuard）：deny 的工具从发现里隐藏；ask/allow 仍可见。
        mode = await capability_guard.decide(
            db,
            agent_hasn_id=agent.agent_hasn_id,
            tool_name=capability['mcp_name'],
            required_scopes=list(capability.get('required_scopes') or []),
        )
        if mode == MODE_DENY:
            return self._capabilities_payload(workspace=workspace, agent=agent, manifest=manifest, tools=[])

        return self._capabilities_payload(
            workspace=workspace,
            agent=agent,
            manifest=manifest,
            tools=[
                {
                    'app_id': manifest['app_id'],
                    'tool_id': capability['tool_id'],
                    'mcp_name': capability['mcp_name'],
                    'collaboration_mode': manifest['collaboration_mode'],
                    'display_name': capability['name'],
                    'input_schema': capability['input_schema'],
                    'output_schema': capability['output_schema'],
                    'required_scopes': list(capability.get('required_scopes') or []),
                    'risk_level': capability['risk_level'],
                    'requires_confirmation': bool(
                        (capability.get('human_confirmation') or {}).get('required', False)
                    ),
                    'idempotent': bool(capability.get('idempotent', True)),
                }
            ],
        )

    def _capabilities_payload(
        self,
        *,
        workspace: dict[str, Any],
        agent: AgentTokenPayload | None,
        manifest: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            'workspace': self._workspace_payload(workspace),
            'agent': self._agent_payload(agent),
            'manifest_hash': manifest['manifest_hash'],
            'tools': tools,
        }

    async def call_tool(
        self,
        db: AsyncSession,
        *,
        request: Request,
        app_id: str,
        tool_id: str,
        body: AiNativeToolCallRequest,
    ) -> dict[str, Any]:
        """编排器：鉴权 → 解析工作区 → 统一闸门授权 → 分发执行 → 审计放行。

        各闸门（维度① 三态 + 工作区/角色/输入）抽到 `_authorize_tool_call`，handler 分发抽到
        `_dispatch_tool`，本方法只串流程。
        """
        agent_result = await self._authenticate_runtime_agent(request)
        if agent_result.get('decision') == 'deny':
            manifest = await ai_native_app_registry.ensure_builtin_published(db, app_id)
            return await self._deny(
                db,
                body=body,
                workspace=self._fallback_personal_workspace(agent_result.get('agent')),
                agent=agent_result.get('agent'),
                manifest=manifest,
                capability=self._find_capability(manifest, tool_id),
                tool=self._find_tool(manifest, tool_id),
                code=agent_result['code'],
                reason=agent_result['message'],
            )

        agent = agent_result['agent']
        try:
            workspace = await self._resolve_workspace(db, agent=agent, requested_workspace=body.workspace)
        except _RuntimeDenialError as denial:
            manifest = await ai_native_app_registry.ensure_builtin_published(db, app_id)
            return await self._deny(
                db,
                body=body,
                workspace=denial.workspace,
                agent=agent,
                manifest=manifest,
                capability=self._find_capability(manifest, tool_id),
                tool=self._find_tool(manifest, tool_id),
                code=denial.code,
                reason=denial.message,
            )

        manifest = await ai_native_app_registry.ensure_builtin_published(db, app_id)
        tool = self._find_tool(manifest, tool_id)
        capability = self._find_capability(manifest, tool_id)
        input_payload = dict(body.input or {})

        # 入参绑定接缝（候选①）：按 capability.input_schema 校验 + 强转 + 回填默认，使 manifest
        # 成为工具入参的唯一事实源——下游授权闸门与 handler 都拿到规范化 typed 入参（也让
        # ask_gate 的 args_hash 规范化）。声明字段从严（缺必填/转不动/越枚举/越界 → 15020 deny），
        # 未声明字段原样透传（向后兼容，不破坏现有 handler）。
        try:
            input_payload = bind_tool_input(capability.get('input_schema'), input_payload)
        except ToolInputError as exc:
            return await self._deny(
                db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                capability=capability, tool=tool, code='15020',
                reason=f'input_invalid:{exc.field}:{exc.reason}',
            )

        # 令牌重试模型（doc15 §3.1）：批准后 hasn-mcp 带 `X-Capability-Ticket` 头重发同一调用，
        # 网关在 ask 闸门前验票跳闸。两种到达面：
        # - 中继面（FastAPI 路由，真实 Request）：维度① 三态闸门由本网关执行，能力票走请求头。
        # - MCP 直连面（AppTool shim，`state.mcp_face=True`）：维度① 三态 + 验票已在 server.call_tool
        #   经 ContextVar 完成；本网关跳过重复闸门（skip_mode_gate），否则一次性票会被二次消费、
        #   已批准的 ask 调用反被网关重新挂起审批。其余 app 专属闸门（启用/协作/角色/输入）照常跑。
        mcp_face = bool(getattr(request.state, 'mcp_face', False))
        capability_ticket = request.headers.get('X-Capability-Ticket') if not mcp_face else None
        denied = await self._authorize_tool_call(
            db,
            body=body,
            workspace=workspace,
            agent=agent,
            manifest=manifest,
            tool=tool,
            capability=capability,
            input_payload=input_payload,
            capability_ticket=capability_ticket,
            skip_mode_gate=mcp_face,
        )
        if denied is not None:
            return denied

        try:
            result = await self._dispatch_tool(
                db,
                app_id=app_id,
                tool_id=tool_id,
                workspace=workspace,
                agent=agent,
                input_payload=input_payload,
                manifest=manifest,
            )
        except InstanceResolutionError as exc:
            # 实例未配置 / 凭据无效 / transport 不允许此调用面（15050/15051/15052）→ 写 deny 审计 + 返回错误。
            return await self._deny(
                db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                capability=capability, tool=tool, code=exc.code, reason=exc.message,
            )
        audit = await self._write_audit(
            db,
            trace_id=body.trace_id,
            workspace=workspace,
            agent=agent,
            manifest=manifest,
            capability=capability,
            tool=tool,
            decision='allow',
            result_ref=f'{app_id}:{tool_id}:{body.trace_id}',
        )
        return {
            'trace_id': body.trace_id,
            'decision': 'allow',
            'workspace': self._workspace_payload(workspace),
            'app_id': app_id,
            'tool_id': tool_id,
            'result': result,
            'audit_id': audit['id'],
        }

    async def _authorize_tool_call(
        self,
        db: AsyncSession,
        *,
        body: AiNativeToolCallRequest,
        workspace: dict[str, Any],
        agent: AgentTokenPayload,
        manifest: dict[str, Any],
        tool: dict[str, Any],
        capability: dict[str, Any],
        input_payload: dict[str, Any],
        capability_ticket: str | None = None,
        skip_mode_gate: bool = False,
    ) -> dict[str, Any] | None:
        """跑完所有闸门；命中任一返回 deny payload，全过返回 None。

        顺序：app 启用(15002) → 协作模式(15005) → 维度① 三态(deny 15012 / ask：带有效票跳闸→
        放行；无票→开审批回 approval_required) → 企业角色(15004) → 输入校验(15020)。维度① 走唯一
        判定服务 CapabilityGuard。

        ``skip_mode_gate``：MCP 直连面（AppTool shim）专用——维度① 三态闸门 + 一次性票验票已在
        server.call_tool 完成（票走 ContextVar），此处跳过维度① 避免二次验票/二次消费；其余 app
        专属闸门（启用/协作/角色/输入）server 层不做，仍需在此执行。
        """
        tool_name = tool.get('mcp_name') or tool['tool_id']
        # 应用平台 v3 P3（设计 17 决策①）：挂载概念废除（`hasn_workspace_app` 退役）——
        # published 即可调用，无任何启用/企业 override 步骤；准入仅由下方 entitlement 维度把关。

        # C4 闸门③（设计 §5.3③，安全关键）：付费 app 的 entitlement 维度。
        # **两条到达面都过**（中继路由 + MCP 直连面 shim），不放进 `skip_mode_gate` 块——
        # 否则分身经 MCP 直连面即可绕过付费墙白嫖（见 [[feedback_ai_native_gateway_two_call_faces]]）。
        # 仅付费 app（catalog 存在且 access_type != free）才判定；无 catalog 行 / free → 跳过零开销。
        if (denial := await self._entitlement_denial(db, app_id=manifest['app_id'], agent=agent)) is not None:
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15030', reason=denial)

        collaboration_denial = self._collaboration_denial(workspace=workspace, manifest=manifest)
        if collaboration_denial is not None:
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15005', reason=collaboration_denial)

        # MCP 直连面：维度① 三态 + 验票已在 server.call_tool 完成，跳过此处避免二次消费一次性票。
        if not skip_mode_gate:
            mode = await capability_guard.decide(
                db, agent_hasn_id=agent.agent_hasn_id, tool_name=tool_name,
                required_scopes=list(tool.get('required_scopes') or []),
            )
            if mode == MODE_DENY:
                return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                        capability=capability, tool=tool, code='15012', reason='agent_scope_missing')
            if mode == MODE_ASK:
                # 令牌重试模型（doc15 §3.1）：云端**不长挂**。AI-Native 同步面即「中继路径」
                # （Hermes→hasn-mcp→BackendGateway→本网关）。批准后 hasn-mcp 带有效
                # `X-Capability-Ticket` 重发同一调用 → 验票（agent/tool/args_hash 匹配且 jti 未用）
                # 成功 → 原子消费后**跳过 ask 闸门**落到下面执行；无票 / 票无效 → 开一条审批请求并把
                # 完整 `approval_required`(MCP_9215) 信封连同 request_id 回给中继（绝不当次放行，零 fake）。
                if not await self._consume_capability_ticket(agent, tool_name, input_payload, capability_ticket):
                    from backend.app.mcp.ask_gate import ask_approval_gate
                    from backend.common.security.agent_jwt import get_agent_scopes_cached

                    policy = await get_agent_scopes_cached(agent.agent_hasn_id, db)
                    envelope = await ask_approval_gate.open_request(
                        agent_hasn_id=agent.agent_hasn_id, owner_hasn_id=agent.owner_hasn_id,
                        tool_name=tool_name, required_scopes=list(tool.get('required_scopes') or []),
                        default_mode=policy.get('default_mode', 'allow'),
                        capability_modes=policy.get('capability_modes'),
                        arguments=input_payload,
                    )
                    return await self._approval_required(
                        db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                        capability=capability, tool=tool, envelope=envelope,
                    )
                # 验票通过 → 落到下面其余闸门 + 执行（工具体只在带票这次运行）。

        role_denial = self._enterprise_role_denial(workspace=workspace, capability=capability)
        if role_denial is not None:
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15004', reason=role_denial)

        if not self._valid_tool_input(tool['tool_id'], input_payload):
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15020', reason='input_schema_invalid')
        return None

    async def _dispatch_tool(
        self,
        db: AsyncSession,
        *,
        app_id: str,
        tool_id: str,
        workspace: dict[str, Any],
        agent: AgentTokenPayload,
        input_payload: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """按 实例 + transport 通用路由（设计 11 §6.2）。

        先向控制面 resolve() 拿实例句柄，再按 transport 分发：
        - gateway_internal → internal_handlers 注册表（消灭旧的 ``if app_id == ...`` 硬编码）
        - cloud_relay      → 云端 HMAC 签名转发到实例 endpoint（第三方 Tool API，P3 落地）
        - 其它             → 15052（Tool 面默认不走 daemon/frontend direct）
        """
        tool = self._find_tool(manifest, tool_id)
        handle = await instance_resolver.resolve(
            db, app_id=app_id, workspace=workspace, face=FACE_TOOL, manifest=manifest, tool_id=tool_id
        )
        if handle.transport == TRANSPORT_GATEWAY_INTERNAL:
            handler = self._internal_handlers().get(str(tool.get('handler') or tool_id))
            if handler is None:
                raise InstanceResolutionError(code='15050', message=f'internal_handler_missing:{tool_id}')
            return await handler(db, agent, input_payload)
        if handle.transport == TRANSPORT_CLOUD_RELAY:
            return await self._cloud_relay(handle, tool=tool, agent=agent, input_payload=input_payload)
        # Tool 面默认不允许 daemon_direct / frontend_direct（设计 11 §6.2）。
        raise InstanceResolutionError(code='15052', message=f'transport_not_allowed_for_tool:{handle.transport}')

    def _internal_handlers(self) -> dict[str, Any]:
        """gateway_internal 工具的 handler 注册表（懒构建 + 缓存）。

        键 = manifest ``tool.handler`` 字段；值 = async ``(db, agent, input_payload) -> dict``。
        取代旧的 ``if app_id == 'knowledge'/'community'`` 硬编码分发（设计 11 §6.2）。
        """
        if self._internal_handlers_cache is not None:
            return self._internal_handlers_cache
        from backend.app.hasn_community.service import community_tool_handlers as handlers
        from backend.app.hasn_creator.service import creator_tool_handlers as creator_handlers
        from backend.app.hasn_finance.service import finance_tool_handlers as finance_handlers
        from backend.app.hasn_growth.service import growth_tool_handlers as growth_handlers
        from backend.app.hasn_knowledge.service import tool_handlers as knowledge_handlers
        from backend.app.mcp.apps.quant import quant_tool_handlers as quant_handlers
        from backend.app.mcp.apps.studio import studio_tool_handlers as studio_handlers

        registry: dict[str, Any] = {
            # 创作运营（纯云端业务应用：定位/创作/审核/发布/复盘/进化，零本地操作 → 工具全走云端
            # gateway_internal，不经 hasn-node 本地 hasn-mcp / daemon Agent 代理；handler 落 hasn_creator service）
            'creator.project_list': creator_handlers.handle_project_list,
            'creator.project_get': creator_handlers.handle_project_get,
            'creator.project_create': creator_handlers.handle_project_create,
            'creator.profile_get': creator_handlers.handle_profile_get,
            'creator.profile_analyze': creator_handlers.handle_profile_analyze,
            'creator.profile_set': creator_handlers.handle_profile_set,
            'creator.account_add': creator_handlers.handle_account_add,
            'creator.account_list': creator_handlers.handle_account_list,
            'creator.competitor_log': creator_handlers.handle_competitor_log,
            'creator.topic_suggest': creator_handlers.handle_topic_suggest,
            'creator.content_create': creator_handlers.handle_content_create,
            'creator.content_list': creator_handlers.handle_content_list,
            'creator.content_get': creator_handlers.handle_content_get,
            'creator.content_update': creator_handlers.handle_content_update,
            'creator.content_stage_save': creator_handlers.handle_content_stage_save,
            'creator.publish_submit': creator_handlers.handle_publish_submit,
            'creator.publish_list': creator_handlers.handle_publish_list,
            'creator.publish_update_metrics': creator_handlers.handle_publish_update_metrics,
            'creator.insight_log': creator_handlers.handle_insight_log,
            'creator.pattern_search': creator_handlers.handle_pattern_search,
            'creator.report_overview': creator_handlers.handle_report_overview,
            # 获客（纯云端业务应用：CRM/获客/触达/成交，零本地操作 → 工具全走云端 gateway_internal，
            # 不经 hasn-node 本地 hasn-mcp / daemon Agent 代理；handler 落 hasn_growth service）
            'growth.lead_request': growth_handlers.handle_growth_lead_request,
            # 企业数据读穿中台（GROWTH-QCC-4）：内部调通用网关 call_system_tool(hasn.ext.qcc_*) + read-through 入池。
            'growth.lookup_company': growth_handlers.handle_growth_lookup_company,
            'growth.search_companies': growth_handlers.handle_growth_search_companies,
            'growth.enrich_company': growth_handlers.handle_growth_enrich_company,
            'growth.collect_start': growth_handlers.handle_growth_collect_start,
            'growth.collect_status': growth_handlers.handle_growth_collect_status,
            'growth.lead_search': growth_handlers.handle_growth_lead_search,
            'growth.lead_get': growth_handlers.handle_growth_lead_get,
            'growth.lead_qualify': growth_handlers.handle_growth_lead_qualify,
            'growth.lead_dismiss': growth_handlers.handle_growth_lead_dismiss,
            'growth.customer_list': growth_handlers.handle_growth_customer_list,
            'growth.customer_get': growth_handlers.handle_growth_customer_get,
            'growth.customer_timeline': growth_handlers.handle_growth_customer_timeline,
            'growth.customer_update_profile': growth_handlers.handle_growth_customer_update_profile,
            'growth.activity_log': growth_handlers.handle_growth_activity_log,
            'growth.customer_reassign': growth_handlers.handle_growth_customer_reassign,
            'growth.outreach_send': growth_handlers.handle_growth_outreach_send,
            'growth.outreach_status': growth_handlers.handle_growth_outreach_status,
            'growth.opportunity_create': growth_handlers.handle_growth_opportunity_create,
            'growth.opportunity_update_stage': growth_handlers.handle_growth_opportunity_update_stage,
            'growth.deal_close': growth_handlers.handle_growth_deal_close,
            'growth.report_funnel': growth_handlers.handle_growth_report_funnel,
            # 知识库（AI-Native 重做：工具回归 manifest App 工具，handler 落 knowledge service，
            # RAGFlow 为云端内部处理后端——《知识库AI-Native应用重设计》§2.4/§3）
            'knowledge.search': knowledge_handlers.handle_knowledge_search,
            'knowledge.list_datasets': knowledge_handlers.handle_knowledge_list_datasets,
            'knowledge.create_kb': knowledge_handlers.handle_knowledge_create_kb,
            'knowledge.delete_kb': knowledge_handlers.handle_knowledge_delete_kb,
            'knowledge.fetch_doc': knowledge_handlers.handle_knowledge_fetch_doc,
            'knowledge.list_documents': knowledge_handlers.handle_knowledge_list_documents,
            'knowledge.upload_document': knowledge_handlers.handle_knowledge_upload_document,
            'knowledge.delete_document': knowledge_handlers.handle_knowledge_delete_document,
            'knowledge.write_doc': knowledge_handlers.handle_knowledge_write_doc,
            'knowledge.list_folders': knowledge_handlers.handle_knowledge_list_folders,
            'knowledge.create_folder': knowledge_handlers.handle_knowledge_create_folder,
            'knowledge.update_folder': knowledge_handlers.handle_knowledge_update_folder,
            'knowledge.delete_folder': knowledge_handlers.handle_knowledge_delete_folder,
            # 帖子/文章详情走专用资源取数（含可见性鉴权 + reference_cards）
            'community.get_post': self._handle_community_get_post,
            'community.get_article': self._handle_community_get_article,
            # 读取（community:read）
            'community.get_feed': handlers.handle_community_get_feed,
            'community.get_comments': handlers.handle_community_get_comments,
            'community.search': handlers.handle_community_search,
            'community.discover_peers': handlers.handle_community_discover_peers,
            'community.get_profile': handlers.handle_community_get_profile,
            'community.get_profile_content': handlers.handle_community_get_profile_content,
            'community.get_trending_topics': handlers.handle_community_get_trending_topics,
            'community.get_recommended_agents': handlers.handle_community_get_recommended_agents,
            'community.get_notifications': handlers.handle_community_get_notifications,
            'community.mark_notifications_read': handlers.handle_community_mark_notifications_read,
            # 发布（community:post）
            'community.create_post': handlers.handle_community_create_post,
            'community.create_article': handlers.handle_community_create_article,
            # 评论（community:comment）
            'community.create_comment': handlers.handle_community_create_comment,
            # 互动（community:interact）
            'community.like': handlers.handle_community_like,
            'community.unlike': handlers.handle_community_unlike,
            'community.follow': handlers.handle_community_follow,
            'community.unfollow': handlers.handle_community_unfollow,
            'community.collect': handlers.handle_community_collect,
            'community.uncollect': handlers.handle_community_uncollect,
            # 话题（community:read）— 实施/95 §2.5
            'community.get_topic': handlers.handle_community_get_topic,
            'community.get_topic_feed': handlers.handle_community_get_topic_feed,
            # 圈子读（community:read）
            'community.get_circle': handlers.handle_community_get_circle,
            'community.get_circle_feed': handlers.handle_community_get_circle_feed,
            'community.discover_circles': handlers.handle_community_discover_circles,
            'community.list_my_circles': handlers.handle_community_list_my_circles,
            # 圈子参与（community:circle）
            'community.join_circle': handlers.handle_community_join_circle,
            'community.leave_circle': handlers.handle_community_leave_circle,
            # 文档读（community:read）
            'community.get_doc_space': handlers.handle_community_get_doc_space,
            'community.get_doc_tree': handlers.handle_community_get_doc_tree,
            # 文档创作（community:doc）
            'community.create_doc_space': handlers.handle_community_create_doc_space,
            'community.create_doc_node': handlers.handle_community_create_doc_node,
            # 金融数据（纯云端只读数据应用：14 工具全 finance:read，gateway_internal → finance_provider →
            # finance-data-service；唯一接触 akshare 处隔离独立服务，主云端进程不引 akshare。模块 24 §4/§5）
            'finance.stock_quote_history': finance_handlers.handle_stock_quote_history,
            'finance.stock_realtime': finance_handlers.handle_stock_realtime,
            'finance.stock_info': finance_handlers.handle_stock_info,
            'finance.stock_fund_flow': finance_handlers.handle_stock_fund_flow,
            'finance.stock_billboard': finance_handlers.handle_stock_billboard,
            'finance.stock_financial': finance_handlers.handle_stock_financial,
            'finance.hk_quote_history': finance_handlers.handle_hk_quote_history,
            'finance.us_quote_history': finance_handlers.handle_us_quote_history,
            'finance.index_quote_history': finance_handlers.handle_index_quote_history,
            'finance.fund_nav_history': finance_handlers.handle_fund_nav_history,
            'finance.fund_position': finance_handlers.handle_fund_position,
            'finance.futures_quote_history': finance_handlers.handle_futures_quote_history,
            'finance.macro_indicator': finance_handlers.handle_macro_indicator,
            'finance.bond_quote_history': finance_handlers.handle_bond_quote_history,
            # 量化交易（cloud-brokered 业务应用：5 个回测线工具，gateway_internal → quant_service →
            # quant_engine_provider → quant-engine-service；唯一接触 NautilusTrader 处隔离独立服务。模块 14 doc23 §3/§6。
            # 实盘线 deploy_live/submit_order/resume 受 P0-闸1 硬闸 + 真钱 gated，本期不注册）
            'quant.save_strategy': quant_handlers.handle_save_strategy,
            'quant.list_strategies': quant_handlers.handle_list_strategies,
            'quant.get_strategy': quant_handlers.handle_get_strategy,
            'quant.backtest': quant_handlers.handle_backtest,
            'quant.get_backtest': quant_handlers.handle_get_backtest,
            # 统一视频引擎（cloud-brokered 业务应用，模块 14 doc22 §3/§3.5）：12 工具 gateway_internal →
            # studio_service → montage_engine_provider → montage-engine-service；唯一接触 OpenMontage/Remotion 处
            # 隔离独立服务。read/write 出厂 allow；render/run_pipeline/run_tool/export 出厂 ask（花算力/外发）。
            # 双重身份：任意应用的分身都能编排调 studio.run_pipeline，产物经 hasn://asset/ 回填组合（零数据合并）。
            'studio.list_pipelines': studio_handlers.handle_list_pipelines,
            'studio.list_projects': studio_handlers.handle_list_projects,
            'studio.get_project': studio_handlers.handle_get_project,
            'studio.list_assets': studio_handlers.handle_list_assets,
            'studio.list_artifacts': studio_handlers.handle_list_artifacts,
            'studio.get_render_job': studio_handlers.handle_get_render_job,
            'studio.save_project': studio_handlers.handle_save_project,
            'studio.save_storyboard': studio_handlers.handle_save_storyboard,
            'studio.run_pipeline': studio_handlers.handle_run_pipeline,
            'studio.render': studio_handlers.handle_render,
            'studio.run_tool': studio_handlers.handle_run_tool,
            'studio.export': studio_handlers.handle_export,
            # 分享/发布（§3.6 全复用 resource_share + M18 web 发布）：出厂 ask（外发，主人裁决）。
            'studio.share': studio_handlers.handle_share,
            'studio.publish': studio_handlers.handle_publish,
        }
        self._internal_handlers_cache = registry
        return registry

    async def _handle_community_get_post(
        self, db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await community_service.get_agent_post_resource(
            db, agent=agent, post_id=str(input_payload['post_id'])
        )

    async def _handle_community_get_article(
        self, db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await community_service.get_agent_article_resource(
            db, agent=agent, article_id=str(input_payload['article_id'])
        )

    async def _cloud_relay(
        self,
        handle: Any,
        *,
        tool: dict[str, Any],
        agent: AgentTokenPayload,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """cloud_relay：云端用登记的 app secret HMAC 签名后转发到实例 endpoint（设计 11 §6.2/§10）。

        app secret 仅存云端、此处代签，绝不下发 daemon/前端。签名头遵循 HExt-08 §16.4：
        X-HASN-App-Id / X-HASN-Timestamp / X-HASN-Nonce / X-HASN-Signature(HMAC-SHA256)。
        签名串 = app_id\\n timestamp\\n nonce\\n body。
        """
        import hashlib
        import hmac
        import json
        import time
        import uuid

        import httpx

        if not handle.endpoint:
            raise InstanceResolutionError(code='15050', message='instance_endpoint_missing')
        secret = (handle.credential or {}).get('value') or ''
        if not secret:
            raise InstanceResolutionError(code='15051', message='instance_credential_invalid')

        payload = {
            'tool_id': tool['tool_id'],
            'input': input_payload,
            'actor': {'agent_hasn_id': agent.agent_hasn_id, 'owner_hasn_id': agent.owner_hasn_id},
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        signing_string = f'{handle.app_id}\n{ts}\n{nonce}\n{body}'
        signature = hmac.new(secret.encode('utf-8'), signing_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {
            'Content-Type': 'application/json',
            'X-HASN-App-Id': handle.app_id,
            'X-HASN-Timestamp': ts,
            'X-HASN-Nonce': nonce,
            'X-HASN-Signature': signature,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(handle.endpoint, content=body.encode('utf-8'), headers=headers)
        except httpx.HTTPError as exc:
            raise InstanceResolutionError(code='15050', message=f'instance_unreachable:{exc.__class__.__name__}') from exc
        if resp.status_code != 200:
            raise InstanceResolutionError(code='15050', message=f'instance_http_{resp.status_code}')
        try:
            return resp.json()
        except ValueError as exc:
            raise InstanceResolutionError(code='15050', message='instance_bad_response') from exc

    async def _deny(
        self,
        db: AsyncSession,
        *,
        body: AiNativeToolCallRequest,
        workspace: dict[str, Any],
        agent: AgentTokenPayload | None,
        manifest: dict[str, Any],
        capability: dict[str, Any],
        tool: dict[str, Any],
        code: str,
        reason: str,
    ) -> dict[str, Any]:
        """写 deny 审计 + 返回 deny payload（各闸门共用，避免逐处重复 13 行模板）。"""
        audit = await self._write_audit(
            db,
            trace_id=body.trace_id,
            workspace=workspace,
            agent=agent,
            manifest=manifest,
            capability=capability,
            tool=tool,
            decision='deny',
            error_code=code,
            context={'reason': reason},
        )
        return self._deny_payload(body.trace_id, code, reason, audit_id=audit['id'])

    async def _approval_required(
        self,
        db: AsyncSession,
        *,
        body: AiNativeToolCallRequest,
        workspace: dict[str, Any],
        agent: AgentTokenPayload | None,
        manifest: dict[str, Any],
        capability: dict[str, Any],
        tool: dict[str, Any],
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        """ask 命中：写 approval_required 审计 + 回带 request_id 的 MCP_9215 信封（不长挂）。

        中继（hasn-mcp）据 `decision == 'approval_required'` + `approval.request_id`
        驱动 ApprovalBroker 挂起 → 主人批准换一次性票 → 带 `X-Capability-Ticket` 重试
        （doc15 §3.1，专利 H10）。`envelope` 即 `ask_approval_gate.open_request()` 的返回。
        """
        approval = dict(envelope.get('approval') or {})
        code = str(envelope.get('code') or 'MCP_9215')
        message = str(envelope.get('message') or 'agent_capability_ask_pending')
        audit = await self._write_audit(
            db,
            trace_id=body.trace_id,
            workspace=workspace,
            agent=agent,
            manifest=manifest,
            capability=capability,
            tool=tool,
            decision='approval_required',
            error_code=code,
            context={'reason': 'agent_capability_ask_pending', 'request_id': approval.get('request_id')},
        )
        return {
            'trace_id': body.trace_id,
            'decision': 'approval_required',
            'error': {'code': code, 'message': message},
            'approval': approval,
            'audit_id': audit['id'],
        }

    async def _consume_capability_ticket(
        self,
        agent: AgentTokenPayload,
        tool_name: str,
        arguments: dict[str, Any],
        ticket: str | None,
    ) -> bool:
        """中继面 ask 验票跳闸（A-P5）。镜像 `mcp/server.py:_consume_capability_ticket`，但票据由
        FastAPI 请求头 `X-Capability-Ticket` 直接传入（非 MCP 传输层 ContextVar）。

        票有效（签名 + 未过期 + agent/tool/args_hash 匹配 + jti 未用 → 原子消费）→ 标记审批请求
        consumed + 返回 True（跳过 ask 闸门，工具体只在带票这次运行）；无票 / 票无效 / 重放 → False。
        """
        if not ticket:
            return False
        from backend.app.mcp.ask_gate import _canonical_args_hash, ask_approval_gate
        from backend.common.security.capability_ticket import consume_capability_ticket

        claims = await consume_capability_ticket(
            ticket,
            agent_hasn_id=agent.agent_hasn_id,
            tool_name=tool_name,
            args_hash=_canonical_args_hash(arguments),
        )
        if not claims:
            return False
        await ask_approval_gate.mark_consumed(str(claims.get('request_id') or ''))
        return True

    async def list_audit(
        self,
        db: AsyncSession,
        *,
        query: AiNativeAuditQuery,
        owner_hasn_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        stmt = sa.select(HasnAiNativeAppAudit)
        # owner-scope 硬过滤（服务端权威，防越权读他人分身审计）：调用方传入登录人 hasn_id 后，
        # 只返回 owner_hasn_id 命中的行；不取 query（identity by auth）。`agent_hasn_id` 仅作进一步
        # 收窄——即便客户端传他人分身 ID，也被 owner 过滤兜底，读不到别人的审计。
        if owner_hasn_id:
            stmt = stmt.where(HasnAiNativeAppAudit.owner_hasn_id == owner_hasn_id)
        if query.workspace_kind:
            stmt = stmt.where(HasnAiNativeAppAudit.workspace_kind == query.workspace_kind)
        if query.app_id:
            stmt = stmt.where(HasnAiNativeAppAudit.app_id == query.app_id)
        if query.agent_hasn_id:
            stmt = stmt.where(HasnAiNativeAppAudit.agent_hasn_id == query.agent_hasn_id)
        if query.trace_id:
            stmt = stmt.where(HasnAiNativeAppAudit.trace_id == query.trace_id)
        if query.created_at_from:
            stmt = stmt.where(HasnAiNativeAppAudit.created_at >= query.created_at_from)
        if query.created_at_to:
            stmt = stmt.where(HasnAiNativeAppAudit.created_at <= query.created_at_to)
        capped = max(1, min(limit, 500))
        rows = (
            await db.execute(stmt.order_by(HasnAiNativeAppAudit.id.desc()).limit(capped))
        ).scalars().all()
        return {'items': [self._audit_payload(row) for row in rows], 'total': len(rows)}

    def _require_agent(self, request: Request) -> AgentTokenPayload:
        agent = getattr(request.state, 'agent', None)
        if agent is None:
            raise errors.TokenError(msg='Agent JWT 未认证')
        return agent

    async def _authenticate_runtime_agent(self, request: Request) -> dict[str, Any]:
        agent = getattr(request.state, 'agent', None)
        if agent is not None:
            return {'decision': 'allow', 'agent': agent}

        authorization = request.headers.get('Authorization') or request.headers.get('authorization')
        scheme, token = get_authorization_scheme_param(authorization)
        if not token or scheme.lower() != 'bearer':
            return {'decision': 'deny', 'code': '15010', 'message': 'agent_jwt_missing', 'agent': None}

        try:
            decoded = jwt_decode_agent(token)
        except errors.TokenError:
            return {'decision': 'deny', 'code': '15010', 'message': 'agent_jwt_invalid', 'agent': None}

        redis_token = await redis_client.get(f'agent_token:{decoded.agent_hasn_id}:{decoded.session_uuid}')
        if not redis_token:
            return {
                'decision': 'deny',
                'code': '15011',
                'message': 'agent_token_session_revoked',
                'agent': decoded,
            }
        if token != redis_token:
            return {
                'decision': 'deny',
                'code': '15011',
                'message': 'agent_token_session_mismatch',
                'agent': decoded,
            }

        request.state.agent = decoded
        return {'decision': 'allow', 'agent': decoded}

    def _fallback_personal_workspace(self, agent: AgentTokenPayload | None) -> dict[str, Any]:
        user_id = agent.owner_user_id if agent is not None else None
        return {
            'kind': 'personal',
            'user_id': user_id,
            'enterprise_id': None,
            'workspace_key': f'personal:{user_id}' if user_id is not None else 'personal:unknown',
        }

    async def _resolve_workspace(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload | None,
        requested_workspace: dict[str, Any] | None,
    ) -> dict[str, Any]:
        workspace = dict(
            requested_workspace
            or await workbench_domain_service.get_active_workspace(db, user_id=agent.owner_user_id)
        )
        kind = workspace.get('kind') or 'personal'
        if kind not in {'personal', 'enterprise'}:
            raise _RuntimeDenialError(
                code='15003',
                message='workspace_inaccessible',
                workspace=self._fallback_personal_workspace(agent),
            )
        workspace['kind'] = kind
        if kind == 'personal':
            workspace['user_id'] = agent.owner_user_id
            workspace['enterprise_id'] = None
            workspace['workspace_key'] = f'personal:{agent.owner_user_id}'
            return workspace

        enterprise_id = workspace.get('enterprise_id')
        if enterprise_id is None:
            raise _RuntimeDenialError(
                code='15003',
                message='workspace_inaccessible',
                workspace=self._fallback_enterprise_workspace(None),
            )
        membership = await workbench_domain_service._approved_membership(
            db, enterprise_id=int(enterprise_id), user_id=agent.owner_user_id
        )
        if membership is None:
            raise _RuntimeDenialError(
                code='15003',
                message='workspace_inaccessible',
                workspace=self._fallback_enterprise_workspace(int(enterprise_id)),
            )
        workspace['user_id'] = None
        workspace['enterprise_id'] = int(enterprise_id)
        workspace['workspace_key'] = f'enterprise:{enterprise_id}'
        workspace['role'] = getattr(membership, 'role', 'member')
        return workspace

    def _fallback_enterprise_workspace(self, enterprise_id: int | None) -> dict[str, Any]:
        return {
            'kind': 'enterprise',
            'user_id': None,
            'enterprise_id': enterprise_id,
            'workspace_key': f'enterprise:{enterprise_id}' if enterprise_id is not None else 'enterprise:unknown',
        }

    async def _entitlement_denial(
        self, db: AsyncSession, *, app_id: str, agent: AgentTokenPayload
    ) -> str | None:
        """付费 app 准入维度（设计 §5.3③）：未准入返回审计 reason，准入/免费返回 None。

        只有 catalog 中存在且 ``access_type != free`` 的 app 才判定（无 catalog 行 / free 零开销跳过）。
        owner 维度按 ``agent.owner_hasn_id`` 实时判 tier / active entitlement（零映射，安全路径直取）。

        doc04 偏差2 修复：owner 维度不通时**叠加企业维度**——主人当前激活企业空间
        （``hasn_owner_workbench_pref.active_enterprise_id``）且应用有企业形态时，按企业权益 +
        命名席位（member=该主人）再判一次；任一维度通过即放行（与 ``list_apps`` 的
        ``merge_access``「allowed = owner OR enterprise」口径一致）。否则企业买了席位的成员，
        其分身调该应用工具会被误拒 entitlement_denied（人能打开应用、分身不能干活）。
        """
        cat = await app_catalog_service.get_catalog(db, app_id=app_id)
        if cat is None or (cat.access_type or 'free') == 'free':
            return None
        access = await app_catalog_service.resolve_app_access(
            db, catalog=cat, owner_hasn_id=agent.owner_hasn_id
        )
        if access['allowed']:
            return None
        if 'enterprise' in (cat.scope or []):
            from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref

            active_enterprise_id = (
                (
                    await db.execute(
                        sa.select(HasnOwnerWorkbenchPref.active_enterprise_id).where(
                            HasnOwnerWorkbenchPref.owner_hasn_id == agent.owner_hasn_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if active_enterprise_id is not None:
                enterprise_access = await app_catalog_service.resolve_app_access(
                    db,
                    catalog=cat,
                    owner_hasn_id=agent.owner_hasn_id,
                    subject_type='enterprise',
                    subject_id=str(active_enterprise_id),
                    member_hasn_id=agent.owner_hasn_id,
                )
                if enterprise_access['allowed']:
                    return None
        return 'entitlement_denied'

    # 应用平台 v3 P3（设计 17 决策①）：挂载概念废除——_get_workspace_app（查 hasn_workspace_app
    # 挂载行）已删除，发现/调用准入只看 catalog published + entitlement（_entitlement_denial）。

    def _find_tool(self, manifest: dict[str, Any], tool_id: str) -> dict[str, Any]:
        for tool in manifest['manifest_json'].get('tools') or []:
            if tool.get('tool_id') == tool_id:
                return tool
        raise errors.NotFoundError(msg='AI-Native 工具不存在')

    def _find_capability(self, manifest: dict[str, Any], tool_id: str) -> dict[str, Any]:
        for capability in manifest['manifest_json'].get('capabilities') or []:
            if capability.get('tool_id') == tool_id:
                return capability
        raise errors.NotFoundError(msg='AI-Native 能力不存在')

    def _can_discover_tool(
        self,
        *,
        workspace: dict[str, Any],
        manifest: dict[str, Any],
        capability: dict[str, Any],
    ) -> bool:
        manifest_json = manifest.get('manifest_json') or {}
        workspace_scope = set(manifest.get('workspace_scope') or manifest_json.get('workspace_scope') or [])
        if workspace_scope and workspace['kind'] not in workspace_scope:
            return False
        if self._collaboration_denial(workspace=workspace, manifest=manifest) is not None:
            return False
        return self._enterprise_role_denial(workspace=workspace, capability=capability) is None

    def _collaboration_denial(self, *, workspace: dict[str, Any], manifest: dict[str, Any]) -> str | None:
        if workspace['kind'] != 'enterprise':
            return None
        manifest_json = manifest.get('manifest_json') or {}
        collaboration_mode = str(
            manifest.get('collaboration_mode') or manifest_json.get('collaboration_mode') or 'none'
        )
        if collaboration_mode != 'workspace_shared':
            return 'app_not_support_enterprise_collaboration'
        return None

    def _enterprise_role_denial(self, *, workspace: dict[str, Any], capability: dict[str, Any]) -> str | None:
        if workspace['kind'] != 'enterprise':
            return None
        workspace_roles = set(capability.get('workspace_roles') or [])
        if workspace_roles and self._workspace_role(workspace) not in workspace_roles:
            return 'enterprise_role_insufficient'
        return None

    def _workspace_role(self, workspace: dict[str, Any]) -> str:
        return workspace.get('role') or ('owner' if workspace['kind'] == 'personal' else 'member')

    def _valid_tool_input(self, tool_id: str, data: dict[str, Any]) -> bool:
        # RF-CLOUD：knowledge.search 已下沉为本地 Platform 工具，云端不再校验其入参。
        rule = _COMMUNITY_INPUT_RULES.get(tool_id)
        if rule is None:
            return True
        return self._check_community_rule(rule, data)

    def _check_community_rule(self, rule: dict[str, Any], data: dict[str, Any]) -> bool:
        """按声明式规则校验社区工具入参（必填字符串 / 枚举 / 长度上界 / limit）。"""
        for field in rule.get('required_str', []):
            val = data.get(field)
            if not isinstance(val, str) or not val.strip():
                return False
        for field, allowed in rule.get('enums', {}).items():
            val = data.get(field)
            if not isinstance(val, str) or val not in allowed:
                return False
        for field, maxlen in rule.get('maxlen', {}).items():
            val = data.get(field)
            if isinstance(val, str) and len(val) > maxlen:
                return False
        return self._valid_limit(data, default_max=50)

    def _valid_limit(self, data: dict[str, Any], *, default_max: int) -> bool:
        if 'limit' not in data or data['limit'] is None:
            return True
        try:
            limit = int(data['limit'])
        except (TypeError, ValueError):
            return False
        return 1 <= limit <= default_max

    def _deny_payload(self, trace_id: str, code: str, message: str, *, audit_id: int) -> dict[str, Any]:
        return {
            'trace_id': trace_id,
            'decision': 'deny',
            'error': {'code': code, 'message': message},
            'audit_id': audit_id,
        }

    async def _write_audit(
        self,
        db: AsyncSession,
        *,
        trace_id: str,
        workspace: dict[str, Any],
        agent: AgentTokenPayload,
        manifest: dict[str, Any],
        capability: dict[str, Any],
        tool: dict[str, Any],
        decision: str,
        error_code: str | None = None,
        result_ref: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = HasnAiNativeAppAudit(
            trace_id=trace_id,
            step='runtime',
            workspace_kind=workspace['kind'],
            user_id=workspace.get('user_id'),
            enterprise_id=workspace.get('enterprise_id'),
            app_id=manifest['app_id'],
            app_version=manifest['version'],
            actor_type='agent',
            agent_hasn_id=agent.agent_hasn_id if agent is not None else None,
            owner_hasn_id=agent.owner_hasn_id if agent is not None else None,
            session_uuid=agent.session_uuid if agent is not None else None,
            method='tool_call',
            capability_id=capability.get('capability_id'),
            tool_id=tool.get('tool_id'),
            event_type='tool_call',
            required_scopes=list(tool.get('required_scopes') or []),
            # scopes 已退役（实施102 S0）：AgentTokenPayload 不再携带 scopes，审计快照恒空。
            agent_scopes_snapshot=[],
            workspace_role=self._workspace_role(workspace),
            risk_level=tool.get('risk_level'),
            decision=decision,
            confirmation_id=None,
            result_ref=result_ref,
            error_code=error_code,
            context=context or {},
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return self._audit_payload(row)

    def _workspace_payload(self, workspace: dict[str, Any]) -> dict[str, Any]:
        return {
            'kind': workspace['kind'],
            'user_id': workspace.get('user_id'),
            'enterprise_id': workspace.get('enterprise_id'),
            'workspace_key': workspace['workspace_key'],
        }

    def _agent_payload(self, agent: AgentTokenPayload) -> dict[str, Any]:
        return {
            'agent_hasn_id': agent.agent_hasn_id,
            'owner_hasn_id': agent.owner_hasn_id,
            'session_uuid': agent.session_uuid,
        }

    def _audit_payload(self, row: HasnAiNativeAppAudit) -> dict[str, Any]:
        return {
            'id': row.id,
            'trace_id': row.trace_id,
            'step': row.step,
            'workspace_kind': row.workspace_kind,
            'user_id': row.user_id,
            'enterprise_id': row.enterprise_id,
            'app_id': row.app_id,
            'app_version': row.app_version,
            'actor_type': row.actor_type,
            'agent_hasn_id': row.agent_hasn_id,
            'owner_hasn_id': row.owner_hasn_id,
            'session_uuid': row.session_uuid,
            'method': row.method,
            'capability_id': row.capability_id,
            'tool_id': row.tool_id,
            'event_type': row.event_type,
            'required_scopes': row.required_scopes,
            'agent_scopes_snapshot': row.agent_scopes_snapshot,
            'workspace_role': row.workspace_role,
            'risk_level': row.risk_level,
            'decision': row.decision,
            'confirmation_id': row.confirmation_id,
            'result_ref': row.result_ref,
            'error_code': row.error_code,
            'context': row.context,
            'created_at': row.created_at,
        }


ai_native_runtime_gateway: AiNativeRuntimeGateway = AiNativeRuntimeGateway()
