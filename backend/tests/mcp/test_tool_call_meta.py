"""元工具 hasn.cloud.tool.call 单测（设计 03 §9）。

覆盖：
- list_tools 默认只回 bootstrap（tool.search + tool.call），长尾工具不进清单；
- 直调成功 → 委托内层执行；
- 参数 schema 校验失败 → 回吐内层完整 schema + missing/invalid（schema-on-error，§9.4）；
- 递归护栏 → DIRECT_CALL_DENIED；未知工具 → TOOL_NOT_FOUND；
- 内层三态：deny → PermissionError；ask → 走批准闸门（approved 执行 / rejected raise）。

零外部依赖：_load_app_tools / _log_tool_call / ask 闸门全 monkeypatch，不连 DB/Redis。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tools.base import BaseTool


class _StubTool(BaseTool):
    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.stub.act'

    @property
    def description(self) -> str:
        return 'stub tool for tool.call tests'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {'content': {'type': 'string'}, 'n': {'type': 'integer'}},
            'required': ['content'],
            'additionalProperties': False,
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['stub:act']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'echo': arguments}


def _ctx(*, default_mode: str = 'allow', capability_modes: dict | None = None) -> AgentContext:
    return AgentContext(
        hasn_id='a_call_test',
        owner_id=0,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id='h_call_test',
        session_uuid='amk_call_test',
        default_mode=default_mode,
        capability_modes=capability_modes or {},
    )


def _server(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()
    server.tool_registry.register(_StubTool())

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


def _call_tool(server: HasnCloudMcpServer) -> BaseTool:
    tool = server.tool_registry.get_tool('hasn.cloud.tool.call')
    assert tool is not None
    return tool


@pytest.mark.asyncio
async def test_list_tools_returns_only_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    tools = await server.list_tools(_ctx())
    names = sorted(t['name'] for t in tools)
    assert names == ['hasn.cloud.tool.call', 'hasn.cloud.tool.search']
    assert 'hasn.stub.act' not in names  # 长尾工具不进清单


@pytest.mark.asyncio
async def test_tool_call_valid_delegates_to_inner(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'params': {'content': 'hi', 'n': 3}})
    assert result == {'echo': {'content': 'hi', 'n': 3}}


@pytest.mark.asyncio
async def test_tool_call_missing_required_returns_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'params': {}})
    assert result['ok'] is False
    assert result['error'] == 'input_validation_failed'
    assert result['tool'] == 'hasn.stub.act'
    assert 'content' in result['missing']
    assert result['input_schema']['required'] == ['content']  # 回吐完整内层 schema
    assert result['schema_hash'].startswith('sha256:')


@pytest.mark.asyncio
async def test_tool_call_type_error_reports_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    result = await _call_tool(server).execute(
        _ctx(), {'name': 'hasn.stub.act', 'params': {'content': 'ok', 'n': 'not-int'}}
    )
    assert result['ok'] is False
    assert 'n' in result['invalid']


@pytest.mark.asyncio
async def test_tool_call_recursion_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'name': 'hasn.cloud.tool.call', 'params': {}})
    assert exc.value.code == McpErrorCode.DIRECT_CALL_DENIED


@pytest.mark.asyncio
async def test_tool_call_unknown_tool_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'name': 'hasn.nope.x', 'params': {}})
    assert exc.value.code == McpErrorCode.TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_tool_call_inner_deny_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    ctx = _ctx(capability_modes={'hasn.stub.act': 'deny'})
    with pytest.raises(PermissionError):
        await _call_tool(server).execute(ctx, {'name': 'hasn.stub.act', 'params': {'content': 'hi'}})


@pytest.mark.asyncio
async def test_tool_call_inner_ask_returns_approval_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """内层 ask（令牌重试模型）：tool.call 透传 approval_required 信封，内层**不执行**、不 raise。"""
    from backend.app.mcp import ask_gate as ask_gate_module

    server = _server(monkeypatch)
    opened: dict[str, Any] = {}
    envelope = {'ok': False, 'error': 'approval_required', 'code': 'MCP_9215', 'approval': {'request_id': 'areq_meta'}}

    async def _open_request(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        opened['tool_name'] = kwargs.get('tool_name')
        return envelope

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'open_request', _open_request)

    ctx = _ctx(capability_modes={'hasn.stub.act': 'ask'})
    result = await _call_tool(server).execute(ctx, {'name': 'hasn.stub.act', 'params': {'content': 'hi'}})
    assert result == envelope  # 透传信封；若执行了内层会是 {'echo': ...}
    assert opened['tool_name'] == 'hasn.stub.act'  # 内层工具确实进了 ask 闸门


@pytest.mark.asyncio
async def test_tool_call_inner_ask_envelope_carries_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """ask 信封带 request_id，供 daemon 驱动卡片审批 + 换票重试。"""
    from backend.app.mcp import ask_gate as ask_gate_module

    server = _server(monkeypatch)

    async def _open_request(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        return {'ok': False, 'error': 'approval_required', 'code': 'MCP_9215', 'approval': {'request_id': 'areq_xyz'}}

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'open_request', _open_request)

    ctx = _ctx(capability_modes={'hasn.stub.act': 'ask'})
    result = await _call_tool(server).execute(ctx, {'name': 'hasn.stub.act', 'params': {'content': 'hi'}})
    assert result['error'] == 'approval_required'
    assert result['approval']['request_id'] == 'areq_xyz'


# ── 参数透传健壮性（线上 bug：params 落成空对象 → 兼容三种到达形态）─────────────


@pytest.mark.asyncio
async def test_tool_call_params_as_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """部分 Runtime 把 params 序列化成 JSON 字符串 → 服务端应解析。"""
    server = _server(monkeypatch)
    result = await _call_tool(server).execute(
        _ctx(), {'name': 'hasn.stub.act', 'params': '{"content": "hi", "n": 7}'}
    )
    assert result == {'echo': {'content': 'hi', 'n': 7}}


@pytest.mark.asyncio
async def test_tool_call_params_flattened_to_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """部分 Runtime 不嵌套 params，把内层参数平铺到顶层 → 服务端应收拢。"""
    server = _server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'content': 'hi', 'n': 9})
    assert result == {'echo': {'content': 'hi', 'n': 9}}


@pytest.mark.asyncio
async def test_tool_call_nested_params_take_precedence_over_flatten(monkeypatch: pytest.MonkeyPatch) -> None:
    """嵌套 params 非空时以它为准，不把顶层无关键混进去。"""
    server = _server(monkeypatch)
    result = await _call_tool(
        server
    ).execute(_ctx(), {'name': 'hasn.stub.act', 'params': {'content': 'real'}, 'noise': 'x'})
    assert result == {'echo': {'content': 'real'}}


@pytest.mark.asyncio
async def test_tool_call_params_invalid_json_string_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'params': '{not json'})
    assert exc.value.code == McpErrorCode.TOOL_NOT_FOUND


def test_tool_call_schema_advertises_open_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """schema 必须把 params 声明成「开放对象」，否则 function-calling LLM 填不进字段（线上根因）。"""
    server = _server(monkeypatch)
    schema = _call_tool(server).input_schema
    params_schema = schema['properties']['params']
    assert params_schema.get('additionalProperties') is True  # 允许任意内层字段
    # 同时容忍对象或 JSON 字符串两种承载
    assert 'object' in params_schema['type']
    assert 'string' in params_schema['type']
    # 顶层放开 → 平铺参数能通过 MCP SDK 的 jsonschema 校验抵达桥
    assert schema.get('additionalProperties') is True
