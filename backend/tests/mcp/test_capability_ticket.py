"""A-P2 — 一次性能力票据 + 验票跳闸（令牌重试，doc15 §3.3）。

真实联调（零 fake）：
- capability_ticket 模块：issue → consume（有效）→ 再 consume（jti 重放拒）→ 改 args/tool/agent 拒。
  jti 经真实 Redis SETNX 防重放。
- server 验票跳闸（cloud-pend 下）：带有效票→**真正执行**拿结果 + 落 consumed；同票再调→jti 已用
  →不跳闸、落回云端挂起（不执行）（E1 + E5）。无票分支由云端挂起处理，见 test_ask_gate_cloud_pend。
- grant_approval(always)：签票 + 把触发 ask 的 capability_keys 写回 capability_modes=allow（E3）。
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
from backend.app.mcp.ask_gate import _canonical_args_hash, ask_approval_gate
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.context import set_capability_ticket
from backend.app.mcp.tools.base import BaseTool
from backend.common.security.capability_ticket import consume_capability_ticket, issue_capability_ticket
from backend.database.db import async_db_session
from backend.database.redis import redis_client


class _EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return 'hasn.stub.act'

    @property
    def description(self) -> str:
        return 'echo tool for ticket tests'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {'content': {'type': 'string'}}}

    @property
    def required_scopes(self) -> list[str]:
        return ['stub:act']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {'echo': arguments}


def _ctx(agent_hasn_id: str, *, capability_modes: dict | None = None) -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_ticket_test',
        session_uuid=f'amk_{agent_hasn_id}',
        default_mode='allow',
        capability_modes=capability_modes or {},
    )


@pytest_asyncio.fixture(autouse=True)
async def _reset_db_engine_pool() -> Any:
    """清空全局 async 引擎 + Redis 连接池。

    pytest-asyncio 每个测试一个事件循环，而 async_db_session 引擎与全局 redis_client
    都是模块级单例、池化连接绑定首个使用它的循环——跨循环复用会 'attached to a different
    loop' / 'Event loop is closed'。每测 dispose 让两者都在本循环上重建连接（真 PG + 真 Redis）。
    """
    from backend.database.db import async_engine

    await async_engine.dispose()
    try:
        await redis_client.connection_pool.disconnect()
    except Exception:
        pass
    set_capability_ticket(None)
    yield
    set_capability_ticket(None)


async def _cleanup_requests(agent_hasn_id: str) -> None:
    async with async_db_session() as db:
        rows = await hasn_agent_approval_requests_dao.select_models(db, agent_hasn_id=agent_hasn_id)
    if rows:
        async with async_db_session.begin() as db:
            await hasn_agent_approval_requests_dao.delete(db, [r.id for r in rows])


# ── capability_ticket 模块（真实 Redis jti） ──────────────────────────────


@pytest.mark.asyncio
async def test_ticket_consume_valid_then_replay_rejected() -> None:
    args_hash = _canonical_args_hash({'content': 'hi'})
    ticket, jti = issue_capability_ticket(
        request_id='areq_t1', agent_hasn_id='a_tk_1', owner_hasn_id='h1',
        tool_name='hasn.stub.act', args_hash=args_hash,
    )
    try:
        claims = await consume_capability_ticket(ticket, agent_hasn_id='a_tk_1', tool_name='hasn.stub.act', args_hash=args_hash)
        assert claims is not None
        assert claims['request_id'] == 'areq_t1'
        # 二次消费同票 → jti 已认领 → 拒绝（重放）
        replay = await consume_capability_ticket(ticket, agent_hasn_id='a_tk_1', tool_name='hasn.stub.act', args_hash=args_hash)
        assert replay is None
    finally:
        await redis_client.delete(f'capability_ticket:used:{jti}')


@pytest.mark.asyncio
async def test_ticket_rejects_arg_or_identity_mismatch() -> None:
    args_hash = _canonical_args_hash({'content': 'hi'})
    ticket, jti = issue_capability_ticket(
        request_id='areq_t2', agent_hasn_id='a_tk_2', owner_hasn_id='h1',
        tool_name='hasn.stub.act', args_hash=args_hash,
    )
    try:
        # 换参（args_hash 不符）→ 拒
        assert await consume_capability_ticket(ticket, agent_hasn_id='a_tk_2', tool_name='hasn.stub.act', args_hash=_canonical_args_hash({'content': 'TAMPERED'})) is None
        # 换工具 → 拒
        assert await consume_capability_ticket(ticket, agent_hasn_id='a_tk_2', tool_name='hasn.other', args_hash=args_hash) is None
        # 换 agent（越权复用）→ 拒
        assert await consume_capability_ticket(ticket, agent_hasn_id='a_OTHER', tool_name='hasn.stub.act', args_hash=args_hash) is None
        # 乱码票 → 拒
        assert await consume_capability_ticket('not-a-jwt', agent_hasn_id='a_tk_2', tool_name='hasn.stub.act', args_hash=args_hash) is None
    finally:
        await redis_client.delete(f'capability_ticket:used:{jti}')


# ── server 验票跳闸（E1 token retry + E5 replay） ──────────────────────────


@pytest.mark.asyncio
async def test_token_retry_executes_then_replay_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """令牌重试仍有效 + 重放防护（cloud-pend 下的不变量）。

    cloud-pend（福仔 2026-06-08）下，无票分支不再回 approval_required 信封，而是云端自己挂起
    （发卡片+轮询裁决）；故本用例聚焦仍然成立的「带有效票一次性放行 + 同票重放被拒不执行」：
    - 无票待批准用 open_request 预置 pending（等价于主人将看到的待批准项）；
    - 重放分支（无有效票）落回 request_and_wait，用 monkeypatch 断言它**不执行**（落回挂起拒绝），不真发卡片。
    """
    from backend.app.mcp import ask_gate as ask_gate_module
    from backend.app.mcp.server import HasnCloudMcpServer

    server = HasnCloudMcpServer()
    server.tool_registry.register(_EchoTool())

    async def _noop(*args: object, **kwargs: object) -> None:  # noqa: RUF029
        return None

    monkeypatch.setattr(server, '_load_app_tools', _noop)
    monkeypatch.setattr(server, '_log_tool_call', _noop)

    # 重放（无有效票）落到云端挂起：这里 stub 成 denied，断言“同票重放不再执行”，不真发卡片。
    async def _pend_denied(**kwargs: object) -> dict[str, Any]:  # noqa: RUF029
        return {'decision': 'denied', 'request_id': 'areq_replay_pend', 'description': 'x'}

    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'request_and_wait', _pend_denied)

    agent_hasn_id = 'a_retry_001'
    ctx = _ctx(agent_hasn_id, capability_modes={'stub:act': 'ask'})
    args = {'content': 'hi'}
    jti_holder: dict[str, str] = {}
    try:
        # 1) 开一条 ask 审批请求（落 pending，拿 request_id）——等价于主人将看到的待批准项
        set_capability_ticket(None)
        opened = await ask_approval_gate.open_request(
            agent_hasn_id=agent_hasn_id, owner_hasn_id='h_ticket_test', tool_name='hasn.stub.act',
            required_scopes=['stub:act'], default_mode='allow', capability_modes={'stub:act': 'ask'},
            arguments=args,
        )
        request_id = opened['approval']['request_id']

        # 2) 主人 grant → 签一次性票（绑定本次 agent/tool/args_hash）
        ticket, jti = issue_capability_ticket(
            request_id=request_id, agent_hasn_id=agent_hasn_id, owner_hasn_id='h_ticket_test',
            tool_name='hasn.stub.act', args_hash=_canonical_args_hash(args),
        )
        jti_holder['jti'] = jti

        # 3) 带票重试 → 跳闸真正执行
        set_capability_ticket(ticket)
        r2 = await server.call_tool(ctx, 'hasn.stub.act', args)
        assert r2 == {'echo': args}

        # request 落 consumed
        async with async_db_session() as db:
            row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
        assert row.status == 'consumed'

        # 4) 同票再调 → jti 已用 → 不跳闸 → 落回云端挂起（stub 成 denied）→ **不执行**
        r3 = await server.call_tool(ctx, 'hasn.stub.act', args)
        assert r3['error'] == 'approval_denied'  # 落回挂起，而非带票执行
        assert r3 != {'echo': args}
    finally:
        set_capability_ticket(None)
        if 'jti' in jti_holder:
            await redis_client.delete(f"capability_ticket:used:{jti_holder['jti']}")
        await _cleanup_requests(agent_hasn_id)


# ── grant_approval(always) 写回（E3） ─────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_always_issues_ticket_and_writes_back_allow() -> None:
    from sqlalchemy import text

    from backend.app.hasn.model import HasnAgents
    from backend.app.hasn.service.agent_scopes_service import agent_scopes_service
    from backend.common.security.agent_jwt import create_default_agent_scopes, get_agent_scopes_from_db

    owner_hasn_id = 'h_grant_owner_001'
    agent_hasn_id = 'a_grant_001'
    try:
        # 建 agent + 默认 scopes（真实 DB）
        async with async_db_session.begin() as db:
            db.add(HasnAgents(hasn_id=agent_hasn_id, owner_id=owner_hasn_id, agent_name='grant_t', display_name='Grant T', star_id='999900001'))  # 唯一测试 star_id：免撞 star_id 唯一约束的脏库残留
        async with async_db_session() as db:
            await create_default_agent_scopes(db, agent_hasn_id, owner_hasn_id)

        # 开一条 ask 审批请求（capability_keys=['stub:act']）
        opened = await ask_approval_gate.open_request(
            agent_hasn_id=agent_hasn_id, owner_hasn_id=owner_hasn_id, tool_name='hasn.stub.act',
            required_scopes=['stub:act'], default_mode='allow', capability_modes={'stub:act': 'ask'},
            arguments={'content': 'hi'},
        )
        request_id = opened['approval']['request_id']

        # grant always → 签票 + 写回 allow
        async with async_db_session() as db:
            result = await agent_scopes_service.grant_approval(
                db=db, agent_hasn_id=agent_hasn_id, owner_hasn_id=owner_hasn_id, request_id=request_id, scope='always',
            )
        assert result['grant_scope'] == 'always'
        assert result['capability_ticket']

        # capability_modes 写回 allow
        async with async_db_session() as db:
            policy = await get_agent_scopes_from_db(db, agent_hasn_id)
            row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
        assert policy['capability_modes'].get('stub:act') == 'allow'
        assert row.status == 'approved'
        assert row.grant_scope == 'always'
    finally:
        await _cleanup_requests(agent_hasn_id)
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_agent_scopes WHERE agent_hasn_id = :a'), {'a': agent_hasn_id})
            await db.execute(text('DELETE FROM hasn_agents WHERE hasn_id = :a'), {'a': agent_hasn_id})
