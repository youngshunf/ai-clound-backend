"""Canonical App Tool MCP contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.app.mcp.routes import mcp_router
from backend.app.mcp.tools.app_tools import AppTool
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt import jwt_encode_agent

if TYPE_CHECKING:
    from types import TracebackType


def make_test_app() -> FastAPI:
    from starlette_context.middleware import ContextMiddleware
    from starlette_context.plugins import RequestIdPlugin

    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
    register_exception(app)
    app.include_router(mcp_router, tags=['MCP'])
    return app


@pytest.fixture
def app_tool() -> AppTool:
    return AppTool(
        installation_id='appi_knowledge',
        app_id='knowledge',
        app_namespace='knowledge',
        tool_id='knowledge.search',
        tool_name='search',
        action='search',
        tool_description='Search workspace knowledge',
        tool_input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']},
        tool_output_schema={
            'type': 'object',
            'properties': {'documents': {'type': 'array'}},
        },
        tool_required_scopes=['knowledge.read'],
        risk_level='low',
    )


@pytest.fixture
def mock_agent() -> MagicMock:
    agent = MagicMock()
    agent.hasn_id = 'a_test_agent_app_tool'
    agent.display_name = 'App Tool Test Agent'
    agent.agent_name = 'app_tool_test_agent'
    agent.owner_id = '1001'
    agent.status = 'active'
    return agent


def agent_token(scopes: list[str]) -> str:
    return jwt_encode_agent({
        'token_type': 'agent',
        'agent_hasn_id': 'a_test_agent_app_tool',
        'agent_name': 'App Tool Test Agent',
        'owner_hasn_id': 'h_test_owner',
        'owner_user_id': 1001,
        'scopes': scopes,
        'session_uuid': 'test-session-app-tool',
        'exp': int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    })


def auth_headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}',
        'X-HASN-Agent-ID': 'a_test_agent_app_tool',
    }


class AsyncSessionContext:
    def __init__(self) -> None:
        # app_tools.execute 落审计后 await db.commit()（d1c33ab9 Bug7）——commit/rollback 须为可 await。
        self.session = MagicMock(commit=AsyncMock(), rollback=AsyncMock())

    async def __aenter__(self) -> MagicMock:
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class TestCanonicalAppTool:
    def test_tool_name_uses_app_namespace_not_installation_id(self, app_tool: AppTool) -> None:
        assert app_tool.name == 'hasn.knowledge.search'
        assert 'appi_knowledge' not in app_tool.name
        assert app_tool.required_scopes == ['knowledge.read']

    def test_search_discovers_canonical_app_tool_schema(self, app_tool: AppTool, mock_agent: MagicMock) -> None:
        app = make_test_app()
        client = TestClient(app)
        token = agent_token(['knowledge.read'])

        with (
            patch(
                'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
                new_callable=AsyncMock,
            ) as mock_get,
            patch(
                'backend.app.mcp.server.load_app_tools_for_agent',
                new_callable=AsyncMock,
            ) as mock_load_tools,
            patch(
                'backend.app.mcp.server.load_app_tools_for_owner',
                new_callable=AsyncMock,
            ) as mock_load_owner_tools,
            patch(
                'backend.app.mcp.server.HasnCloudMcpServer._log_tool_call',
                new_callable=AsyncMock,
            ),
        ):
            mock_get.return_value = mock_agent
            mock_load_tools.return_value = [app_tool]
            mock_load_owner_tools.return_value = []

            summary_response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.tool.search',
                    'arguments': {'query': 'app.knowledge'},
                },
                headers=auth_headers(token),
            )
            schema_response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.tool.search',
                    'arguments': {
                        'query': 'tool:hasn.knowledge.search',
                        'detail': 'schema',
                    },
                },
                headers=auth_headers(token),
            )

        assert summary_response.status_code == 200
        summary = summary_response.json()['result']['tools'][0]
        assert summary['source'] == 'app'
        assert summary['name'] == 'hasn.knowledge.search'
        assert summary['required_scopes'] == ['knowledge.read']
        assert summary['schema_ref'].startswith('hasn://tool-schema/hasn.knowledge.search@sha256:')

        assert schema_response.status_code == 200
        schema = schema_response.json()['result']['schemas'][0]
        assert schema['source'] == 'app'
        assert schema['name'] == 'hasn.knowledge.search'
        assert schema['input_schema']['type'] == 'object'
        assert schema['input_schema']['properties']['query']['type'] == 'string'
        assert schema['input_schema']['required'] == ['query']
        assert schema['output_schema']['properties']['documents']['type'] == 'array'

    def test_direct_call_routes_canonical_app_tool_through_runtime_gateway(
        self,
        app_tool: AppTool,
        mock_agent: MagicMock,
    ) -> None:
        app = make_test_app()
        client = TestClient(app)
        token = agent_token(['knowledge.read'])
        session_context = AsyncSessionContext()

        with (
            patch(
                'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
                new_callable=AsyncMock,
            ) as mock_get,
            patch(
                'backend.app.mcp.server.load_app_tools_for_agent',
                new_callable=AsyncMock,
            ) as mock_load_tools,
            patch(
                'backend.app.mcp.server.load_app_tools_for_owner',
                new_callable=AsyncMock,
            ) as mock_load_owner_tools,
            patch('backend.database.db.async_db_session', return_value=session_context),
            patch(
                'backend.app.hasn.service.ai_native_runtime_gateway.ai_native_runtime_gateway.call_tool',
                new_callable=AsyncMock,
            ) as mock_gateway_call,
            patch(
                'backend.app.mcp.server.HasnCloudMcpServer._log_tool_call',
                new_callable=AsyncMock,
            ),
        ):
            mock_get.return_value = mock_agent
            mock_load_tools.return_value = [app_tool]
            mock_load_owner_tools.return_value = []
            mock_gateway_call.return_value = {
                'success': True,
                'data': {'documents': [{'id': 'doc_1'}]},
            }

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.knowledge.search',
                    'arguments': {'query': 'runtime adapter'},
                },
                headers=auth_headers(token),
            )

        assert response.status_code == 200
        assert response.json()['result']['data']['documents'][0]['id'] == 'doc_1'
        mock_gateway_call.assert_awaited_once()
        call = mock_gateway_call.await_args
        assert call is not None
        args, kwargs = call
        assert args == (session_context.session,)
        assert kwargs['app_id'] == 'knowledge'
        assert kwargs['tool_id'] == 'knowledge.search'
        assert kwargs['body'].input == {'query': 'runtime adapter'}
        assert kwargs['body'].workspace == {'kind': 'personal'}
        assert kwargs['body'].trace_id.startswith('trace_')
        assert kwargs['request'].state.agent.agent_hasn_id == 'a_test_agent_app_tool'
        assert kwargs['request'].state.agent.owner_hasn_id == 'h_test_owner'
        assert kwargs['request'].state.agent.owner_user_id == 1001
        assert kwargs['request'].state.agent.session_uuid == 'test-session-app-tool'

    def test_revoked_agent_jwt_is_rejected_before_app_tool_gateway(
        self,
        app_tool: AppTool,
        mock_agent: MagicMock,
        monkeypatch: MonkeyPatch,
    ) -> None:
        async def reject_revoked_token(_token: str) -> NoReturn:
            raise errors.TokenError(msg='Agent Token 已过期或已被吊销')

        monkeypatch.setattr(
            'backend.app.mcp.auth.verify_agent_token',
            reject_revoked_token,
            raising=False,
        )

        app = make_test_app()
        client = TestClient(app)
        token = agent_token(['knowledge.read'])

        with (
            patch(
                'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
                new_callable=AsyncMock,
            ) as mock_get,
            patch(
                'backend.app.mcp.server.load_app_tools_for_agent',
                new_callable=AsyncMock,
            ) as mock_load_tools,
            patch(
                'backend.app.mcp.server.load_app_tools_for_owner',
                new_callable=AsyncMock,
            ) as mock_load_owner_tools,
            patch(
                'backend.app.hasn.service.ai_native_runtime_gateway.ai_native_runtime_gateway.call_tool',
                new_callable=AsyncMock,
            ) as mock_gateway_call,
        ):
            mock_get.return_value = mock_agent
            mock_load_tools.return_value = [app_tool]
            mock_load_owner_tools.return_value = []

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.knowledge.search',
                    'arguments': {'query': 'runtime adapter'},
                },
                headers=auth_headers(token),
            )

        assert response.status_code == 401
        message = response.json().get('detail') or response.json().get('msg') or ''
        assert '吊销' in message
        mock_gateway_call.assert_not_awaited()

    def test_direct_call_deny_mode_is_authorization_not_exposure(
        self,
        app_tool: AppTool,
        mock_agent: MagicMock,
    ) -> None:
        # 新模型（D1/D3）：App 工具默认 allow；owner 把它设 deny → 调用按「授权拒绝」(403)，
        # 而非「未暴露」失败。
        app = make_test_app()
        client = TestClient(app)
        token = agent_token([])

        deny_policy = {
            'scopes': [],
            'post_needs_review': False,
            'default_mode': 'allow',
            'capability_modes': {'hasn.knowledge.search': 'deny'},
        }

        with (
            patch(
                'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
                new_callable=AsyncMock,
            ) as mock_get,
            patch(
                'backend.app.mcp.auth.get_agent_scopes_cached',
                new=AsyncMock(return_value=deny_policy),
            ),
            patch(
                'backend.app.mcp.server.load_app_tools_for_agent',
                new_callable=AsyncMock,
            ) as mock_load_tools,
            patch(
                'backend.app.mcp.server.load_app_tools_for_owner',
                new_callable=AsyncMock,
            ) as mock_load_owner_tools,
            patch(
                'backend.app.mcp.server.HasnCloudMcpServer._log_tool_call',
                new_callable=AsyncMock,
            ),
        ):
            mock_get.return_value = mock_agent
            mock_load_tools.return_value = [app_tool]
            mock_load_owner_tools.return_value = []

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.knowledge.search',
                    'arguments': {'query': 'runtime adapter'},
                },
                headers=auth_headers(token),
            )

        assert response.status_code == 403
        message = response.json().get('detail') or response.json().get('msg') or ''
        assert 'denied' in message.lower()
        assert 'not exposed' not in message.lower()
