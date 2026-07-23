"""节点会话实现归属测试。"""

import pytest

from backend.app.hasn_im.application.node_session_service import (
    NodeSessionService,
    node_session_service,
)


def test_node_session_service_is_owned_by_hasn_im() -> None:
    """WS 生命周期与 Presence 写入必须由 hasn_im 应用层直接拥有。"""
    assert node_session_service.__class__ is NodeSessionService
    assert NodeSessionService.__module__ == __name__.replace('tests.test_node_session_service', 'application.node_session_service')


@pytest.mark.asyncio
async def test_offline_claim_failure_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """离线消息读取失败必须传播，不能伪装成空消息列表。"""
    from backend.app.hasn_im.application.ws_node_runtime import ws_node_runtime

    async def raise_loop_error(_entity_ids: list[str]) -> tuple[list[dict], dict[str, list[str]]]:
        raise RuntimeError('Event loop is closed')

    monkeypatch.setattr(node_session_service, 'claim_offline_messages', raise_loop_error)

    with pytest.raises(RuntimeError, match='Event loop is closed'):
        await ws_node_runtime.claim_offline_messages(['h_owner'])


def test_provider_returns_native_node_session_service() -> None:
    """业务 port 必须直接取得节点会话服务，不经协议运行时中转。"""
    from backend.app.hasn_im.application.provider import get_node_session_gateway

    assert get_node_session_gateway() is node_session_service
