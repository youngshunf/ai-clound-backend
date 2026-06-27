"""ExternalMcpTool —— 第三方 MCP 工具在云端 MCP 注册表里的一等表示（P7）。

每个 (binding 命中的工具) 投影成一个 source='external' 的 BaseTool；execute 委托给
ExternalMcpGateway.proxy_call（按 runtime agent_context 做 binding/health/secret/quota 全校验）。

发现可见性由 server.py 注入 agent_context.external_allowed_tools 集合控制（gate1+gate2），
**不**靠工具实例携带 agent 状态（实例全局共享、execute 时按 agent_context 现解析，避免串号）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.external_mcp.service.gateway_service import external_mcp_gateway
from backend.app.mcp.tools.base import BaseTool

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext


class ExternalMcpTool(BaseTool):
    """第三方 MCP 工具（hasn.ext.{ns}.{tool}，execution_location=cloud）。"""

    source = 'external'  # type: ignore[assignment]
    execution_location = 'cloud'

    def __init__(
        self,
        *,
        name: str,
        summary: str,
        input_schema: dict[str, Any],
        required_scopes: list[str],
        risk_level: str,
        mcp_id: str,
    ) -> None:
        self._name = name
        self._summary = summary or name
        self._input_schema = input_schema or {'type': 'object'}
        self._required_scopes = required_scopes or [f'mcp:tool://{name}']
        self.risk_level = risk_level or 'medium'
        self.mcp_id = mcp_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._summary

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def required_scopes(self) -> list[str]:
        return self._required_scopes

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        trace_id = None
        metadata = getattr(agent_context, 'metadata', None)
        if isinstance(metadata, dict):
            trace_id = metadata.get('trace_id')
        return await external_mcp_gateway.proxy_call(
            agent_hasn_id=agent_context.agent_hasn_id,
            owner_hasn_id=agent_context.owner_hasn_id or '',
            tool_name=self._name,
            arguments=arguments or {},
            trace_id=trace_id,
        )


async def load_external_mcp_tools_for_agent(agent_context: AgentContext) -> list[ExternalMcpTool]:
    """解析该 Agent 的 external binding，构造 ExternalMcpTool 列表（供注册 + 发现门控）。"""
    metas = await external_mcp_gateway.resolve_agent_external_tools(
        agent_hasn_id=agent_context.agent_hasn_id,
        owner_hasn_id=agent_context.owner_hasn_id,
    )
    return [
        ExternalMcpTool(
            name=meta['name'],
            summary=meta['summary'],
            input_schema=meta['input_schema'],
            required_scopes=meta['required_scopes'],
            risk_level=meta['risk_level'],
            mcp_id=meta['mcp_id'],
        )
        for meta in metas
    ]
