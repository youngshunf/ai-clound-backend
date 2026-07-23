"""ws_node `_handle_send` context 透传契约测试（doc10 GS0）。

daemon 把群 @提及（mentions/mention_all）等随帧元数据放在 `params.context` 里，
`_handle_send` 必须整体透传给 `route_message`——群分支据此持久化 mentions 并随
envelope 扇出，是 mention_only 派发闸的权威数据载体。历史 bug：这里只摘 reply_to、
context 本体被丢弃，跨节点分身在 mention_only 群里永远收不到 @。本测试锁住透传契约。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


class _FakeWS:
    """只收集 send_json 出参的假 WebSocket（握手/路由不在本测试范围）。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_handle_send_passes_context_to_route_message(monkeypatch) -> None:
    from backend.app.hasn_im.api import ws_node

    captured: dict = {}

    async def _fake_route(**kwargs) -> dict:
        captured.update(kwargs)
        # deduped=True 让 _handle_send 回完 ACK 即返回，跳过多端同步分支
        return {'error': False, 'msg_id': 1001, 'conversation_id': 'c1', 'deduped': True}

    monkeypatch.setattr(ws_node.message_router, 'route_message', _fake_route)

    @asynccontextmanager
    async def _fake_session():
        yield object()

    monkeypatch.setattr(ws_node, 'async_db_session', _fake_session)

    ws = _FakeWS()
    ctx = {'mentions': ['a_peer'], 'mention_all': False, 'reply_to': None}
    await ws_node._handle_send(
        ws,
        node_id='node-1',
        params={
            'from_id': 'h_sender',
            'to': 'g:500001',
            'content': {'content_type': 'text', 'body': {'text': '@分身 在吗'}},
            'local_id': 'local-1',
            'context': ctx,
        },
        active_entities={'h_sender'},
    )

    # 核心断言：context 整体透传（不是只摘 reply_to）
    assert captured['context'] == ctx
    assert captured['from_id'] == 'h_sender'
    assert captured['to_target'] == 'g:500001'
    # ACK 正常回发
    assert ws.sent and ws.sent[0]['method'] == 'hasn.message.ack'
    assert ws.sent[0]['params']['msg_id'] == 1001


@pytest.mark.asyncio
async def test_handle_send_without_context_passes_none(monkeypatch) -> None:
    """不带 context 的帧照常路由，context 参数收敛为 None（与 route_message 默认一致）。"""
    from backend.app.hasn_im.api import ws_node

    captured: dict = {}

    async def _fake_route(**kwargs) -> dict:
        captured.update(kwargs)
        return {'error': False, 'msg_id': 1002, 'conversation_id': 'c2', 'deduped': True}

    monkeypatch.setattr(ws_node.message_router, 'route_message', _fake_route)

    @asynccontextmanager
    async def _fake_session():
        yield object()

    monkeypatch.setattr(ws_node, 'async_db_session', _fake_session)

    ws = _FakeWS()
    await ws_node._handle_send(
        ws,
        node_id='node-1',
        params={
            'from_id': 'h_sender',
            'to': 'h_peer',
            'content': '你好',
            'local_id': 'local-2',
        },
        active_entities={'h_sender'},
    )

    assert captured['context'] is None
    assert ws.sent and ws.sent[0]['method'] == 'hasn.message.ack'
