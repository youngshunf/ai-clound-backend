"""MCP unified tool system error codes (P2).

Mirrors the Rust ``hasn-mcp::error::McpErrorCode`` so cross-layer responses
preserve a stable ``MCP_92xx`` code. Fact source: ``05-数据缓存与错误码.md §3``.
"""
from __future__ import annotations

from enum import Enum


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

    def __str__(self) -> str:
        return f"{self.value} {self.name}"


class McpToolError(ValueError):
    """MCP tool error carrying a stable MCP_92xx code.

    Subclasses ``ValueError`` so the MCP routes keep mapping "not found"-class
    failures to HTTP 404 while the detail preserves the code + symbolic name.
    """

    def __init__(self, code: McpErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
