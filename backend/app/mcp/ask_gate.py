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

import hashlib
import json
import logging
import uuid

from datetime import timedelta
from typing import Any

from backend.app.mcp.errors import McpErrorCode
from backend.app.mcp.scopes import scope_meta
from backend.common.security.scope_policy import MODE_ASK, resolve_capability_mode
from backend.utils.timezone import timezone

logger = logging.getLogger(__name__)

# 审批超时（A6）：默认 10 分钟，云端只用于落 expires_time；真正的等待/超时在 daemon。
APPROVAL_TTL_SECONDS = 600

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


def _describe(tool_name: str, required_scopes: list[str], digest: dict[str, Any]) -> str:
    """NLG 审批描述：能力中文 label（scopes.py）+ 关键参数摘要。"""
    labels = [scope_meta(scope)['label'] for scope in (required_scopes or [])]
    capability = '、'.join(dict.fromkeys(labels)) if labels else tool_name
    arg_preview = '；'.join(f'{k}={v}' for k, v in list(digest.items())[:3])
    description = f'请求执行【{capability}】' + (f'：{arg_preview}' if arg_preview else '')
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
        description = _describe(tool_name, required_scopes, args_digest)
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
