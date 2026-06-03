from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from fastapi.security.utils import get_authorization_scheme_param

from backend.app.hasn.model import HasnAiNativeAppAudit, HasnWorkspaceApp
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

        tool = manifest['manifest_json']['tools'][0]
        capability = manifest['manifest_json']['capabilities'][0]
        if not await self._is_workspace_app_available(db, workspace=workspace, app_id=manifest['app_id']):
            # 去掉安装态：published 即可用，仅企业 override 显式 disabled 时从发现里隐藏（设计 11 §4）。
            return self._capabilities_payload(workspace=workspace, agent=agent, manifest=manifest, tools=[])
        if not self._can_discover_tool(workspace=workspace, manifest=manifest, capability=capability):
            return self._capabilities_payload(workspace=workspace, agent=agent, manifest=manifest, tools=[])
        # 维度① 能力授权（D3 活取，唯一判定走 CapabilityGuard）：deny 的工具从发现里隐藏；ask/allow 仍可见。
        mode = await capability_guard.decide(
            db, agent_hasn_id=agent.agent_hasn_id, tool_name=tool['mcp_name'], required_scopes=tool['required_scopes']
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
                    'tool_id': tool['tool_id'],
                    'mcp_name': tool['mcp_name'],
                    'collaboration_mode': manifest['collaboration_mode'],
                    'display_name': capability['name'],
                    'input_schema': capability['input_schema'],
                    'output_schema': capability['output_schema'],
                    'required_scopes': tool['required_scopes'],
                    'risk_level': tool['risk_level'],
                    'requires_confirmation': False,
                    'idempotent': tool['idempotent'],
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

        denied = await self._authorize_tool_call(
            db,
            body=body,
            workspace=workspace,
            agent=agent,
            manifest=manifest,
            tool=tool,
            capability=capability,
            input_payload=input_payload,
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
    ) -> dict[str, Any] | None:
        """跑完所有闸门；命中任一返回 deny payload，全过返回 None。

        顺序：app 启用(15002) → 协作模式(15005) → 维度① 三态(deny 15012 / ask 挂起→拒 15013)
        → 企业角色(15004) → 输入校验(15020)。维度① 走唯一判定服务 CapabilityGuard。
        """
        tool_name = tool.get('mcp_name') or tool['tool_id']
        # 去掉安装态：published 即可调用，无需任何启用步骤；仅企业 override 显式 disabled 时拒（设计 11 §4.2/§4.3）。
        if not await self._is_workspace_app_available(db, workspace=workspace, app_id=manifest['app_id']):
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15002', reason='app_disabled_by_enterprise')

        collaboration_denial = self._collaboration_denial(workspace=workspace, manifest=manifest)
        if collaboration_denial is not None:
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15005', reason=collaboration_denial)

        mode = await capability_guard.decide(
            db, agent_hasn_id=agent.agent_hasn_id, tool_name=tool_name,
            required_scopes=list(tool.get('required_scopes') or []),
        )
        if mode == MODE_DENY:
            return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                    capability=capability, tool=tool, code='15012', reason='agent_scope_missing')
        if mode == MODE_ASK:
            # 与 MCP 面一致：owner 设 ask 的能力每次挂起等批准；批准放行，拒绝/超时→15013 deny。
            from backend.app.mcp.ask_gate import ask_approval_gate
            try:
                await ask_approval_gate.gate(
                    agent_hasn_id=agent.agent_hasn_id, owner_hasn_id=agent.owner_hasn_id,
                    tool_name=tool_name, arguments=input_payload,
                )
            except PermissionError:
                return await self._deny(db, body=body, workspace=workspace, agent=agent, manifest=manifest,
                                        capability=capability, tool=tool, code='15013',
                                        reason='agent_capability_ask_denied')

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

        registry: dict[str, Any] = {
            # 知识库（knowledge:read）
            'knowledge.search': self._handle_knowledge_search,
            # 帖子/文章详情走专用资源取数（含可见性鉴权 + reference_cards）
            'community.get_post': self._handle_community_get_post,
            'community.get_article': self._handle_community_get_article,
            # 读取（community:read）
            'community.get_feed': handlers.handle_community_get_feed,
            'community.get_comments': handlers.handle_community_get_comments,
            'community.search': handlers.handle_community_search,
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
        }
        self._internal_handlers_cache = registry
        return registry

    async def _handle_knowledge_search(
        self, db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await workbench_domain_service.search_current_knowledge(
            db,
            user_id=agent.owner_user_id,
            query=str(input_payload['query']),
            limit=int(input_payload.get('limit') or 50),
            dataset_id=input_payload.get('dataset_id'),
        )

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

    async def list_audit(self, db: AsyncSession, *, query: AiNativeAuditQuery) -> dict[str, Any]:
        stmt = sa.select(HasnAiNativeAppAudit)
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
        rows = (await db.execute(stmt.order_by(HasnAiNativeAppAudit.id.desc()))).scalars().all()
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

    async def _is_workspace_app_available(self, db: AsyncSession, *, workspace: dict[str, Any], app_id: str) -> bool:
        """published 即可用：默认无 workspace_app 记录即可用；仅企业 override 记录 status=disabled 时不可用。

        hasn_workspace_app 从"启用开关（必须有记录才可用）"降级为"企业可选 override（默认零记录即可用）"
        （设计 11 §4.2）。个人空间永远用公共实例、published 即用。
        """
        row = await self._get_workspace_app(db, workspace=workspace, app_id=app_id)
        return row is None or row.status != 'disabled'

    async def _get_workspace_app(
        self, db: AsyncSession, *, workspace: dict[str, Any], app_id: str
    ) -> HasnWorkspaceApp | None:
        stmt = sa.select(HasnWorkspaceApp).where(
            HasnWorkspaceApp.workspace_kind == workspace['kind'],
            HasnWorkspaceApp.app_id == app_id,
        )
        if workspace['kind'] == 'personal':
            stmt = stmt.where(HasnWorkspaceApp.user_id == workspace.get('user_id'))
        else:
            stmt = stmt.where(HasnWorkspaceApp.enterprise_id == workspace.get('enterprise_id'))
        return (await db.execute(stmt)).scalars().first()

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

    def _valid_search_input(self, data: dict[str, Any]) -> bool:
        query = data.get('query')
        if not isinstance(query, str) or not query.strip():
            return False
        if 'limit' in data and data['limit'] is not None:
            try:
                limit = int(data['limit'])
            except (TypeError, ValueError):
                return False
            if limit < 1 or limit > 50:
                return False
        dataset_id = data.get('dataset_id')
        return dataset_id is None or isinstance(dataset_id, str)

    def _valid_tool_input(self, tool_id: str, data: dict[str, Any]) -> bool:
        if tool_id == 'knowledge.search':
            return self._valid_search_input(data)
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
            agent_scopes_snapshot=list(agent.scopes) if agent is not None else [],
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
