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

import logging

from typing import Any

import jsonschema
import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tools.base import BaseTool
from backend.app.mcp.trust_gate import allow_reserved_fields_in_schema


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
    """params 串不是合法 JSON → 报**入参错误**，绝不再报 TOOL_NOT_FOUND。

    旧行为把它塞进 TOOL_NOT_FOUND(9209)：分身看到「工具不存在」只会去换工具名重试，而真因是
    自己这段 JSON 写坏了。实测某分身 41% 的 designsystem.save 卡在这条上。反向断言钉住这次改判——
    退回 9209 即红。
    """
    server = _server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'params': '{not json'})
    assert exc.value.code == McpErrorCode.INVALID_CALL_ARGUMENTS
    assert exc.value.code != McpErrorCode.TOOL_NOT_FOUND  # 反向：不得退回旧码
    assert 'params' in exc.value.message


@pytest.mark.asyncio
async def test_tool_call_oversized_bad_json_advises_sharding(monkeypatch: pytest.MonkeyPatch) -> None:
    """超大 params 串坏掉时，错误要点破真因（一次塞太多）并指出分片/句柄这条出路。"""
    server = _server(monkeypatch)
    huge = '{"content": "' + 'x' * 9000  # 未闭合，且超过 8000 字符的「过大」阈值
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'params': huge})
    assert exc.value.code == McpErrorCode.INVALID_CALL_ARGUMENTS
    assert '分片' in exc.value.message  # 给出出路，而不是只说「不合法」
    assert str(len(huge)) in exc.value.message  # 把实际长度摆出来，分身才知道自己塞了多少


