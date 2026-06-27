"""Compatibility AppTool surface for MCP tests and external extensions."""
from __future__ import annotations

import hashlib
import json

from typing import TYPE_CHECKING, Any

from backend.app.mcp.tools.base import BaseTool

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext


class AppTool(BaseTool):
    def __init__(
        self,
        installation_id: str,
        app_id: str,
        app_namespace: str,
        tool_id: str,
        tool_name: str,
        tool_description: str,
        tool_input_schema: dict[str, Any],
        tool_required_scopes: list[str],
        action: str | None = None,
        tool_output_schema: dict[str, Any] | None = None,
        risk_level: str = "low",
        execution_location: str = "cloud",
    ) -> None:
        self.installation_id = installation_id
        self.app_id = app_id
        self.app_namespace = app_namespace
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.action = action or tool_name
        self._description = tool_description
        self._input_schema = tool_input_schema
        self._output_schema = tool_output_schema or {"type": "object"}
        self._required_scopes = tool_required_scopes
        self._risk_level = risk_level
        # P3: registration-time placement (cloud unless it touches the machine).
        self._execution_location = execution_location

        # P0: validate the derived canonical name (rejects reserved-namespace
        # conflicts and malformed names) at construction time.
        from backend.app.mcp.canonical import validate_canonical_name

        validate_canonical_name(self.name, self.source)

    @property
    def source(self) -> str:
        return "app"

    @property
    def namespace(self) -> str:
        return f"hasn.{self.app_namespace}"

    @property
    def name(self) -> str:
        return f"hasn.{self.app_namespace}.{self.action}"

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def output_schema(self) -> dict[str, Any]:
        return self._output_schema

    @property
    def required_scopes(self) -> list[str]:
        return self._required_scopes

    @property
    def risk_level(self) -> str:
        return self._risk_level

    @property
    def execution_location(self) -> str:
        return self._execution_location

    async def execute(
        self,
        agent_context: AgentContext,
        arguments: dict[str, Any],
    ) -> Any:
        from backend.app.hasn_core.app_platform import AiNativeToolCallRequest, ai_native_runtime_gateway
        from backend.database.db import async_db_session

        # 经 MCP 直连面进入：身份与维度① 三态闸门已在 server.call_tool 完成
        # （能力票走传输层 ContextVar、由 server.py 消费），这里只承载 agent 供网关复用，
        # 并以 mcp_face=True 标记让网关跳过重复的三态闸门（否则一次性能力票会被二次消费、
        # 已批准的 ask 调用反被网关重新挂起审批）。headers 置空：MCP 面不经请求头传票，
        # 同时补齐属性避免网关 `request.headers.get(...)` 抛 AttributeError。
        class _Request:
            state = type(
                "_State",
                (),
                {"agent": agent_context.to_token_payload(), "mcp_face": True},
            )()
            headers: dict[str, str] = {}

        # 裸 session + 末尾显式 commit（对齐平台工具 asset.create 模式）：网关 dispatch 全程只
        # flush（业务行 + 审计行）、从不自己 commit——末尾 commit 落库（修非自 commit 的 App 写类
        # creator/knowledge/... 经 MCP 直连面返回成功却整体回滚不落库）。
        # 不用 async_db_session.begin()：community 等 handler 自身 db.commit() 会在 begin() 上下文里
        # 提前关闭事务，随后网关 flush 审计行撞「Can't operate on closed transaction」；裸 session
        # 允许 handler 多次 commit，末尾 commit 再提交审计行。异常时 __aexit__ 关闭 session 自动回滚。
        async with async_db_session() as db:
            result = await ai_native_runtime_gateway.call_tool(
                db,
                request=_Request(),
                app_id=self.app_id,
                tool_id=self.tool_id,
                body=AiNativeToolCallRequest(
                    agent_hasn_id=agent_context.hasn_id,
                    workspace={"kind": "personal"},
                    input=arguments,
                    trace_id=self._trace_id(agent_context, arguments),
                ),
            )
            await db.commit()
            return result

    def _trace_id(self, agent_context: AgentContext, arguments: dict[str, Any]) -> str:
        canonical_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
        raw = f"{agent_context.hasn_id}:{self.name}:{canonical_arguments}"
        return "trace_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
