"""
MCP 工具基类
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext


def require_owner_hasn_id(agent_context: AgentContext) -> str:
    """读取主人 HASN ID；身份上下文不完整时闭合失败。"""
    from backend.common.exception import errors

    owner_hasn_id = agent_context.owner_hasn_id
    if not owner_hasn_id:
        raise errors.TokenError(msg='AgentContext 缺少 owner_hasn_id')
    return owner_hasn_id


class BaseTool(ABC):
    """MCP 工具基类"""

    @property
    def source(self) -> str:
        """工具来源类别，默认平台工具。"""
        return "platform"

    @property
    def namespace(self) -> str:
        """工具命名空间，默认取 canonical 名称前两段。"""
        parts = self.name.split(".")
        if self.source == "external" and len(parts) >= 3:
            return ".".join(parts[:3])
        if len(parts) >= 2:
            return ".".join(parts[:2])
        return self.name

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称（使用点分隔命名空间）"""

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """输入参数 JSON Schema"""

    @property
    def required_scopes(self) -> list[str]:
        """所需权限范围"""
        return []

    @property
    def app_id(self) -> str | None:
        """所属应用 catalog app_id（G3 应用权益门·doc18/实施103 U3）。

        平台工具默认 None——其「所属应用」由 namespace 经 `tool_app_registry` 回填；
        AI-Native app 工具应覆盖此属性返回 manifest 声明的 app_id。None = 平台底座，跳过 G3。
        """
        return None

    @property
    def min_trust_level(self) -> int | None:
        """L3 工具门（doc08 §4·RT3·云端半场）：本工具在**对外会话**里要求的最低信任档。

        ``None``（默认）= 无对外门，任何会话都放行。声明后（看日程/看计划=好友3、位置/代预约=
        密友4），对外会话（peer 1:1 / A2A / 群）里由 ``server.call_tool`` 按对端**真实** trust
        判档，不足即结构化拒绝（``trust_gate.evaluate_min_trust_level``）。主会话/主人自环不受限。
        """
        return None

    @property
    def enterprise_capability(self) -> str | None:
        """企业能力族键（G4 企业角色门·doc18/实施103 U4）。

        默认 None = 不挂 G4（企业空间也放行）。声明后（如 `oa:approve`/`plan:manage`），
        企业空间下需主人在该企业的角色被授予此能力族才可见。⚠️ 依赖 doc12/02 角色→能力族
        策略表落地——策略表未落地前无工具声明此值、门恒 inert（见 evaluate G4 段注释）。
        """
        return None

    def descriptor(self) -> dict[str, Any]:
        """结构化描述符投影（P0），与 Rust ToolDescriptor 对齐。

        统一暴露 source/namespace/action/schema_hash/scopes/risk/visibility/
        execution_location，供 source 维度索引与后续阶段消费。execution_location
        为 P0 占位（local 来源→local，其余→cloud），P3 由工具显式声明覆盖。
        """
        from backend.app.mcp.canonical import ToolSource, schema_hash, validate_canonical_name
        from backend.app.mcp.tool_app_registry import resolve_tool_app_id

        parsed = validate_canonical_name(self.name, cast(ToolSource, self.source))
        output_schema = getattr(self, "output_schema", None)
        default_location = "local" if self.source == "local" else "cloud"
        return {
            "canonical_name": parsed.full,
            "source": self.source,
            "namespace": parsed.namespace,
            "action": parsed.action,
            "input_schema_hash": schema_hash(self.input_schema),
            "output_schema_hash": schema_hash(output_schema) if output_schema else None,
            "required_scopes": self.required_scopes,
            "app_id": resolve_tool_app_id(self),
            "risk_level": getattr(self, "risk_level", "low"),
            "schema_visibility": getattr(self, "schema_visibility", "public"),
            "execution_location": getattr(self, "execution_location", default_location),
        }

    @abstractmethod
    async def execute(
        self,
        agent_context: AgentContext,
        arguments: dict[str, Any],
    ) -> Any:
        """执行工具"""
