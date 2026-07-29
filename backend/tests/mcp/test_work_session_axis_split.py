"""会话轴分流（设计 02 §4.3）云端提取点守卫（真实本地 PostgreSQL 不需要——收窄查询已退役）。

守三条不变量：

1. `trust_gate.pop_work_session_id` 把系统注入的 `_hasn_work_session_id` 从入参剥离（工具体永不见）。
2. 混合语义 `_hasn_session_id` **绝不**落工作会话轴（P2-8d 起旧「在册 task 收窄」回落退役：
   即使值恰好是在册 task 会话 id 也不回填——工作会话轴只认两级显式权威，运行时/逻辑会话 id
   只落 `AgentContext.session_id` 溯源轴）。
3. `server.call_tool` 工作会话 ContextVar 的两级权威：auth 绑定（CLI header）> `_hasn_work_session_id`
   保留参数（Hermes 盖章）；且只进不退（重入不覆写）。
"""

from __future__ import annotations

import uuid

from typing import Any

import pytest

from backend.app.mcp import trust_gate
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.context import (
    clear_current_work_session_id,
    get_current_work_session_id,
    set_current_work_session_id,
)
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tools.base import BaseTool

pytestmark = pytest.mark.asyncio(loop_scope='session')


# ─── pop_work_session_id 纯函数 ───


def test_pop_work_session_id_strips_reserved_arg() -> None:
    cleaned, sid = trust_gate.pop_work_session_id(
        {'to': 'h_peer', trust_gate.RESERVED_WORK_SESSION_ID: 'sess_work_1'}
    )
    assert cleaned == {'to': 'h_peer'}, '保留参数必须剥离，工具体不该见到它'
    assert sid == 'sess_work_1'


def test_pop_work_session_id_absent_or_blank_is_none() -> None:
    args, sid = trust_gate.pop_work_session_id({'to': 'h_peer'})
    assert args == {'to': 'h_peer'} and sid is None
    _, sid = trust_gate.pop_work_session_id({trust_gate.RESERVED_WORK_SESSION_ID: '   '})
    assert sid is None, '空白工作会话 id 按缺省处理（never over-block）'


# ─── server.call_tool 两级权威（stub 捕获 ContextVar）───


class _CtxVarRecorderTool(BaseTool):
    """stub 业务工具：execute 内捕获工作会话 ContextVar（call_tool finally 会清，只能在执行内读）。"""

    def __init__(self, sink: list[str | None]) -> None:
        self._sink = sink

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.axis.rec'

    @property
    def description(self) -> str:
        return 'recorder for work-session axis tests'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}, 'additionalProperties': True}

    @property
    def required_scopes(self) -> list[str]:
        return ['axis:rec']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        self._sink.append(get_current_work_session_id())
        return {'ok': True, 'args': arguments}


def _ctx(tag: str) -> AgentContext:
    return AgentContext(
        hasn_id=f'a_axis_{tag}',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id=f'h_axis_{tag}',
        session_uuid=f'amk_axis_{tag}',
        default_mode='allow',
        capability_modes={},
    )


def _server(monkeypatch: pytest.MonkeyPatch, sink: list[str | None]) -> HasnCloudMcpServer:
    server = HasnCloudMcpServer()
    server.tool_registry.register(_CtxVarRecorderTool(sink))

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_load_external_mcp_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)
    return server


async def test_call_tool_prefers_auth_bound_work_session_over_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 直连面：streamable 已把 `X-Hasn-Work-Session-Id` 落 auth 绑定 → 优先于 Hermes 盖章。"""
    sink: list[str | None] = []
    server = _server(monkeypatch, sink)
    ctx = _ctx(uuid.uuid4().hex[:12])
    ctx.work_session_id = 'sess_bound_ws'
    try:
        await server.call_tool(
            ctx,
            'hasn.axis.rec',
            {trust_gate.RESERVED_WORK_SESSION_ID: 'sess_stamped_ws', trust_gate.RESERVED_SESSION_ID: 'sess_rt'},
        )
        assert sink == ['sess_bound_ws'], 'auth 绑定的工作会话必须优先于保留参数盖章'
    finally:
        clear_current_work_session_id()


async def test_call_tool_adopts_stamped_work_session_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermes 面：无 auth 绑定时 `_hasn_work_session_id` 盖章直接采信，且两枚会话键都被剥离。"""
    sink: list[str | None] = []
    server = _server(monkeypatch, sink)
    ctx = _ctx(uuid.uuid4().hex[:12])
    try:
        result = await server.call_tool(
            ctx,
            'hasn.axis.rec',
            {trust_gate.RESERVED_WORK_SESSION_ID: 'sess_stamped_ws', trust_gate.RESERVED_SESSION_ID: 'sess_rt'},
        )
        assert sink == ['sess_stamped_ws']
        assert result['args'] == {}, '两枚会话保留参数都必须剥离，不得泄漏给业务工具'
        assert ctx.session_id == 'sess_rt', '运行时会话 id 落 session_id 轴（message.send 回灌消费）'
    finally:
        clear_current_work_session_id()


async def test_call_tool_legacy_session_id_never_lands_on_work_session_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合语义 `_hasn_session_id` 绝不落工作会话轴（P2-8d 收窄回落退役）。

    退役前：值恰好是在册 task 会话 id 时会经「在册收窄」回填 ContextVar（兼容只发混合值的
    旧节点）。退役后：工作会话轴只认两级显式权威，`_hasn_session_id` 无论取值是什么都只落
    `AgentContext.session_id` 运行时轴——新写点再想图省事走 session_id 通道绑会话，绑不上。
    """
    sink: list[str | None] = []
    server = _server(monkeypatch, sink)
    ctx = _ctx(uuid.uuid4().hex[:12])
    try:
        # 形如在册工作会话 id 的值也不回填——收窄查询已不存在，无从命中。
        await server.call_tool(ctx, 'hasn.axis.rec', {trust_gate.RESERVED_SESSION_ID: 'sess_task_looks_registered'})
        assert sink == [None], '混合语义 session_id 绝不落工作会话轴（收窄回落已退役）'
        assert ctx.session_id == 'sess_task_looks_registered', '运行时轴照常沉淀（回灌定位不受影响）'
    finally:
        clear_current_work_session_id()


async def test_call_tool_work_session_contextvar_only_moves_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """只进不退：ContextVar 已落非 None 时，内层无 stamp 的重入调用绝不覆写外层会话归属。"""
    sink: list[str | None] = []
    server = _server(monkeypatch, sink)
    ctx = _ctx(uuid.uuid4().hex[:12])
    set_current_work_session_id('sess_outer_ws')
    try:
        await server.call_tool(ctx, 'hasn.axis.rec', {})
        assert sink == ['sess_outer_ws'], '重入无 stamp 不得把外层已落的会话归属清掉'
    finally:
        clear_current_work_session_id()
