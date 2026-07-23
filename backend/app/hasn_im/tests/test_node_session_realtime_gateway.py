"""节点会话实时投递适配器的单元测试。"""

import pytest

from backend.app.hasn_im.adapters.routing.node_session_realtime_gateway import NodeSessionRealtimeGateway
from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame


class _FakeNodeSessionService:
    def __init__(self) -> None:
        self.owner_calls: list[tuple[str, dict]] = []
        self.node_calls: list[tuple[str, dict]] = []

    async def push_to_owner(self, owner_id: str, payload: dict) -> bool:
        self.owner_calls.append((owner_id, payload))
        return True

    async def push_to_node(self, node_id: str, payload: dict) -> bool:
        self.node_calls.append((node_id, payload))
        return True


@pytest.mark.asyncio
async def test_realtime_gateway_routes_owner_and_node_to_node_session_service(monkeypatch):
    """实时帧应由新节点会话服务投递，不留旧路由兼容分支。"""
    from backend.app.hasn_im.adapters.routing import node_session_realtime_gateway as module

    service = _FakeNodeSessionService()
    monkeypatch.setattr(module, 'node_session_service', service)
    gateway = NodeSessionRealtimeGateway()
    frame = RealtimeFrame(method='hasn.message.new', params={'message_id': 'm-1'})

    await gateway.push_to_owner('h_owner', frame)
    await gateway.push_to_node('node-1', frame)

    expected = {
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.new',
        'params': {'message_id': 'm-1'},
    }
    assert service.owner_calls == [('h_owner', expected)]
    assert service.node_calls == [('node-1', expected)]
