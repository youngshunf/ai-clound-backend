"""
AgentContext 上下文传递

使用 contextvars 在异步上下文中传递 AgentContext
"""
from contextvars import ContextVar

from backend.app.hasn.service.authz import AuthorizedResource
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


# 工作会话 id 注入通道（doc31 register-on-write·产物自动登记铁律，照抄上方能力票据先例）：
# 系统注入的 `_hasn_session_id`（分身不可伪造，daemon/Hermes 出站打在入参）由两个分发入口剥离后落入：
# - MCP 直连面 `server.py::call_tool` —— 剥离后同时落 `AgentContext.work_session_id` 与本通道；
# - daemon 代理面 `ai_native_runtime_gateway.call_tool` —— 该面 handler 只收 `AgentTokenPayload`
#   （拿不到 AgentContext），故必须经本通道取。
# AI-Native 应用 handler（`app/<app>/service/tool_handlers.py`）经 `get_current_work_session_id()`
# 取本次工作会话 id，登记产物时带上 → 产物才挂得进「工作会话资源栏」。None = 主会话直调（非工作
# 会话），产物仍凭 resource_uri 进分身产物 tab。分发入口 finally 清，防跨调用串味。
_work_session_id_var: ContextVar[str | None] = ContextVar('work_session_id', default=None)


def set_current_work_session_id(session_id: str | None) -> None:
    """落入本次调用的工作会话 id（无则传 None）。"""
    _work_session_id_var.set(session_id or None)


def get_current_work_session_id() -> str | None:
    """取本次调用的工作会话 id；无（主会话直调 / 未注入）则 None。"""
    return _work_session_id_var.get()


def clear_current_work_session_id() -> None:
    """清本次调用的工作会话 id（分发入口 finally 调，防跨请求串味）。"""
    _work_session_id_var.set(None)


# G6 已判权资源注入通道（doc32 §5.5 / doc33 S2-3，照抄上方能力票据先例）：两个分发入口
# （MCP 直连面 server.py::call_tool、daemon 代理面 gateway.call_tool）在 enforce_declaration
# 判过后 set、调用工具体前生效、finally 清；handler 侧经 `get_authorized_resource(param)` 取
# 已判权资源的 owner key，**不再拿调用者身份当资源 owner key**。key = 声明的 `param`（工具入参名）。
_authorized_resources_var: ContextVar[dict[str, AuthorizedResource] | None] = ContextVar(
    'authorized_resources', default=None
)


def set_authorized_resources(authorized: dict[str, AuthorizedResource]) -> None:
    """落入本次调用 G6 判过的 `{param → AuthorizedResource}`（无声明工具不必调用）。"""
    _authorized_resources_var.set(authorized)


def get_authorized_resource(param: str) -> AuthorizedResource | None:
    """取本次调用中某入参对应的已判权资源；无（未判 / 该参未声明）则 None。"""
    authorized = _authorized_resources_var.get()
    if authorized is None:
        return None
    return authorized.get(param)


def clear_authorized_resources() -> None:
    """清本次调用的已判权资源（分发入口 finally 调，防跨请求串味）。"""
    _authorized_resources_var.set(None)
