"""会话轴分流（设计 02 §4.3）云端提取点守卫（真实本地 PostgreSQL，零 mock 收窄查询）。

守三条不变量：

1. `trust_gate.pop_work_session_id` 把系统注入的 `_hasn_work_session_id` 从入参剥离（工具体永不见）。
2. `coalesce_legacy_work_session_id` 的「在册 task 收窄」判据：只有确实在册的工作会话（kind=task）
   才回落；interactive/查无一律 None（IM 主会话 runtime id 绝不落工作会话轴）。
3. `server.call_tool` 工作会话 ContextVar 的三级权威：auth 绑定（CLI header）> `_hasn_work_session_id`
   保留参数（Hermes 盖章）> 旧节点 `session_id` 收窄；且只进不退（重入不覆写）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnSessions
from backend.app.hasn.service.hasn_artifacts_service import coalesce_legacy_work_session_id
from backend.app.mcp import trust_gate
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.context import (
    clear_current_work_session_id,
    get_current_work_session_id,
    set_current_work_session_id,
)
from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio(loop_scope='session')


@pytest_asyncio.fixture
async def pg_session():
    """真实本地 PG AsyncSession：flush 不 commit，结束 rollback（PG 侧不留残留）。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


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


# ─── 在册 task 收窄（真实 PG）───


async def _make_session(db, *, owner_hasn_id: str, agent_hasn_id: str, session_id: str, kind: str) -> None:
    """种一条 hasn_sessions 会话行（kind=task 是工作会话，interactive 是主会话逻辑会话）。"""
    db.add(
        HasnSessions(
            session_id=session_id,
            owner_id=owner_hasn_id,
            hasn_id=agent_hasn_id,
            session_kind=kind,
            session_scope='summary_only',
            session_status='active',
            origin_type='app' if kind == 'task' else 'ui',
        )
    )
    await db.flush()


async def test_coalesce_legacy_work_session_id_narrowing(pg_session) -> None:
    tag = uuid.uuid4().hex[:12]
    owner = f'h_axis_{tag}'
    task_id = f'sess_task_{tag}'
    interactive_id = f'sess_im_{tag}'
    await _make_session(pg_session, owner_hasn_id=owner, agent_hasn_id=f'a_{tag}', session_id=task_id, kind='task')
    await _make_session(
        pg_session, owner_hasn_id=owner, agent_hasn_id=f'a_{tag}', session_id=interactive_id, kind='interactive'
    )

    assert await coalesce_legacy_work_session_id(pg_session, owner_hasn_id=owner, session_id=task_id) == task_id, (
        '在册 task 行必须收窄命中（旧节点工作会话派发零断裂）'
    )
    assert await coalesce_legacy_work_session_id(pg_session, owner_hasn_id=owner, session_id=interactive_id) is None, (
        'interactive 行不是工作会话，绝不落工作会话轴'
    )
    assert await coalesce_legacy_work_session_id(pg_session, owner_hasn_id=owner, session_id=f'sess_ghost_{tag}') is None, (
        '查无的行（runtime 值）绝不落工作会话轴'
    )
    # 他人 owner 的同 id 行不得命中（owner 隔离）。
    assert await coalesce_legacy_work_session_id(pg_session, owner_hasn_id=f'h_other_{tag}', session_id=task_id) is None


# ─── server.call_tool 三级权威（真实 PG 收窄 + stub 捕获 ContextVar）───


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


class _SharedSessionCtx:
    """把测试 session 共享给 call_tool 内部自开的 `async_db_session()`（退出不 close）。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


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


async def test_call_tool_legacy_session_id_narrowed_to_registered_task(
    monkeypatch: pytest.MonkeyPatch, pg_session
) -> None:
    """旧节点（只发混合语义 `_hasn_session_id`）：在册 task 值收窄落轴，runtime 值不落。"""
    tag = uuid.uuid4().hex[:12]
    task_id = f'sess_task_{tag}'
    runtime_id = f'sess_im_{tag}'
    await _make_session(pg_session, owner_hasn_id=f'h_axis_{tag}', agent_hasn_id=f'a_axis_{tag}', session_id=task_id, kind='task')
    monkeypatch.setattr('backend.database.db.async_db_session', lambda: _SharedSessionCtx(pg_session))

    sink: list[str | None] = []
    server = _server(monkeypatch, sink)
    ctx = _ctx(tag)
    try:
        # 旧节点工作会话派发：`_hasn_session_id` 值本就是在册 task 行 → 收窄落轴（零断裂）。
        await server.call_tool(ctx, 'hasn.axis.rec', {trust_gate.RESERVED_SESSION_ID: task_id})
        assert sink == [task_id], '旧节点在册工作会话必须经收窄回落，不丢会话归属'
    finally:
        clear_current_work_session_id()

    sink.clear()
    ctx2 = _ctx(tag)
    try:
        # 旧节点 IM 主会话派发：runtime 值查无 → 不落轴（此前会被直接落列污染工作会话轴）。
        await server.call_tool(ctx2, 'hasn.axis.rec', {trust_gate.RESERVED_SESSION_ID: runtime_id})
        assert sink == [None], 'runtime 会话 id 绝不落工作会话轴'
        assert ctx2.session_id == runtime_id, 'runtime id 仍落 session_id 轴（回灌定位不受影响）'
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
