"""P6 / doc15 §3 — ask 态批准闸门单测（令牌重试模型，A-P1）。

覆盖：
- open_request() 自身（真实 PG）：落一条 pending 行 + 返回 approval_required 信封；
  args 脱敏；capability_keys 取实际触发 ask 的 key。
- server.call_tool 接线（云端挂起 cloud-pend）：mode=ask→挂起轮询裁决，拒绝/超时回**工具错误**、
  批准放行真执行（工具体审批前不执行，对 agent 透明、不回 approval_required）；mode=allow→直接执行
  （不开审批）；mode=deny→PermissionError。即便 risk=high，allow 也不开审批。

真实联调：open_request 落 `hasn_agent_approval_requests`（真 PG），测试后清理。
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
from backend.app.mcp.ask_gate import _canonical_args_hash, ask_approval_gate
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session


class _StubTool(BaseTool):
    def __init__(self, *, risk: str = 'low') -> None:
        self._risk = risk
        self.executed = False

    @property
    def name(self) -> str:
        return 'hasn.stub.act'

    @property
    def description(self) -> str:
        return 'stub tool for ask-gate tests'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    @property
    def required_scopes(self) -> list[str]:
        return ['stub:act']

    @property
    def risk_level(self) -> str:
        return self._risk

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        self.executed = True
        return {'executed': True}


def _ctx(*, default_mode: str = 'allow', capability_modes: dict | None = None) -> AgentContext:
    return AgentContext(
        hasn_id='a_ask_test',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_ask_test',
        session_uuid='amk_ask_test',
        default_mode=default_mode,
        capability_modes=capability_modes or {},
    )


@pytest_asyncio.fixture(autouse=True)
async def _reset_db_engine_pool() -> Any:
    """每个用例前清空全局 async 引擎连接池。

    pytest-asyncio 每个测试一个事件循环，而 async_db_session 引擎是模块级全局，
    跨循环复用池化连接会触发 'attached to a different loop'。dispose 让每测拿到
    本循环上的新连接（真 PG，零 fake）。
    """
    from backend.database.db import async_engine

    await async_engine.dispose()
    yield


async def _delete_request(request_id: str) -> None:
    async with async_db_session() as db:
        row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
    if row is not None:
        async with async_db_session.begin() as db:
            await hasn_agent_approval_requests_dao.delete(db, [row.id])


@pytest.mark.asyncio
async def test_open_request_persists_pending_and_returns_envelope() -> None:
    """mode=ask → 返回 MCP_9215 信封 + 落 pending 行；敏感参数脱敏；capability_keys 准确。"""
    result = await ask_approval_gate.open_request(
        agent_hasn_id='a_ask_open_001',
        owner_hasn_id='h_ask_open_001',
        tool_name='hasn.stub.act',
        required_scopes=['stub:act'],
        default_mode='allow',
        capability_modes={'stub:act': 'ask'},
        arguments={'target': '供应商B', 'api_token': 'sk-supersecret-xyz'},
    )
    request_id = result['approval']['request_id']
    try:
        # 信封形状（透传安全：作为工具结果，非异常）
        assert result['ok'] is False
        assert result['error'] == 'approval_required'
        assert result['code'] == McpErrorCode.APPROVAL_REQUIRED.value == 'MCP_9215'
        assert request_id.startswith('areq_')
        assert result['approval']['expires_in'] == 600
        # 敏感参数已脱敏，普通参数保留
        digest = result['approval']['args_digest']
        assert digest['api_token'] == '***'
        assert digest['target'] == '供应商B'

        # DB 落了一条 pending 行
        async with async_db_session() as db:
            row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
        assert row is not None
        assert row.status == 'pending'
        assert row.tool_name == 'hasn.stub.act'
        assert row.capability_keys == ['stub:act']  # 实际触发 ask 的 key
        assert row.args_hash == _canonical_args_hash({'target': '供应商B', 'api_token': 'sk-supersecret-xyz'})
        assert row.args_digest['api_token'] == '***'  # 落库也脱敏
    finally:
        await _delete_request(request_id)


@pytest.mark.asyncio
async def test_call_tool_ask_mode_denied_does_not_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """mode=ask + 主人拒绝（云端挂起 cloud-pend）：call_tool 回工具错误 approval_denied，工具体**未执行**。

    且绝不把 approval_required 信封透给 agent——agent 对 ask 过程无感（截图事故的根因修复）。
    """
    from backend.app.mcp import ask_gate as ask_gate_module
    from backend.app.mcp.server import HasnCloudMcpServer

    server = HasnCloudMcpServer()
    tool = _StubTool()
    server.tool_registry.register(tool)

    async def _noop_load(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop_load)
    monkeypatch.setattr(server, '_log_tool_call', _noop_load)

    async def _deny(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        return {'decision': 'denied', 'request_id': 'areq_deny', 'description': 'x'}

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'request_and_wait', _deny)

    ctx = _ctx(default_mode='allow', capability_modes={'stub:act': 'ask'})
    result = await server.call_tool(ctx, 'hasn.stub.act', {'x': 1})
    assert result['ok'] is False
    assert result['error'] == 'approval_denied'
    assert result.get('error') != 'approval_required'  # 不向 agent 暴露审批
    assert 'approval' not in result and 'request_id' not in result
    assert tool.executed is False  # 工具体绝不在审批前执行


@pytest.mark.asyncio
async def test_call_tool_ask_mode_approved_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """mode=ask + 主人批准（云端挂起 cloud-pend）：call_tool 落到工具体**真执行**（批准放行）。"""
    from backend.app.mcp import ask_gate as ask_gate_module
    from backend.app.mcp.server import HasnCloudMcpServer

    server = HasnCloudMcpServer()
    tool = _StubTool()
    server.tool_registry.register(tool)

    async def _noop_load(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop_load)
    monkeypatch.setattr(server, '_log_tool_call', _noop_load)

    async def _approve(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        return {'decision': 'approved', 'request_id': 'areq_ok', 'description': 'x'}

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'request_and_wait', _approve)

    ctx = _ctx(default_mode='allow', capability_modes={'stub:act': 'ask'})
    result = await server.call_tool(ctx, 'hasn.stub.act', {'x': 1})
    assert result == {'executed': True}
    assert tool.executed is True


@pytest.mark.asyncio
@pytest.mark.parametrize('risk', ['low', 'high'])
async def test_call_tool_allow_mode_never_opens_request(monkeypatch: pytest.MonkeyPatch, risk: str) -> None:
    """mode=allow 直接执行、不开审批；high risk 但 allow 也不开（D4：非 risk 强制）。"""
    from backend.app.mcp import ask_gate as ask_gate_module
    from backend.app.mcp.server import HasnCloudMcpServer

    server = HasnCloudMcpServer()
    server.tool_registry.register(_StubTool(risk=risk))

    async def _noop_load(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop_load)
    monkeypatch.setattr(server, '_log_tool_call', _noop_load)

    async def _must_not_open(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        raise AssertionError('allow 模式不应开审批请求')

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'open_request', _must_not_open)

    ctx = _ctx(default_mode='allow', capability_modes={})  # 全 allow
    result = await server.call_tool(ctx, 'hasn.stub.act', {})
    assert result == {'executed': True}


@pytest.mark.asyncio
async def test_call_tool_deny_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """mode=deny：直接 PermissionError，不开审批、不执行。"""
    from backend.app.mcp.server import HasnCloudMcpServer

    server = HasnCloudMcpServer()
    server.tool_registry.register(_StubTool())

    async def _noop_load(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop_load)
    monkeypatch.setattr(server, '_log_tool_call', _noop_load)

    ctx = _ctx(default_mode='allow', capability_modes={'stub:act': 'deny'})
    with pytest.raises(PermissionError):
        await server.call_tool(ctx, 'hasn.stub.act', {})
