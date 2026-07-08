"""
AgentContext 上下文传递

使用 contextvars 在异步上下文中传递 AgentContext
"""
from contextvars import ContextVar

from backend.app.mcp.auth import AgentContext

# 使用 contextvars 在异步上下文中传递 AgentContext
_agent_context_var: ContextVar[AgentContext | None] = ContextVar(
    'agent_context',
    default=None
)


def set_current_agent_context(context: AgentContext) -> None:
    """设置当前 Agent 上下文"""
    _agent_context_var.set(context)


def get_current_agent_context() -> AgentContext:
    """获取当前 Agent 上下文"""
    context = _agent_context_var.get()
    if context is None:
        raise RuntimeError("Agent context not found in current async context")
    return context


def clear_agent_context() -> None:
    """清除当前 Agent 上下文"""
    _agent_context_var.set(None)


# 一次性能力票据（X-Capability-Ticket，A-P2 验票跳闸）：由传输层从 header 提取后落入，
# call_tool 的 ask 分支据此验票跳过审批闸门直接执行。
_capability_ticket_var: ContextVar[str | None] = ContextVar('capability_ticket', default=None)


def set_capability_ticket(ticket: str | None) -> None:
    """设置当前请求携带的一次性能力票据（无则传 None）。"""
    _capability_ticket_var.set(ticket or None)


def get_capability_ticket() -> str | None:
    """取当前请求的一次性能力票据（无则 None）。"""
    return _capability_ticket_var.get()


# 会话信任语境 header（L3 工具门云端半场·doc08 §4·RT3）：由传输层从 daemon 组装的 per-dispatch
# HTTP header（X-Hasn-Is-External / X-Hasn-Peer-Id / X-Hasn-Peer-Trust）提取**原始字符串**后落入，
# call_tool 的 L3 门据此判档（header 优先、工具入参 reserved-arg 兜底）。存原始三元组（未解析）；
# None = 本次请求**无**信任语境 header → 回落工具入参保留参数。分身不可伪造（daemon 组装、mcp.json
# 在 0700 临时目录，LLM 触达不到 header）。三元组：(is_external_raw, peer_id_raw, peer_trust_raw)。
_trust_context_header_var: ContextVar[tuple[str | None, str | None, str | None] | None] = ContextVar(
    'trust_context_header', default=None
)


def set_trust_context_header(raw: tuple[str | None, str | None, str | None] | None) -> None:
    """设置当前请求携带的会话信任语境 header 原始三元组（无 header 则传 None）。"""
    _trust_context_header_var.set(raw)


def get_trust_context_header() -> tuple[str | None, str | None, str | None] | None:
    """取当前请求的会话信任语境 header 原始三元组（无则 None → 回落工具入参保留参数）。"""
    return _trust_context_header_var.get()
