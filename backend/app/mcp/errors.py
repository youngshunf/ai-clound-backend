"""MCP unified tool system error codes (P2).

Mirrors the Rust ``hasn-mcp::error::McpErrorCode`` so cross-layer responses
preserve a stable ``MCP_92xx`` code. Fact source: ``05-数据缓存与错误码.md §3``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class McpErrorCode(Enum):
    """Stable MCP_92xx error codes."""

    CONFIG_PLAINTEXT_CREDENTIAL = "MCP_9200"
    CONFIG_SCHEMA_INVALID = "MCP_9201"
    BINDING_ORPHANED = "MCP_9202"
    SERVER_NOT_INSTALLED = "MCP_9203"
    SERVER_UNHEALTHY = "MCP_9204"
    SERVER_CIRCUIT_BROKEN = "MCP_9205"
    TOOL_NOT_ALLOWED = "MCP_9206"
    CREDENTIAL_MISSING = "MCP_9207"
    TOOL_NAME_CONFLICT = "MCP_9208"
    TOOL_NOT_FOUND = "MCP_9209"
    SCHEMA_HASH_MISMATCH = "MCP_9210"
    TOOL_SCHEMA_NOT_VISIBLE = "MCP_9211"
    QUERY_TOO_BROAD = "MCP_9212"
    DIRECT_CALL_DENIED = "MCP_9213"
    LOCAL_EXECUTION_UNAVAILABLE = "MCP_9214"
    APPROVAL_REQUIRED = "MCP_9215"
    # P7 第三方 MCP 网关：平台 key（system-origin）配额超限。
    # 注：事实源 10 §7.2 以 "如 MCP_9214 QUOTA_EXCEEDED" 举例，但 9214 已被
    # LOCAL_EXECUTION_UNAVAILABLE 占用，故配额码取下一个空位 9216。
    QUOTA_EXCEEDED = "MCP_9216"
    # L3 工具门（doc08 §4·RT3·云端半场）：对外会话里对方信任档不足以调用该能力工具。
    # ⚠️ 云端**不复用** 9215（云端 9215 已是 APPROVAL_REQUIRED——若复用会被 hasn-node
    # ai_native `is_approval_required` 误判成待审批、错误触发换票流程）。故取下一个空位 9217，
    # 符号名与本地 hasn-mcp `TrustLevelInsufficient`（本地占 9215）对齐，结构化拒绝 shape 一致。
    TRUST_LEVEL_INSUFFICIENT = "MCP_9217"
    # G6 统一资源权限门（doc32 §5.3 评审拍板新增）：分身对某资源实例的有效档位低于工具所需档。
    # 载荷 shape 照抄 TRUST_LEVEL_INSUFFICIENT（文案含当前档 + 所需档 + 回绝引导，分身据此礼貌
    # 说明权限不足）。取下一个空位 9218；与 9217 一样**不复用** 9215，避免被误判成待审批。
    RESOURCE_PERMISSION_INSUFFICIENT = "MCP_9218"
    # P7 工作流场景模板付费墙（doc94 §10-P7 / doc11 §8.3）：付费模板（sku_ref 非空）无有效权益。
    # `data` 携完整 AccessDecision（reason/offer/feature_key），供 daemon→webui PaywallDialog 渲染。
    # 取下一个空位 9219；与 9217/9218 一样**不复用** 9215，避免被误判成待审批换票流程。
    WORKFLOW_TEMPLATE_ENTITLEMENT_REQUIRED = "MCP_9219"
    # P9-B 场景工作流项目轴实例化硬闸（doc11 §6.1 / doc95 §2.1）：场景实例化未解析出所属平台项目。
    # 三级取值优先级（显式入参 ＞ ContextVar 继承 ＞ 全局兜底）全落空后结构化拒绝——分身据此回头
    # 问主人「这个场景挂到哪个项目下」，而不是随便挑一个项目。取下一个空位 9220。
    PROJECT_REQUIRED = "MCP_9220"
    # tool.call 转发层的**入参**不合法（doc03 §9.4 之外的一类）：目标工具名缺失/不像工具名、
    # `params` 不是对象也不是合法 JSON 对象串。此前这三种都被塞进 TOOL_NOT_FOUND(9209)，
    # 于是「我的 JSON 写坏了」在分身眼里长成「这个工具不存在」——它只会去换工具名重试，
    # 而真正该做的是把入参改小/改对。取下一个空位 9221。
    # ⚠️ 本码**只存在于云端**：本文件开头那句「Mirrors the Rust hasn-mcp::error::McpErrorCode」
    # 对 9215 及以后**已经不成立**（云端 9215=APPROVAL_REQUIRED / 9216=QUOTA_EXCEEDED /
    # 9217=TRUST_LEVEL_INSUFFICIENT，Rust 侧 9215=TrustLevelInsufficient / 9216、9217=G3 应用
    # 权益门，语义早已分叉且各自注释都记了原因）。新增码不要照着那句去 Rust 侧补一个同号的。
    INVALID_CALL_ARGUMENTS = "MCP_9221"

    def __str__(self) -> str:
        return f"{self.value} {self.name}"


class McpToolError(ValueError):
    """MCP tool error carrying a stable MCP_92xx code.

    Subclasses ``ValueError`` so the MCP routes keep mapping "not found"-class
    failures to HTTP 404 while the detail preserves the code + symbolic name.

    可选 ``data``：结构化拒绝载荷（如付费墙 AccessDecision），随异常携带，供上层网关/
    daemon 解析成客户端能渲染的形状（webui PaywallDialog 等）。不传即为纯文本错误（存量语义不变）。
    """

    def __init__(self, code: McpErrorCode, message: str, data: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"{code}: {message}")
