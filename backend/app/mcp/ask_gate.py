"""ask 态批准闸门（D4，维度①）——令牌重试模型（doc15 §3 / A-P1）。

设计事实源：13-doc §3.1 / 93-doc §P6 / 15-doc《工具调用审批链路》§3。
- 默认 **allow 无确认**；只有 owner 把某能力设为 `ask` 时，该能力**每次调用**才需主人批准。
- **不按 risk 自动强制**：`risk_level` 仅 catalog/UI 提示，与是否挂起无关。

**令牌重试（H10）**：云端**不长挂**。判 `ask` 且无有效票 → `open_request()` 落一条
`hasn_agent_approval_requests`(status=pending) + 审计 + 返回 `approval_required` 信封
（作为工具结果透传，跨 streamable/JSON 两条传输面都不丢）。挂起态在 daemon 本地
（ApprovalBroker 发卡片 + 等待），主人批准后向云端换一次性票据，hasn-mcp 带票重试
（验票跳闸见 server.py，A-P2）。零 fake：无决定即超时拒绝，绝不默认放行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid

from datetime import timedelta
from typing import Any

from backend.app.mcp.errors import McpErrorCode
from backend.app.mcp.scopes import scope_meta
from backend.common.security.scope_policy import MODE_ASK, resolve_capability_mode
from backend.utils.timezone import timezone

logger = logging.getLogger(__name__)

# 审批超时（A6）：默认 10 分钟。
APPROVAL_TTL_SECONDS = 600

# 「云端挂起(cloud-pend)」窗口（福仔 2026-06-08 拍板，doc15 A1 修订）：分身经 cloud 直连面
# 调云端 MCP 工具时**不经 daemon 中转**，故 daemon 令牌重试拦不到 ask——云端必须自己挂起：
# 命中 ask → 发审批卡片给主人 + 在本请求内阻塞轮询裁决，对 agent 透明（agent 只等工具返回）。
# 持有窗口受多层超时约束（须同步调高，否则 agent 提前拿到错误）：
#   - Hermes MCP 客户端 per-call timeout（默认 120s，profile_config 调高到 ≥ 本窗口）；
#   - 云端进程超时（gunicorn worker timeout）/反代 proxy_read_timeout / SSE sse_read_timeout
#     部署侧须 ≥ 本窗口（见 实施/96）。可经 MCP_ASK_CLOUD_WAIT_SECONDS 调整。
CLOUD_WAIT_SECONDS = max(5, int(os.getenv('MCP_ASK_CLOUD_WAIT_SECONDS', str(APPROVAL_TTL_SECONDS))))
# 轮询裁决间隔（秒）：每轮开短会话查 status，不长持事务。
_POLL_INTERVAL_SECONDS = 1.5

# 入参摘要脱敏：键名命中任一即打码，绝不把敏感原文写进 args_digest / description。
_SENSITIVE_HINTS = ('token', 'secret', 'password', 'passwd', 'apikey', 'api_key', 'authorization', 'cookie', 'jwt')

# 单个参数值在摘要里的最大展示长度。
_VALUE_PREVIEW_LIMIT = 80
# description 列宽 varchar(500)，留余量截断。
_DESCRIPTION_LIMIT = 480


def _canonical_args_hash(arguments: dict[str, Any]) -> str:
    """入参 canonical JSON（key 排序、紧凑、保 UTF-8）的 sha256。

    票据据此绑定本次调用，daemon 换票 / hasn-mcp 带票重试必须对同一 arguments
    复算出同一 hash，否则验票失败——故 server 验票侧（A-P2）复用本函数。
    """
    canonical = json.dumps(arguments or {}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _redact_digest(arguments: dict[str, Any]) -> dict[str, Any]:
    """把入参收敛成脱敏摘要（卡片展示 / 审计用，不存敏感原文）。"""
    digest: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        key_lower = str(key).lower()
        if any(hint in key_lower for hint in _SENSITIVE_HINTS):
            digest[key] = '***'
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            digest[key] = value
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        digest[key] = text if len(text) <= _VALUE_PREVIEW_LIMIT else f'{text[:_VALUE_PREVIEW_LIMIT - 3]}...'
    return digest


def _ask_capability_keys(
    tool_name: str, required_scopes: list[str], default_mode: str, capability_modes: dict | None
) -> list[str]:
    """聚合中**实际触发 ask** 的能力 key（工具名 + 各 required_scope，去重保序）。

    "总是允许"据此把这些 key 写回 `capability_modes[key]=allow`（A-P2/§3.4）。
    """
    keys: list[str] = [tool_name, *(required_scopes or [])]
    ask_keys = [k for k in dict.fromkeys(keys) if resolve_capability_mode(default_mode, capability_modes, k) == MODE_ASK]
    return ask_keys or [tool_name]


def _capability_text(required_scopes: list[str]) -> tuple[str, str]:
    """(权限中文名, 能力说明) —— 都取自 scopes.py 展示元数据。

    面向主人的友好展示：**只讲“要什么权限、能做什么”，绝不写工具名（如
    hasn.community.create_post），也不暴露调用参数**（主人无需关心这些技术细节）。
    """
    metas = [scope_meta(scope) for scope in (required_scopes or [])]
    labels = list(dict.fromkeys(m['label'] for m in metas if m.get('label')))
    descs = list(dict.fromkeys(m['description'] for m in metas if m.get('description')))
    label = '、'.join(labels) if labels else '一项操作'
    return label, '；'.join(descs)


def _describe(tool_name: str, required_scopes: list[str]) -> str:
    """审批描述（落库 purpose / 挂起列表展示）：能力中文名 + 说明，**不写工具名、不带参数**。"""
    label, desc = _capability_text(required_scopes)
    description = f'请求{label}' + (f'：{desc}' if desc else '')
    return description[:_DESCRIPTION_LIMIT]


class AskApprovalGate:
    """ask 态闸门：开请求（落库 + 信封），不在云端阻塞。"""

    async def open_request(
        self,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str | None,
        tool_name: str,
        required_scopes: list[str],
        default_mode: str,
        capability_modes: dict | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """开一条审批请求 → 落 pending 行 + 审计 → 返回 `approval_required` 信封。

        信封作为**工具结果**回传（不 raise），hasn-mcp 据 `error=='approval_required'`
        驱动 daemon ApprovalBroker。请求体从未执行（在 dispatch 之前返回）。
        """
        request_id = f'areq_{uuid.uuid4().hex}'
        args_hash = _canonical_args_hash(arguments)
        args_digest = _redact_digest(arguments)
        capability_keys = _ask_capability_keys(tool_name, required_scopes, default_mode, capability_modes)
        description = _describe(tool_name, required_scopes)
        expires_time = timezone.now() + timedelta(seconds=APPROVAL_TTL_SECONDS)

        await self._persist_pending(
            request_id=request_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id or '',
            tool_name=tool_name,
            args_hash=args_hash,
            args_digest=args_digest,
            capability_keys=capability_keys,
            description=description,
            expires_time=expires_time,
        )
        await self._audit(
            agent_hasn_id,
            'mcp_ask_pending',
            request_id,
            tool_name,
            {'capability_keys': capability_keys, 'args_digest': args_digest},
        )

        return {
            'ok': False,
            'error': 'approval_required',
            'code': McpErrorCode.APPROVAL_REQUIRED.value,
            'message': f'需要主人批准后才能执行：{description}',
            'approval': {
                'request_id': request_id,
                'tool_name': tool_name,
                'description': description,
                'args_digest': args_digest,
                'expires_in': APPROVAL_TTL_SECONDS,
            },
        }

    async def request_and_wait(
        self,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str | None,
        tool_name: str,
        required_scopes: list[str],
        default_mode: str,
        capability_modes: dict | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """云端挂起模型（cloud-pend）：开请求 → 发审批卡片到 Agent↔主人会话 → 阻塞轮询裁决。

        返回 `{'decision': approved|denied|timeout, 'request_id', 'description'}`，对 agent 透明：
        - approved → 调用方落 dispatch 真执行（工具体只在批准后运行）；
        - denied/timeout → 调用方回**工具错误**（绝不当 approval_required 透传给 agent）。
        零 fake：无主人 / 发不出卡片（无审批出口）→ 直接 denied，绝不默认放行。
        """
        if not owner_hasn_id:
            logger.warning('ask gate: missing owner_hasn_id, deny (no approval channel) tool=%s', tool_name)
            return {'decision': 'denied', 'request_id': '', 'description': ''}

        request_id = f'areq_{uuid.uuid4().hex}'
        args_hash = _canonical_args_hash(arguments)
        args_digest = _redact_digest(arguments)
        capability_keys = _ask_capability_keys(tool_name, required_scopes, default_mode, capability_modes)
        description = _describe(tool_name, required_scopes)
        deadline = timezone.now() + timedelta(seconds=CLOUD_WAIT_SECONDS)

        await self._persist_pending(
            request_id=request_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            tool_name=tool_name,
            args_hash=args_hash,
            args_digest=args_digest,
            capability_keys=capability_keys,
            description=description,
            expires_time=deadline,
        )
        await self._audit(
            agent_hasn_id,
            'mcp_ask_pending',
            request_id,
            tool_name,
            {'capability_keys': capability_keys, 'args_digest': args_digest},
        )

        card_sent = await self._send_approval_card(
            request_id=request_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            required_scopes=required_scopes,
            description=description,
            capability_keys=capability_keys,
        )
        if not card_sent:
            # 零 fake：发不出卡片 = 没有审批出口，不能假装在等。标 timeout 并拒绝。
            await self._mark_timeout(request_id)
            return {'decision': 'denied', 'request_id': request_id, 'description': description}

        decision = await self._await_decision(request_id, deadline)
        return {'decision': decision, 'request_id': request_id, 'description': description}

    async def _send_approval_card(
        self,
        *,
        request_id: str,
        agent_hasn_id: str,
        owner_hasn_id: str,
        required_scopes: list[str],
        description: str,
        capability_keys: list[str],
    ) -> bool:
        """把 A 类审批卡片（`agent_tool_approval`，doc15 §7.1）发到 Agent↔主人 1:1 会话。

        镜像 daemon `card.rs::build_agent_tool_approval_card` 的 schema（三按钮 + authorization_request
        + 每个授权 action 的 payload.request_id 一致），以便主人点按钮时走既有 daemon
        `emit_card_action` → 云端 grant/deny 解析路径。经 `route_message` 投递（含跨设备 sync_event +
        WS 推送）。失败返回 False（调用方零 fake 处理）。

        面向主人友好展示：卡片只讲“谁、想要什么权限、能做什么”，**不写工具名、不暴露参数**。
        """
        from backend.app.hasn.service import message_router
        from backend.database.db import async_db_session

        display = await self._agent_display(agent_hasn_id)
        label, capability_desc = _capability_text(required_scopes)
        # 「请求能力」字段：权限中文名 +（能力说明），不含工具名/参数。
        ability_value = f'{label}（{capability_desc}）' if capability_desc else label
        card = {
            'schema_version': 'hasn.card/0.1',
            'title': f'「{display}」想请你确认一项操作',
            'description': description,
            'source': {'kind': 'agent', 'id': agent_hasn_id, 'display_name': display},
            'resource': {
                'type': 'agent_tool_approval',
                'id': request_id,
                'uri': f'hasn://agents/{agent_hasn_id}/approvals/{request_id}',
                'status': 'pending',
            },
            'fields': [
                {'label': '请求能力', 'value': ability_value},
            ],
            'authorization_request': {
                'request_id': request_id,
                'requester': {'agent_hasn_id': agent_hasn_id},
                'purpose': description,
                'requested_scopes': capability_keys,
                'grant': {'mode': 'one_time'},
            },
            'primary_action': {
                'label': '本次允许',
                'action_id': 'grant_once',
                'kind': 'grant_authorization',
                'style': 'primary',
                'event': {'event_type': 'agent_tool_approval.granted', 'payload': {'request_id': request_id, 'scope': 'once'}},
            },
            'actions': [
                {
                    'label': '总是允许',
                    'action_id': 'grant_always',
                    'kind': 'grant_authorization',
                    'style': 'default',
                    'event': {'event_type': 'agent_tool_approval.granted', 'payload': {'request_id': request_id, 'scope': 'always'}},
                },
                {
                    'label': '拒绝',
                    'action_id': 'deny',
                    'kind': 'deny_authorization',
                    'style': 'danger',
                    'event': {'event_type': 'agent_tool_approval.denied', 'payload': {'request_id': request_id}},
                },
            ],
        }
        try:
            # route_message 自带 commit（见 message_router.py），**不能**再包 `.begin()`——否则
            # 退出时二次 commit 抛异常 → 误判发卡失败 → 工具被即时拒绝（这正是「设置每次询问却
            # 0.x 秒就被拒」的根因）。与规范调用面 `mcp/tools/message.py` 一致用裸工厂会话。
            async with async_db_session() as db:
                result = await message_router.route_message(
                    db,
                    from_id=agent_hasn_id,
                    to_target=owner_hasn_id,
                    content=card,
                    content_type=5,
                    msg_type='message',
                )
            if isinstance(result, dict) and result.get('error'):
                logger.error('ask gate: failed to deliver approval card %s: %s', request_id, result)
                return False
            return True
        except Exception:
            logger.exception('ask gate: exception delivering approval card %s', request_id)
            return False

    async def _await_decision(self, request_id: str, deadline: Any) -> str:
        """阻塞轮询 `hasn_agent_approval_requests.status` 直到裁决或超时。

        主人点卡片 → daemon → 云端 grant(approved)/deny(denied) 改状态；本方法据状态返回。
        到 deadline 仍 pending → 标 timeout 返回。每轮短会话查询，不长持事务。
        """
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.database.db import async_db_session

        while timezone.now() < deadline:
            async with async_db_session() as db:
                row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
            if row is None:
                return 'denied'
            if row.status in ('approved', 'consumed'):
                return 'approved'
            if row.status == 'denied':
                return 'denied'
            if row.status == 'timeout':
                return 'timeout'
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        await self._mark_timeout(request_id)
        return 'timeout'

    async def _mark_timeout(self, request_id: str) -> None:
        """到窗口仍未裁决 → 标 timeout（仅 pending 行生效，幂等）。"""
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.database.db import async_db_session

        try:
            async with async_db_session.begin() as db:
                row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
                if row is not None and row.status == 'pending':
                    await hasn_agent_approval_requests_dao.update_model(
                        db, row.id, {'status': 'timeout', 'decided_time': timezone.now()}
                    )
        except Exception:
            logger.exception('ask gate: failed to mark approval timeout %s', request_id)

    async def _agent_display(self, agent_hasn_id: str) -> str:
        """取分身展示名作卡片来源标签；失败回落 agent_hasn_id（零 fake，不编造）。"""
        try:
            from sqlalchemy import select

            from backend.app.hasn.model.hasn_agents import HasnAgents
            from backend.database.db import async_db_session

            async with async_db_session() as db:
                name = (
                    await db.execute(select(HasnAgents.display_name).where(HasnAgents.hasn_id == agent_hasn_id))
                ).scalar_one_or_none()
            return (name or '').strip() or agent_hasn_id
        except Exception:
            return agent_hasn_id

    async def _persist_pending(
        self,
        *,
        request_id: str,
        agent_hasn_id: str,
        owner_hasn_id: str,
        tool_name: str,
        args_hash: str,
        args_digest: dict[str, Any],
        capability_keys: list[str],
        description: str,
        expires_time: Any,
    ) -> None:
        """落一条 pending 审批请求行。失败必须上抛（零 fake：落库失败不能假装已发审批）。"""
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.app.hasn.schema.hasn_agent_approval_requests import CreateHasnAgentApprovalRequestsParam
        from backend.database.db import async_db_session

        param = CreateHasnAgentApprovalRequestsParam(
            request_id=request_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            tool_name=tool_name,
            args_hash=args_hash,
            args_digest=args_digest,
            capability_keys=capability_keys,
            description=description,
            status='pending',
            grant_scope=None,
            ticket_jti=None,
            decided_time=None,
            expires_time=expires_time,
        )
        async with async_db_session.begin() as db:
            await hasn_agent_approval_requests_dao.create(db, param)

    async def list_pending(self, agent_hasn_id: str) -> list[dict[str, Any]]:
        """列出某 Agent 当前挂起的 ask 请求（主人 UI / 审计排障，DB 权威）。"""
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.database.db import async_db_session

        async with async_db_session() as db:
            rows = await hasn_agent_approval_requests_dao.list_pending_by_agent(db, agent_hasn_id)
        return [
            {
                'request_id': row.request_id,
                'agent_hasn_id': row.agent_hasn_id,
                'tool_name': row.tool_name,
                'description': row.description,
                'args_digest': row.args_digest,
                'owner_hasn_id': row.owner_hasn_id,
                'expires_time': row.expires_time.isoformat() if row.expires_time else None,
            }
            for row in rows
        ]

    async def mark_consumed(self, request_id: str) -> None:
        """验票跳闸成功后把审批请求标记为 consumed（best-effort，jti 才是防重放真相）。"""
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.database.db import async_db_session

        try:
            async with async_db_session.begin() as db:
                row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
                if row is not None and row.status in ('approved', 'pending'):
                    await hasn_agent_approval_requests_dao.update_model(db, row.id, {'status': 'consumed'})
        except Exception:
            logger.exception('Failed to mark approval request consumed: %s', request_id)

    async def submit_decision(self, request_id: str, decision: str) -> None:
        """主人对某挂起请求记一个决定（approve/reject）→ 更新 DB 行状态 + decided_time。

        兼容既有 owner 决定 API（`agent_scopes_service.decide_ask_request`）。注：新模型下
        **权威**决定走 daemon→云端换票端点（A-P2，签一次性票放行）；本方法只落状态、不签票，
        用于 owner 在挂起列表里直接拒绝/标记。仅对 pending 行生效（幂等）。
        """
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.database.db import async_db_session

        normalized = 'approved' if decision in ('approved', 'approve') else 'denied'
        async with async_db_session.begin() as db:
            row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
            if row is None or row.status != 'pending':
                return
            await hasn_agent_approval_requests_dao.update_model(
                db, row.id, {'status': normalized, 'decided_time': timezone.now()}
            )
        await self._audit(row.agent_hasn_id, 'mcp_ask_decision', request_id, row.tool_name, {'decision': normalized})

    async def _audit(
        self, agent_hasn_id: str, action: str, request_id: str, tool_name: str, extra: dict[str, Any]
    ) -> None:
        try:
            from backend.app.hasn.service.hasn_audit_log_service import HasnAuditLogService
            from backend.database.db import async_db_session

            async with async_db_session() as db:
                await HasnAuditLogService().append(
                    db=db,
                    actor_type='agent',
                    actor_id=agent_hasn_id,
                    action=action,
                    target_type='tool',
                    target_id=tool_name,
                    details={'request_id': request_id, 'tool_name': tool_name, **extra},
                )
        except Exception:
            logger.exception('Failed to audit ask-mode event %s', action)


ask_approval_gate = AskApprovalGate()