@pytest.mark.asyncio
async def test_tool_call_small_bad_json_does_not_advise_sharding(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向：小 params 坏掉是纯语法问题，不该甩「入参太大」这个不对症的建议。"""
    server = _server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.act', 'params': '{not json'})
    assert '分片' not in exc.value.message
    assert '转义' in exc.value.message


def test_tool_call_schema_exposes_exactly_one_target_name_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """schema 里承载「目标工具名」的键只能有一个——两个等价键会让模型把工具名填两遍。

    2026-08-26 线上回归：properties 同时列出 `tool` 与 `name`、两者描述都写「目标工具 canonical
    name」，模型产出 `{"name":"hasn.designsystem.get","tool":"hasn.designsystem.get"}`——工具名填了
    两遍，**params 整个丢掉**，designsystem / knowledge 等域一起报 missing，分身在纠正循环里
    finish_reason=stop 收尾。服务端仍兼容接收 `name`（见 _resolve_target_name），但那是**收**，不是**宣传**。

    这条是本次回归唯一可机器化的判据：模型会怎么填 schema 测不了，但「schema 里有没有摆出两个
    等价选项」测得了。
    """
    server = _server(monkeypatch)
    schema = _call_tool(server).input_schema
    properties = schema['properties']
    assert 'tool' in properties
    assert 'name' not in properties, 'schema 不得同时暴露 tool 与 name 两个工具名键'
    assert 'params' in properties
    # 描述里要明说业务参数只能放 params——模型是照描述填的
    assert 'params' in properties['tool']['description']
    # `name` 只能不被宣传成**键**；描述散文里出现「canonical name」「目标工具自己的 name 字段」
    # 是说明不是宣传，故判结构位置、不扫文案。
    assert set(properties) == {'tool', 'params'}
    # 顶层不得出现任何**判定性**关键字：schema 只宣传，判定一律落 handler。写进 required /
    # anyOf 的键会被 MCP SDK 在 handler 之前强制，把服务端的兼容与平铺兜底一起变成死代码。
    for keyword in ('required', 'anyOf', 'oneOf', 'allOf', 'dependentRequired'):
        assert keyword not in schema, f'顶层不得出现 {keyword}——SDK 会拿它先于 handler 判死 wire 入参'


def _assert_passes_sdk_gate(server: HasnCloudMcpServer, arguments: dict[str, Any]) -> None:
    """按 MCP SDK 的方式校验 wire 入参——这是本元工具真正的第一道闸。

    ⚠️ 直接 `await tool.execute(...)` **测不到**这道闸：SDK 在 `mcp/server/lowlevel/server.py`
    里先 `jsonschema.validate(instance=arguments, schema=tool.inputSchema)`，通过了才调我们的
    handler。2026-08-26 线上事故正是死在这里——服务端的 `name` 兼容分支写得好好的，但 schema 把
    `tool` 钉成 required，wire 上根本走不到那个分支，而单测因为直调 execute 一路全绿。
    schema 与线上同源过一道 `allow_reserved_fields_in_schema`（list_tools 就是这么投影的）。
    """
    schema = allow_reserved_fields_in_schema(_call_tool(server).input_schema)
    jsonschema.validate(instance=arguments, schema=schema)


@pytest.mark.parametrize(
    ('shape', 'arguments'),
    [
        ('规范形', {'tool': 'hasn.stub.act', 'params': {'content': 'hi'}}),
        ('兼容 name', {'name': 'hasn.stub.act', 'params': {'content': 'hi'}}),
        ('params 是 JSON 串', {'tool': 'hasn.stub.act', 'params': '{"content": "hi"}'}),
        ('平铺到顶层', {'tool': 'hasn.stub.act', 'content': 'hi'}),
        ('两个键都填', {'tool': 'hasn.stub.act', 'name': 'hasn.stub.act', 'params': {'content': 'hi'}}),
        ('系统注入保留字段', {'tool': 'hasn.stub.act', 'params': {'content': 'hi'}, '_hasn_session_id': 's1'}),
    ],
)
def test_advertised_schema_lets_every_supported_wire_shape_through_the_sdk_gate(
    monkeypatch: pytest.MonkeyPatch, shape: str, arguments: dict[str, Any],
) -> None:
    """服务端宽容接收的每一种形态，都必须先能过 SDK 的 jsonschema 闸——否则那份宽容是死代码。

    变异验证：给 input_schema 加回 `"required": ["tool", "params"]`，「兼容 name」与「平铺到顶层」
    两条当场红（正是 2026-08-26 线上那两条路）。
    """
    _assert_passes_sdk_gate(_server(monkeypatch), arguments)


@pytest.mark.asyncio
async def test_tool_call_still_accepts_legacy_name_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向：`name` 不再进 schema，但服务端必须继续收——老 Runtime 与存量调用方还在用它。

    先过 SDK 闸再进 handler：只断言后者会漏掉真正的失败点（见 `_assert_passes_sdk_gate`）。
    """
    server = _server(monkeypatch)
    arguments = {'name': 'hasn.stub.act', 'params': {'content': 'hi'}}
    _assert_passes_sdk_gate(server, arguments)
    result = await _call_tool(server).execute(_ctx(), arguments)
    assert result == {'echo': {'content': 'hi'}}


@pytest.mark.asyncio
async def test_tool_name_filled_twice_still_reaches_inner_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个键都填成同一个工具名时（线上那个形状），params 里的入参仍须如实抵达内层。"""
    server = _server(monkeypatch)
    result = await _call_tool(server).execute(
        _ctx(), {'name': 'hasn.stub.act', 'tool': 'hasn.stub.act', 'params': {'content': 'hi'}}
    )
    assert result == {'echo': {'content': 'hi'}}


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


# ── 字段值级序列化还原（线上 bug：deck 三个写工具一律 input_validation_failed）──────────
#
# 现象：分身经 tool.call 调 hasn.deck.page.write / page.write_batch / outline.set 全部返回
# input_validation_failed，而同域的 create / page.edit 正常——差别是前三个的**必填字段含
# 非 string 类型**（position:integer、pages:array），后两个全是 string。
# 根因：`_extract_params` 只把顶层 params 从字符串还原成 dict，字段值这一层无人还原；
# function-calling Runtime 常把嵌套容器序列化成 JSON 字符串、把数值当字符串填，撞上
# Draft202012Validator 的严格类型校验就整调用判死。


class _ShapeTool(_StubTool):
    """带 integer / array / object 字段的工具——复刻 deck 写工具的入参形状。"""

    @property
    def name(self) -> str:
        return 'hasn.stub.shape'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'deck_id': {'type': 'string', 'minLength': 1},
                'position': {'type': 'integer', 'minimum': 0},
                'pages': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'position': {'type': 'integer', 'minimum': 0},
                            'html': {'type': 'string', 'minLength': 1},
                        },
                        'required': ['position', 'html'],
                    },
                },
                'design_contract': {'type': ['object', 'null']},
                'confirm': {'type': 'boolean'},
            },
            'required': ['deck_id'],
        }


def _shape_server(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = _server(monkeypatch)
    server.tool_registry.register(_ShapeTool())
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('label', 'params', 'expected'),
    [
        ('整数被当作字符串填', {'deck_id': '21', 'position': '0'}, {'deck_id': '21', 'position': 0}),
        ('字符串字段被填成整数', {'deck_id': 21}, {'deck_id': '21'}),
        (
            '数组被序列化成 JSON 字符串',
            {'deck_id': '21', 'pages': '[{"position": 0, "html": "<div>x</div>"}]'},
            {'deck_id': '21', 'pages': [{'position': 0, 'html': '<div>x</div>'}]},
        ),
        (
            '数组元素内的整数被字符串化',
            {'deck_id': '21', 'pages': [{'position': '2', 'html': '<div>x</div>'}]},
            {'deck_id': '21', 'pages': [{'position': 2, 'html': '<div>x</div>'}]},
        ),
        (
            '双重序列化：JSON 串内的整数也是串',
            {'deck_id': '21', 'pages': '[{"position": "2", "html": "<div>x</div>"}]'},
            {'deck_id': '21', 'pages': [{'position': 2, 'html': '<div>x</div>'}]},
        ),
        (
            '对象被序列化成 JSON 字符串',
            {'deck_id': '21', 'design_contract': '{"palette": "deep-blue"}'},
            {'deck_id': '21', 'design_contract': {'palette': 'deep-blue'}},
        ),
        ('布尔被当作字符串填', {'deck_id': '21', 'confirm': 'true'}, {'deck_id': '21', 'confirm': True}),
    ],
)
async def test_tool_call_coerces_serialized_field_values(
    monkeypatch: pytest.MonkeyPatch, label: str, params: dict[str, Any], expected: dict[str, Any]
) -> None:
    """字段值被 Runtime 序列化时应还原后放行，且**内层 handler 收到的就是还原后的值**。"""
    server = _shape_server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.shape', 'params': params})
    assert result == {'echo': expected}, label


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('label', 'params', 'bad_field'),
    [
        ('真错型：整数位填了纯文字', {'deck_id': '21', 'position': '第一页'}, 'position'),
        ('真错型：数组位填了非 JSON 文本', {'deck_id': '21', 'pages': '封面、目录、正文'}, 'pages'),
        ('还原出的类型不符：串解出来是对象而非数组', {'deck_id': '21', 'pages': '{"position": 0}'}, 'pages'),
        ('越界仍判：position 为负', {'deck_id': '21', 'position': '-1'}, 'position'),
        ('布尔位填了别的词', {'deck_id': '21', 'confirm': 'maybe'}, 'confirm'),
    ],
)
async def test_tool_call_still_rejects_genuine_type_errors(
    monkeypatch: pytest.MonkeyPatch, label: str, params: dict[str, Any], bad_field: str
) -> None:
    """宽容还原不得退化成猜：转不动的一律原样交给校验器如实报错（零 fake）。"""
    server = _shape_server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.shape', 'params': params})
    assert result['ok'] is False, label
    assert result['error'] == 'input_validation_failed', label
    assert bad_field in result['invalid'], label


@pytest.mark.asyncio
async def test_tool_call_missing_required_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺必填仍走 schema-on-error，不因还原层而被吞掉。"""
    server = _shape_server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.shape', 'params': {'position': 1}})
    assert result['ok'] is False
    assert 'deck_id' in result['missing']


@pytest.mark.asyncio
async def test_tool_call_leaves_wellformed_params_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """已合规的入参绝不改动——还原层只治真正错型的，避免 args_hash 漂移。"""
    server = _shape_server(monkeypatch)
    params = {'deck_id': '21', 'position': 3, 'pages': [{'position': 0, 'html': '<p>x</p>'}], 'confirm': False}
    result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.shape', 'params': params})
    assert result == {'echo': params}


def test_coerce_does_not_mutate_input() -> None:
    """纯函数：不得就地改写调用方的 dict（ask_gate args_hash 依赖确定性）。"""
    from backend.app.mcp.tools.tool_call import coerce_params_to_schema

    schema = {'type': 'object', 'properties': {'n': {'type': 'integer'}}}
    raw = {'n': '5'}
    coerced = coerce_params_to_schema(schema, raw)
    assert coerced == {'n': 5}
    assert raw == {'n': '5'}  # 原 dict 未被改动


_DECK_SERIALIZED_PAYLOADS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    # tool → (Runtime 实际发来的形态, 内层 handler 应当收到的形态)
    'hasn.deck.page.write': (
        {'deck_id': 21, 'position': '0', 'html': '<div>x</div>'},
        {'deck_id': '21', 'position': 0, 'html': '<div>x</div>'},
    ),
    'hasn.deck.page.write_batch': (
        {'deck_id': '21', 'pages': '[{"position": "0", "html": "<div>x</div>"}]'},
        {'deck_id': '21', 'pages': [{'position': 0, 'html': '<div>x</div>'}]},
    ),
    'hasn.deck.outline.set': (
        {'deck_id': '21', 'pages': '[{"title": "\\u5c01\\u9762"}]'},
        {'deck_id': '21', 'pages': [{'title': '封面'}]},
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize('tool_name', sorted(_DECK_SERIALIZED_PAYLOADS))
async def test_real_deck_write_tools_accept_serialized_params(monkeypatch: pytest.MonkeyPatch, tool_name: str) -> None:
    """回归钉子：用**真实 deck input_schema** 钉住线上撞到的那三个工具。

    stub 形状会随手改漂移，这条直接吃 DECK_TOOLS 的真 schema，并且**走 execute 全路径**
    （只把最内层 call_tool 换成探针，不连 DB）——既守住还原逻辑本身，也守住它确实接在了
    转发路径上。deck 侧若再把 position/pages 的声明收窄回去，本测试会红。
    """
    server = _server(monkeypatch)
    # deck 工具由 HasnCloudMcpServer.__init__ 启动期注册，此处直接取真实注册项（不重复注册）。
    assert server.tool_registry.get_tool(tool_name) is not None, f'{tool_name} 未在启动期注册'

    seen: dict[str, Any] = {}

    async def _probe(_ctx_arg: object, name: str, params: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
        seen['name'] = name
        seen['params'] = params
        return {'ok': True}

    monkeypatch.setattr(server, 'call_tool', _probe)

    sent, expected = _DECK_SERIALIZED_PAYLOADS[tool_name]
    result = await _call_tool(server).execute(_ctx(), {'name': tool_name, 'params': sent})

    assert result == {'ok': True}, f'{tool_name} 仍被判 input_validation_failed：{result}'
    assert seen['name'] == tool_name
    # 内层 handler（_deck_id / _upsert_pages）拿到的必须是还原后的形态，否则会在取值处再炸一次。
    assert seen['params'] == expected


@pytest.mark.asyncio
async def test_tool_call_validation_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """入参校验失败必须在服务端留痕。

    本分支 return 而非 raise，且 tool.call 在 `_DISPATCH_TOOL_NAMES` 里 → `_should_audit_call`
    返回 False、内层工具又压根没被调到，审计两侧皆空。没有这条日志，分身卡在这里的次数
    在运维侧完全不可见（deck 字段值序列化 bug 长期无声即此因）。
    """
    server = _shape_server(monkeypatch)
    with caplog.at_level(logging.WARNING, logger='backend.app.mcp.tools.tool_call'):
        result = await _call_tool(server).execute(_ctx(), {'name': 'hasn.stub.shape', 'params': {'position': '第一页'}})
    assert result['error'] == 'input_validation_failed'
    records = [r for r in caplog.records if 'hasn.stub.shape' in r.getMessage()]
    assert records, '校验失败未产生任何服务端日志'
    message = records[0].getMessage()
    assert records[0].levelno == logging.WARNING  # 可重试/可自愈 → warn，不是 error
    assert 'deck_id' in message  # 缺的必填字段名进日志
    assert 'position' in message  # 错型的字段名进日志
    assert '第一页' not in message  # 只记字段名，不记值（入参可能含整页 HTML）


# ── `name` 撞键：tool.call 的「目标工具名」与内层工具自己的 `name` 入参同名 ──────────────
# 实测事故（2026-08-25，生产 content_operator 分身）：`hasn.designsystem.save` 的必填 `name` 是
# 设计系统展示名，分身把它放在顶层 → 转发层拿它去查注册表 → 报
# `TOOL_NOT_FOUND: 昆明即时宠物零售 · 专业猫舍设计系统`，连撞两次触发 runtime 的
# repeated_exact_failure_warning。放进 params 又会被 tool.call 自己判「缺 name」——两条路都堵死。
# 同形状的还有 task / workflow 域共 5 处 schema。


class _NamedTool(BaseTool):
    """自身带**必填 `name`** 字段的内层工具（designsystem.save / task.* / workflow.* 的形状）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.stub.named'

    @property
    def description(self) -> str:
        return 'stub tool whose own input schema has a required `name` field'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {'name': {'type': 'string'}, 'slug': {'type': 'string'}},
            'required': ['name', 'slug'],
            'additionalProperties': False,
        }

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'echo': arguments}


def _named_server(monkeypatch: pytest.MonkeyPatch) -> HasnCloudMcpServer:
    server = _server(monkeypatch)
    server.tool_registry.register(_NamedTool())
    return server


@pytest.mark.asyncio
async def test_tool_key_carries_target_so_inner_name_survives_flattening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用 `tool` 指定目标 → 平铺在顶层的业务 `name` 必须如实抵达内层。

    这是撞键的正解：`_extract_params` 只排除**实际承载目标名的那个键**。若它退回旧写法
    （硬排除 `("name","params")`），内层 `name` 在平铺形态下结构性不可达，本用例即红。
    """
    server = _named_server(monkeypatch)
    result = await _call_tool(server).execute(
        _ctx(), {'tool': 'hasn.stub.named', 'name': '昆明即时宠物零售 · 专业猫舍设计系统', 'slug': 'kunming-pet'}
    )
    assert result == {'echo': {'name': '昆明即时宠物零售 · 专业猫舍设计系统', 'slug': 'kunming-pet'}}


@pytest.mark.asyncio
async def test_tool_key_wins_over_name_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个键都在时以 `tool` 为准——否则 `name` 里的业务值仍会被当成工具名。"""
    server = _named_server(monkeypatch)
    result = await _call_tool(server).execute(
        _ctx(), {'tool': 'hasn.stub.named', 'params': {'name': '展示名', 'slug': 's'}, 'name': '展示名'}
    )
    assert result == {'echo': {'name': '展示名', 'slug': 's'}}


@pytest.mark.asyncio
async def test_both_name_keys_holding_same_tool_name_do_not_leak_inward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tool` 与 `name` 装的是同一个工具名时，别把工具名当业务字段塞进内层。"""
    server = _named_server(monkeypatch)
    result = await _call_tool(server).execute(
        _ctx(), {'tool': 'hasn.stub.named', 'name': 'hasn.stub.named', 'slug': 's', 'params': {'name': 'n'}}
    )
    assert result == {'echo': {'name': 'n'}} or result['error'] == 'input_validation_failed'


@pytest.mark.asyncio
async def test_business_name_mistaken_for_tool_name_gets_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务名被当成工具名 → 报入参错误并给出正确形状，而不是「工具不存在」。"""
    server = _named_server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(
            _ctx(), {'name': '昆明即时宠物零售 · 专业猫舍设计系统', 'slug': 'kunming-pet'}
        )
    assert exc.value.code == McpErrorCode.INVALID_CALL_ARGUMENTS
    assert exc.value.code != McpErrorCode.TOOL_NOT_FOUND  # 反向：不得再报「工具不存在」
    assert 'tool' in exc.value.message and 'params' in exc.value.message  # 给出正确形状
    assert '昆明即时宠物零售 · 专业猫舍设计系统' in exc.value.message  # 点名是哪个值被误当工具名


@pytest.mark.asyncio
async def test_genuinely_unknown_canonical_name_still_reports_tool_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向：长得就是 canonical name 却查不到 → 仍报 TOOL_NOT_FOUND。

    两种失败必须分得开——把它们合并成同一个码，等于用一个模糊错误换掉两个精确错误。
    """
    server = _named_server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'tool': 'hasn.nosuch.action', 'params': {}})
    assert exc.value.code == McpErrorCode.TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_missing_target_name_is_argument_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个名字键都没有 → 入参错误（此前报 TOOL_NOT_FOUND，同样是错码）。"""
    server = _named_server(monkeypatch)
    with pytest.raises(McpToolError) as exc:
        await _call_tool(server).execute(_ctx(), {'params': {'name': 'n', 'slug': 's'}})
    assert exc.value.code == McpErrorCode.INVALID_CALL_ARGUMENTS


@pytest.mark.asyncio
async def test_validation_failure_ships_copyable_example(monkeypatch: pytest.MonkeyPatch) -> None:
    """校验失败必须附一个可逐字照抄的 `{tool, params}` 示例。

    完整 schema 只说「字段叫什么、什么类型」，从不示范「该放在哪一层」——实测分身拿着 schema
    仍反复把内层字段平铺到顶层。
    """
    server = _named_server(monkeypatch)
    result = await _call_tool(server).execute(_ctx(), {'tool': 'hasn.stub.named', 'params': {}})
    assert result['error'] == 'input_validation_failed'
    example = result['example']
    assert example['tool'] == 'hasn.stub.named'  # 示例用 tool 键，不用会撞名的 name
    assert set(example['params']) == {'name', 'slug'}  # 必填字段都在 params 里
    assert 'name' not in example  # 反向：业务 name 绝不出现在顶层


@pytest.mark.asyncio
async def test_how_to_fix_warns_about_name_collision_only_when_relevant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内层有 `name` 字段且它确实缺失时，才点破撞键陷阱；无关工具不发这条噪声。"""
    server = _named_server(monkeypatch)
    collided = await _call_tool(server).execute(_ctx(), {'tool': 'hasn.stub.named', 'params': {'slug': 's'}})
    assert 'name' in collided['how_to_fix'] and '工具' in collided['how_to_fix']

    plain = await _call_tool(server).execute(_ctx(), {'tool': 'hasn.stub.act', 'params': {}})
    assert 'params' in plain['how_to_fix']
    assert '会被当成' not in plain['how_to_fix']  # 反向：无 name 字段的工具不发撞键警告
