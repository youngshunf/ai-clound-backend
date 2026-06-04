"""
AgentContext 上下文传递

使用 contextvars 在异步上下文中传递 AgentContext
"""
from contextvars import ContextVar
from typing import Optional

from backend.app.mcp.auth import AgentContext

# 使用 contextvars 在异步上下文中传递 AgentContext
_agent_context_var: ContextVar[Optional[AgentContext]] = ContextVar(
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
_capability_ticket_var: ContextVar[Optional[str]] = ContextVar('capability_ticket', default=None)


def set_capability_ticket(ticket: Optional[str]) -> None:
    """设置当前请求携带的一次性能力票据（无则传 None）。"""
    _capability_ticket_var.set(ticket or None)


def get_capability_ticket() -> Optional[str]:
    """取当前请求的一次性能力票据（无则 None）。"""
    return _capability_ticket_var.get()
