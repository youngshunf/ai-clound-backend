"""Canonical tool-name validation + source classification tests (P0).

Mirrors the Rust ``hasn-mcp::descriptor`` unit tests so both servers enforce
identical naming rules.
"""
from __future__ import annotations

import pytest

from typing import Any

from backend.app.mcp.canonical import CanonicalNameError, validate_canonical_name
from backend.app.mcp.tool_directory import ToolDirectoryService
from backend.app.mcp.tools.app_tools import AppTool
from backend.app.mcp.tools.registry import ToolRegistry
from backend.app.mcp.tools.tool_search import ToolSearchTool


class TestCanonicalValidation:
    def test_parses_platform_name(self) -> None:
        parsed = validate_canonical_name("hasn.tool.search", "platform")
        assert parsed.namespace == "hasn.tool"
        assert parsed.action == "search"

    def test_parses_app_multi_segment_action(self) -> None:
        parsed = validate_canonical_name("hasn.crm.lead.create", "app")
        assert parsed.namespace == "hasn.crm"
        assert parsed.action == "lead.create"

    def test_parses_external_name(self) -> None:
        parsed = validate_canonical_name("hasn.ext.gmail.draft_email", "external")
        assert parsed.namespace == "hasn.ext.gmail"
        assert parsed.action == "draft_email"

    def test_rejects_app_in_reserved_namespace(self) -> None:
        with pytest.raises(CanonicalNameError):
            validate_canonical_name("hasn.memory.search", "app")

    def test_allows_local_in_reserved_namespace(self) -> None:
        parsed = validate_canonical_name("hasn.memory.search", "local")
        assert parsed.namespace == "hasn.memory"

    def test_rejects_ext_marker_for_non_external(self) -> None:
        with pytest.raises(CanonicalNameError):
            validate_canonical_name("hasn.ext.gmail.draft_email", "app")

    def test_rejects_non_hasn_prefix(self) -> None:
        with pytest.raises(CanonicalNameError):
            validate_canonical_name("workbench.crm.lead.create", "app")

    def test_rejects_too_short(self) -> None:
        with pytest.raises(CanonicalNameError):
            validate_canonical_name("hasn.search", "platform")

    def test_rejects_empty_and_malformed_segments(self) -> None:
        with pytest.raises(CanonicalNameError):
            validate_canonical_name("hasn..search", "platform")
        with pytest.raises(CanonicalNameError):
            validate_canonical_name("hasn.tool.sea rch", "platform")

    def test_normalizes_surrounding_whitespace(self) -> None:
        parsed = validate_canonical_name("  hasn.tool.search  ", "platform")
        assert parsed.full == "hasn.tool.search"


class TestSourceClassificationAndDescriptor:
    def test_app_tool_source_and_descriptor(self) -> None:
        tool = AppTool(
            installation_id="appi_knowledge",
            app_id="knowledge",
            app_namespace="knowledge",
            tool_id="knowledge.search",
            tool_name="search",
            action="search",
            tool_description="Search workspace knowledge",
            tool_input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            tool_output_schema={"type": "object"},
            tool_required_scopes=["knowledge.read"],
            risk_level="low",
        )
        assert tool.source == "app"

        descriptor = tool.descriptor()
        assert descriptor["canonical_name"] == "hasn.knowledge.search"
        assert descriptor["source"] == "app"
        assert descriptor["namespace"] == "hasn.knowledge"
        assert descriptor["action"] == "search"
        assert descriptor["required_scopes"] == ["knowledge.read"]
        assert descriptor["risk_level"] == "low"
        assert descriptor["execution_location"] == "cloud"
        assert descriptor["input_schema_hash"].startswith("sha256:")
        assert descriptor["output_schema_hash"].startswith("sha256:")

    def test_app_tool_rejects_reserved_namespace_at_construction(self) -> None:
        with pytest.raises(CanonicalNameError):
            AppTool(
                installation_id="appi_bad",
                app_id="bad",
                app_namespace="memory",  # reserved namespace
                tool_id="bad.search",
                tool_name="search",
                action="search",
                tool_description="bad tool",
                tool_input_schema={"type": "object"},
                tool_required_scopes=[],
            )

    def test_tool_search_is_platform_source(self) -> None:
        registry = ToolRegistry()
        directory = ToolDirectoryService(registry)
        search = ToolSearchTool(directory)

        assert search.source == "platform"
        descriptor = search.descriptor()
        assert descriptor["source"] == "platform"
        assert descriptor["canonical_name"] == "hasn.cloud.tool.search"
        assert descriptor["execution_location"] == "cloud"


