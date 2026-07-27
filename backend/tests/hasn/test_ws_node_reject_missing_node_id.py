"""C5 安全修复：WS 节点端点缺 X-Node-Id（且无 node_id query）→ 拒连 4001，不进认证。

事实源：Core/05 §5.1 node_id MUST 由设备指纹派生，服务端禁止用进程内地址凭空伪造。
旧实现缺 X-Node-Id 时回退 f'n_tmp_{id(websocket)}'，伪造的临时 node_id 被当真身份注册
节点 / 落入 binding 参与路由——本修复改为直接拒连。
"""
from __future__ import annotations

from typing import Any, NoReturn

import pytest

from backend.app.hasn_im.api import ws_node


class FakeWebSocket:
    """最小 WS 替身：headers/query_params 取值 + close 记录，足够覆盖 node_id 闸。"""

    def __init__(self, headers: dict[str, str], query: dict[str, str] | None = None) -> None:
        self.headers = headers
        self.query_params = query or {}
        self.closed_code: int | None = None
        self.closed_reason: str | None = None

    async def accept(self) -> None:
        pass

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_code = code
        self.closed_reason = reason


@pytest.mark.asyncio
async def test_reject_when_missing_node_id(monkeypatch) -> None:
    # 拒连必须发生在认证之前：若 authenticate 被触达则测试爆炸
    async def _boom(*a, **k) -> NoReturn:
        raise AssertionError('authenticate_ws_connection 不应在缺 node_id 时被调用')

    monkeypatch.setattr(ws_node, 'authenticate_ws_connection', _boom)

    ws: Any = FakeWebSocket(headers={'Authorization': 'Bearer xxx'})  # 有鉴权头但无 X-Node-Id
    await ws_node.hasn_node_websocket(ws)

    assert ws.closed_code == 4001
    assert 'X-Node-Id' in (ws.closed_reason or '')


@pytest.mark.asyncio
async def test_reject_when_missing_authorization() -> None:
    ws: Any = FakeWebSocket(headers={})  # 无 Authorization → 更早拒连
    await ws_node.hasn_node_websocket(ws)
    assert ws.closed_code == 4001


@pytest.mark.asyncio
async def test_node_id_via_query_param_passes_gate(monkeypatch) -> None:
    # node_id 经 query param 提供（无 X-Node-Id header）→ 过 node_id 闸，进入认证
    reached = {'auth': False}

    async def _auth(scheme, credentials, node_id, node_name) -> NoReturn:
        reached['auth'] = True
        assert node_id == 'n_via_query'
        raise RuntimeError('stop after gate')  # 提前终止，只验证已过 node_id 闸

    monkeypatch.setattr(ws_node, 'authenticate_ws_connection', _auth)

    ws: Any = FakeWebSocket(headers={'Authorization': 'Bearer xxx'}, query={'node_id': 'n_via_query'})
    await ws_node.hasn_node_websocket(ws)
    assert reached['auth'] is True  # node_id 闸放行了合法的 query 形态，未误杀
