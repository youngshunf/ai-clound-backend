"""折叠心跳：`hasn.node.add_agent` 重认领帧携带 online_status 时，云端 WS handler
顺手把运行时健康写入持久列（复用 update_heartbeat），取代 per-agent HTTP /heartbeat。

锁住 `_handle_add_agent` 的折叠接线（不依赖真实 PG/Redis）：
- 注册被接受且帧带 online_status → 调 update_heartbeat（node_id 取自连接、user_id=None）。
- 帧不带 online_status（生命周期 claim，仅注册路由）→ 不写持久列。
- 注册被拒（accepted=False）→ 不写持久列。
- 健康写入抛错 → 吞掉并告警，不影响路由注册关键路径（仍发 add_agent_ack）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.app.hasn.api import ws_node
from backend.app.hasn.service import hasn_agents_service
from backend.app.hasn.service.ws_router import ws_router


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


@pytest.fixture
def patched(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())

    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(ws_node, 'async_db_session', fake_session)
    monkeypatch.setattr(ws_router, 'add_agent_presence', AsyncMock(return_value={'accepted': True}))
    monkeypatch.setattr(ws_router, 'get_offline_messages', AsyncMock(return_value=[]))
    update = AsyncMock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(hasn_agents_service.agent_profile_service, 'update_heartbeat', update)
    return update


@pytest.mark.asyncio
async def test_fold_persists_health_when_frame_carries_online_status(patched) -> None:
    ws = FakeWebSocket()
    await ws_node._handle_add_agent(
        ws,
        'n_node_X',
        {
            'agent_id': 'a_brain',
            'owner_id': 'h_owner',
            'online_status': 'online',
            'health_status': 'ok',
            'last_heartbeat_at': 1_700_000_500,
        },
        set(),
    )

    patched.assert_awaited_once()
    args, kwargs = patched.call_args
    # update_heartbeat(db, hasn_id, request, *, user_id=None)
    assert args[1] == 'a_brain'
    request = args[2]
    assert request.online_status == 'online'
    assert request.health_status == 'ok'
    assert request.last_heartbeat_at == 1_700_000_500
    assert request.node_id == 'n_node_X'  # 取自连接，非帧自报
    assert kwargs.get('user_id') is None
    # 路由注册关键路径仍照常 ack。
    assert any(f.get('method') == 'hasn.node.add_agent_ack' for f in ws.sent)


@pytest.mark.asyncio
async def test_lifecycle_claim_without_online_status_skips_persist(patched) -> None:
    ws = FakeWebSocket()
    await ws_node._handle_add_agent(
        ws, 'n_node_X', {'agent_id': 'a_brain', 'owner_id': 'h_owner'}, set()
    )
    patched.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_registration_skips_persist(monkeypatch, patched) -> None:
    monkeypatch.setattr(ws_router, 'add_agent_presence', AsyncMock(return_value={'accepted': False}))
    ws = FakeWebSocket()
    await ws_node._handle_add_agent(
        ws,
        'n_node_X',
        {'agent_id': 'a_brain', 'owner_id': 'h_owner', 'online_status': 'online'},
        set(),
    )
    patched.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_failure_does_not_break_registration(monkeypatch, patched) -> None:
    patched.side_effect = RuntimeError('db blip')
    ws = FakeWebSocket()
    # 不抛：健康写入失败被吞，路由 ack 仍发。
    await ws_node._handle_add_agent(
        ws,
        'n_node_X',
        {'agent_id': 'a_brain', 'owner_id': 'h_owner', 'online_status': 'online'},
        set(),
    )
    assert any(f.get('method') == 'hasn.node.add_agent_ack' for f in ws.sent)