class TestDiscoveryAlias:
    """P1: cloud discovery tool name + `hasn.tool.search` migration alias."""

    def test_cloud_search_canonical_and_alias_resolve(self) -> None:
        from backend.app.mcp.tools.registry import BOOTSTRAP_TOOL_NAMES

        registry = ToolRegistry()
        directory = ToolDirectoryService(registry)
        registry.register(ToolSearchTool(directory))
        registry.register_alias("hasn.tool.search", "hasn.cloud.tool.search")

        # bootstrap 集 = 发现 + 调用两个元工具（03 §9）。
        assert frozenset({"hasn.cloud.tool.search", "hasn.cloud.tool.call"}) == BOOTSTRAP_TOOL_NAMES

        canonical = registry.get_tool("hasn.cloud.tool.search")
        alias = registry.get_tool("hasn.tool.search")
        assert canonical is not None
        assert alias is canonical  # alias resolves to the same instance

        # Bootstrap exposes only registered canonical bootstrap tools, never the alias.
        # 本用例只注册了 tool.search，故 bootstrap 仅含它（tool.call 未注册）。
        bootstrap_names = {tool.name for tool in registry.list_bootstrap_tools()}
        assert bootstrap_names == {"hasn.cloud.tool.search"}


class TestSourceDispatch:
    """P2: MCP error codes carried by the source-dispatch resolution."""

    def test_mcp_tool_error_carries_code_and_is_value_error(self) -> None:
        from backend.app.mcp.errors import McpErrorCode, McpToolError

        err = McpToolError(McpErrorCode.TOOL_NOT_FOUND, "Tool not found: hasn.x.y")
        assert "MCP_9209" in str(err)
        assert "TOOL_NOT_FOUND" in str(err)
        assert "Tool not found" in str(err)
        assert err.code is McpErrorCode.TOOL_NOT_FOUND
        # Subclasses ValueError so MCP routes keep mapping it to HTTP 404.
        assert isinstance(err, ValueError)


class TestExecutionLocation:
    """P3: execution_location registration model + discovery projection."""

    def _app_tool(self, execution_location: str = "cloud") -> AppTool:
        return AppTool(
            installation_id="appi_k",
            app_id="knowledge",
            app_namespace="knowledge",
            tool_id="knowledge.search",
            tool_name="search",
            action="search",
            tool_description="Search",
            tool_input_schema={"type": "object"},
            tool_required_scopes=[],
            execution_location=execution_location,
        )

    def test_app_tool_defaults_to_cloud(self) -> None:
        tool = self._app_tool()
        assert tool.execution_location == "cloud"
        assert tool.descriptor()["execution_location"] == "cloud"

    def test_app_tool_can_declare_local(self) -> None:
        tool = self._app_tool("local")
        assert tool.descriptor()["execution_location"] == "local"

    def test_registry_replaces_dynamic_app_snapshot(self) -> None:
        registry = ToolRegistry()
        original = self._app_tool()
        refreshed = AppTool(
            installation_id='appi_k_v2',
            app_id='knowledge',
            app_namespace='knowledge',
            tool_id='knowledge.search',
            tool_name='search',
            action='search',
            tool_description='更新后的知识检索',
            tool_input_schema={
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
                'required': ['query'],
            },
            tool_required_scopes=['knowledge.read'],
        )

        registry.replace_source_tools('app', [original])
        registry.replace_source_tools('app', [refreshed])

        resolved = registry.get_tool('hasn.knowledge.search')
        assert resolved is refreshed
        assert len(registry.get_all_tools()) == 1

    def test_directory_schema_projection_carries_execution_location(self) -> None:
        import asyncio

        from backend.app.mcp.auth import AgentContext
        from backend.app.mcp.tool_directory import ToolSearchQuery

        registry = ToolRegistry()
        registry.register(self._app_tool())
        directory = ToolDirectoryService(registry)
        ctx = AgentContext(
            hasn_id="a_exec",
            owner_id=1,
            agent_status="active",
            metadata={},
        )

        result = asyncio.run(
            directory.search(
                ctx,
                ToolSearchQuery(query="tool:hasn.knowledge.search", detail="schema"),
            )
        )
        assert result["schemas"][0]["execution_location"] == "cloud"


class TestAuditSampling:
    """P5: audit landing (`04 §6`) — calls/schema/denials always; summary sampled."""

    def _server(self) -> Any:
        from backend.app.mcp.server import HasnCloudMcpServer

        return HasnCloudMcpServer()

    def test_real_tool_call_always_audited(self) -> None:
        server = self._server()
        assert server._should_audit_call("hasn.contact.list", {"limit": 10}, success=True)

    def test_failure_or_denial_always_audited(self) -> None:
        server = self._server()
        assert server._should_audit_call(
            "hasn.cloud.tool.search",
            {"detail": "summary", "query": "anything"},
            success=False,
        )

    def test_schema_query_always_audited(self) -> None:
        server = self._server()
        assert server._should_audit_call(
            "hasn.cloud.tool.search",
            {"detail": "schema", "query": "knowledge"},
            success=True,
        )

    def test_summary_discovery_queries_are_downsampled(self) -> None:
        server = self._server()
        decisions = [
            server._should_audit_call(
                "hasn.cloud.tool.search",
                {"detail": "summary", "query": f"q{i}"},
                success=True,
            )
            for i in range(100)
        ]
        audited = sum(decisions)
        # Downsampled: not every summary query lands in the audit log, but some
        # do — and every call still returns a trace_id for aggregation.
        assert 0 < audited < 100
