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
async def test_tool_call_inner_ask_approved_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """内层 ask（云端挂起 cloud-pend）：主人批准 → tool.call 落到内层**真执行**，对 agent 透明。

    福仔 2026-06-08 拍板：分身经 cloud 直连面命中 ask 时云端自己挂起（发卡片+轮询裁决），
    批准后直接返回工具结果——agent 只等工具返回，不知道 ask 过程（不再回 approval_required）。
    """
    from backend.app.mcp import ask_gate as ask_gate_module

    server = _server(monkeypatch)
    seen: dict[str, Any] = {}

    async def _request_and_wait(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        seen['tool_name'] = kwargs.get('tool_name')
        return {'decision': 'approved', 'request_id': 'areq_meta', 'description': 'x'}

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'request_and_wait', _request_and_wait)

    ctx = _ctx(capability_modes={'hasn.stub.act': 'ask'})
    result = await _call_tool(server).execute(ctx, {'name': 'hasn.stub.act', 'params': {'content': 'hi'}})
    assert result == {'echo': {'content': 'hi'}}  # 批准后内层真执行（不是审批信封）
    assert seen['tool_name'] == 'hasn.stub.act'  # 内层工具确实进了 ask 挂起闸门


@pytest.mark.asyncio
async def test_tool_call_inner_ask_denied_returns_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """内层 ask 被主人拒绝 → 回**工具错误**（approval_denied），绝不把 approval_required/request_id 透给 agent。

    回归截图事故：旧逻辑把 `areq_...` 请求 ID + “需要你批准后才能执行”当工具结果返回给 agent，
    agent 因此知道了 ask 过程——这正是要修掉的。现在 agent 只看到一个普通工具错误。
    """
    from backend.app.mcp import ask_gate as ask_gate_module

    server = _server(monkeypatch)

    async def _request_and_wait(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        return {'decision': 'denied', 'request_id': 'areq_xyz', 'description': 'x'}

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'request_and_wait', _request_and_wait)

    ctx = _ctx(capability_modes={'hasn.stub.act': 'ask'})
    result = await _call_tool(server).execute(ctx, {'name': 'hasn.stub.act', 'params': {'content': 'hi'}})
    assert result['ok'] is False
    assert result['error'] == 'approval_denied'
    # agent 对 ask 过程无感：既不执行内层（无 echo），也不泄露 approval_required / request_id。
    assert 'echo' not in result
    assert result.get('error') != 'approval_required'
    assert 'approval' not in result and 'request_id' not in result


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


# ── 会话绑定守卫（register-on-write 铁律·经 tool.call 转发不许丢会话）─────────────


class _SessionEchoTool(_StubTool):
    """回读 ContextVar 里的工作会话 id——模拟 knowledge 等 handler 面 register-on-write 的取法。"""

    @property
    def name(self) -> str:
        return 'hasn.stub.session_echo'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}, 'additionalProperties': True}

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.mcp.context import get_current_work_session_id

        return {
            'ctxvar_session': get_current_work_session_id(),
            'field_session': agent_context.session_id,
        }


def _session_server(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = _server(monkeypatch)
    server.tool_registry.register(_SessionEchoTool())

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    # 完整 call_tool 链路会加载外部 MCP 工具（连 DB），本测试与之无关，noop 掉保持零 DB。
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    return server


@pytest.mark.asyncio
async def test_call_tool_direct_stamp_binds_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """直调面（设计 02 §4.3 会话轴分流）：`_hasn_work_session_id` 剥离后落 ContextVar（工作会话轴），
    `_hasn_session_id` 只落 AgentContext.session_id（运行时轴）——两键分落、互不串扰。"""
    server = _session_server(monkeypatch)
    result = await server.call_tool(
        _ctx(),
        'hasn.stub.session_echo',
        {'_hasn_work_session_id': 'sess_guard_ws', '_hasn_session_id': 'sess_guard_rt'},
    )
    assert result == {'ctxvar_session': 'sess_guard_ws', 'field_session': 'sess_guard_rt'}


@pytest.mark.asyncio
async def test_call_tool_legacy_runtime_session_id_never_lands_on_work_session_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合语义 `_hasn_session_id` 绝不落工作会话 ContextVar（P2-8d 起旧「在册 task 收窄」
    回落退役，runtime id 天然无从误绑）；运行时轴照常沉淀。"""
    server = _session_server(monkeypatch)
    result = await server.call_tool(
        _ctx(), 'hasn.stub.session_echo', {'_hasn_session_id': 'sess_guard_runtime_only'}
    )
    assert result == {'ctxvar_session': None, 'field_session': 'sess_guard_runtime_only'}


@pytest.mark.asyncio
async def test_call_tool_via_meta_forward_keeps_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """经 hasn.cloud.tool.call 转发仍绑会话（知识库产物丢会话归属的回归钉子）。

    渐进暴露下 app 工具只能经元工具触达：stamp 只打在最外层调用入参上，元工具重入
    call_tool 时内层 params 没有 stamp。ContextVar 若按 origin stamp（=None）无条件
    覆写，就会把外层已落的会话 id 抹掉——handler 面（knowledge 等）登记的产物全部丢
    工作会话归属。修法：只进不退——已落非 None 绝不覆写（设计 02 §4.3 分流后语义不变）。
    """
    server = _session_server(monkeypatch)
    result = await server.call_tool(
        _ctx(),
        'hasn.cloud.tool.call',
        {'name': 'hasn.stub.session_echo', 'params': {}, '_hasn_work_session_id': 'sess_guard_meta'},
    )
    assert result == {'ctxvar_session': 'sess_guard_meta', 'field_session': None}


@pytest.mark.asyncio
async def test_call_tool_no_stamp_leaves_session_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """主会话直调（无 stamp）：会话 id 两处都为空，产物只进分身产物 tab、不挂工作会话。"""
    server = _session_server(monkeypatch)
    result = await server.call_tool(_ctx(), 'hasn.stub.session_echo', {})
    assert result == {'ctxvar_session': None, 'field_session': None}
