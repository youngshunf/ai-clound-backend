"""StreamableHTTP MCP authentication contract tests."""
from __future__ import annotations

import sys
import types

from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.common.exception import errors
from backend.common.security.agent_jwt import jwt_encode_agent


class AsyncSessionContext:
    def __init__(self) -> None:
        self.session = MagicMock()

    async def __aenter__(self) -> MagicMock:
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


def streamable_agent_token() -> str:
    return jwt_encode_agent(
        {
            "token_type": "agent",
            "agent_hasn_id": "a_test_agent_001",
            "agent_name": "Test Agent",
            "owner_hasn_id": "h_test_owner_001",
            "owner_user_id": 1001,
            "scopes": ["knowledge.read"],
            "session_uuid": "test-session-streamable",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        }
    )


def install_mcp_sdk_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubServer:
        def __init__(self, _name: str) -> None:
            pass

        def list_tools(self):
            return lambda handler: handler

        def call_tool(self):
            return lambda handler: handler

    class StubStreamableHTTPSessionManager:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def handle_request(self, *_args, **_kwargs) -> None:
            return None

    class StubTool:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class StubTextContent:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    streamable_module = types.ModuleType("mcp.server.streamable_http_manager")
    types_module = types.ModuleType("mcp.types")

    setattr(server_module, 'Server', StubServer)
    setattr(streamable_module, 'StreamableHTTPSessionManager', StubStreamableHTTPSessionManager)
    setattr(types_module, 'Tool', StubTool)
    setattr(types_module, 'TextContent', StubTextContent)
    setattr(types_module, 'ImageContent', type("StubImageContent", (), {}))
    setattr(types_module, 'EmbeddedResource', type("StubEmbeddedResource", (), {}))
    setattr(mcp_module, 'types', types_module)

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.streamable_http_manager", streamable_module)
    monkeypatch.setitem(sys.modules, "mcp.types", types_module)
    sys.modules.pop("backend.app.mcp.streamable", None)


@pytest.mark.asyncio
async def test_streamable_rejects_revoked_agent_jwt_before_agent_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_mcp_sdk_stub(monkeypatch)
    from backend.app.mcp.streamable import HasnMcpStreamableServer

    async def reject_revoked_token(_token: str) -> NoReturn:
        raise errors.TokenError(msg="Agent Token 已过期或已被吊销")

    monkeypatch.setattr(
        "backend.app.mcp.streamable.verify_agent_token",
        reject_revoked_token,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.app.mcp.streamable.async_db_session",
        lambda: AsyncSessionContext(),
    )
    get_agent = AsyncMock()
    monkeypatch.setattr(
        "backend.app.mcp.streamable.hasn_agents_dao.get_by_hasn_id",
        get_agent,
    )

    server = HasnMcpStreamableServer()
    headers = {
        b"authorization": f"Bearer {streamable_agent_token()}".encode(),
        b"x-hasn-agent-id": b"a_test_agent_001",
    }

    try:
        with pytest.raises(ValueError, match="吊销"):
            await server._authenticate_from_headers(headers)
    finally:
        sys.modules.pop("backend.app.mcp.streamable", None)

    get_agent.assert_not_awaited()
