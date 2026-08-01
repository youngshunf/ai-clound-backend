"""技能市场本地工具面切换反向守卫（DOC15-95 M0）。"""

from __future__ import annotations

from pathlib import Path

from backend.app.mcp.server import HasnCloudMcpServer


def test_cloud_mcp_registers_no_marketplace_tools() -> None:
    """云端 MCP 不得继续暴露任何 ``hasn.marketplace.*`` 工具。"""
    registered = {
        tool.name
        for tool in HasnCloudMcpServer().tool_registry.get_all_tools()
        if tool.name.startswith('hasn.marketplace.')
    }
    assert registered == set()


def test_cloud_marketplace_mcp_source_contains_no_binary_payload_contract() -> None:
    """云端市场 MCP 活代码不得继续声明 base64/字节包搬运契约。"""
    tools_dir = Path(__file__).resolve().parents[3] / 'app' / 'mcp' / 'tools'
    marketplace_source = tools_dir / 'marketplace.py'
    if not marketplace_source.exists():
        return

    source = marketplace_source.read_text(encoding='utf-8')
    forbidden = ('package_' + 'base64', '*_' + 'base64', 'base64.' + 'b64decode')
    assert all(token not in source for token in forbidden)
