"""stock 平台工具契约测试（A-P2·纯函数，无 DB 无网络）。

验两件套 hasn.stock.search / hasn.stock.download 的静态声明：source/namespace/execution_location/
risk_level/required_scopes + input_schema 形状（含铁律：入参**不得**出现 *_base64 / 字节块入参）。
"""

from __future__ import annotations

import json

from backend.app.mcp.tools.stock import STOCK_TOOLS, StockDownloadTool, StockSearchTool


def test_stock_tools_exported() -> None:
    names = {t.name for t in STOCK_TOOLS}
    assert names == {'hasn.stock.search', 'hasn.stock.download'}


def test_server_registers_stock_tools() -> None:
    """server._register_builtin_tools 真注册两件套——否则分身 tools/list 里根本看不到、调不到（防漏接线回归）。"""
    from backend.app.mcp.server import mcp_server

    registered = {tool.name for tool in mcp_server.tool_registry.get_all_tools()}
    for tool in STOCK_TOOLS:
        assert tool.name in registered, f'{tool.name} 未在 MCP server 注册'


def test_search_tool_static_declaration() -> None:
    t = StockSearchTool()
    assert t.name == 'hasn.stock.search'
    assert t.namespace == 'hasn.stock'
    assert t.source == 'platform'
    assert t.execution_location == 'cloud'
    assert t.risk_level == 'low'
    # 不外发、不动钱 → 出厂 Allow（无 scope 门）。
    assert t.required_scopes == []


def test_search_input_schema_shape() -> None:
    schema = StockSearchTool().input_schema
    assert schema['type'] == 'object'
    props = schema['properties']
    assert set(props) == {'query', 'media_type', 'source', 'orientation', 'per_page'}
    assert schema['required'] == ['query']
    assert props['media_type']['enum'] == ['image', 'video']
    assert props['orientation']['enum'] == ['landscape', 'portrait', 'square']
    # source enum 动态渲染（缓存冷时兜底内置三站）——至少是个 list。
    assert isinstance(props['source']['enum'], list)


def test_download_tool_static_declaration() -> None:
    t = StockDownloadTool()
    assert t.name == 'hasn.stock.download'
    assert t.namespace == 'hasn.stock'
    assert t.source == 'platform'
    assert t.execution_location == 'cloud'
    assert t.required_scopes == []


def test_download_input_schema_props() -> None:
    # url 必填；title/description 可选（description 落 artifact summary 提升检索召回）。
    schema = StockDownloadTool().input_schema
    props = schema['properties']
    assert set(props) == {'url', 'title', 'description'}
    assert schema['required'] == ['url']


def test_no_binary_base64_input_params() -> None:
    """铁律：Agent 工具入参禁止二进制 base64 / 字节块。"""
    for tool in STOCK_TOOLS:
        blob = json.dumps(tool.input_schema).lower()
        assert 'base64' not in blob, f'{tool.name} 入参不得出现 base64'
        assert '_bytes' not in blob
        for key in tool.input_schema.get('properties', {}):
            assert not key.endswith('_base64'), f'{tool.name}.{key} 违反禁二进制入参铁律'
