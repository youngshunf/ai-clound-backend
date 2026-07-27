"""MCP 端到端测试

测试 MCP Server 的完整功能：
1. JWT 认证
2. 工具列表
3. 工具调用
4. 权限控制
5. 错误处理
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.mcp.routes import mcp_router
from backend.app.mcp.auth import AgentContext
from backend.common.exception.exception_handler import register_exception


def make_test_app() -> FastAPI:
    """创建测试用的 FastAPI 应用"""
    from starlette_context.middleware import ContextMiddleware
    from starlette_context.plugins import RequestIdPlugin

    app = FastAPI()

    # 添加必要的中间件
    app.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))

    register_exception(app)

    # 注意：mcp_router 已经有 prefix="/mcp"，所以这里不要再加
    app.include_router(mcp_router, tags=['MCP'])
    return app


@pytest.fixture(autouse=True)
def isolate_dynamic_app_tools():
    """避免 e2e 触发真实的 App 工具加载和审计落库。"""
    with (
        patch(
            'backend.app.mcp.server.load_app_tools_for_agent',
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            'backend.app.mcp.server.load_app_tools_for_owner',
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            'backend.app.mcp.server.HasnCloudMcpServer._log_tool_call',
            new_callable=AsyncMock,
        ),
    ):
        yield


class TestMcpAuthentication:
    """测试 MCP 认证"""

    def test_missing_authorization_header(self) -> None:
        """测试缺少 Authorization header"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            '/mcp/tools/list',
            headers={'X-HASN-Agent-ID': 'a_test_agent_001'},
        )

        assert response.status_code == 422  # Missing required header

    def test_invalid_authorization_format(self) -> None:
        """测试无效的 Authorization 格式"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            '/mcp/tools/list',
            headers={
                'Authorization': 'InvalidFormat token123',
                'X-HASN-Agent-ID': 'a_test_agent_001',
            },
        )

        assert response.status_code == 401
        # 适配自定义错误响应格式
        json_response = response.json()
        assert 'msg' in json_response
        assert 'Invalid authorization header' in json_response['msg']

    def test_expired_token(self, test_agent_expired_token) -> None:
        """测试过期的 token"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            '/mcp/tools/list',
            headers={
                'Authorization': f'Bearer {test_agent_expired_token}',
                'X-HASN-Agent-ID': 'a_test_agent_003',
            },
        )

        assert response.status_code == 401

    def test_agent_id_mismatch(self, test_agent_token) -> None:
        """测试 Agent ID 不匹配"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            '/mcp/tools/list',
            json={},
            headers={
                'Authorization': f'Bearer {test_agent_token}',
                'X-HASN-Agent-ID': 'a_wrong_agent_id',  # 与 token 中的不匹配
            },
        )

        assert response.status_code == 401
        # 适配自定义错误响应格式
        json_response = response.json()
        assert 'msg' in json_response
        assert 'Agent ID mismatch' in json_response['msg']


class TestMcpToolsList:
    """测试工具列表接口"""

    @patch('backend.app.mcp.auth.async_db_session')
    def test_list_tools_success(self, mock_db_session, test_agent_token) -> None:
        """测试成功获取工具列表"""
        mock_agent = MagicMock()
        mock_agent.status = 'active'
        mock_agent.hasn_id = 'a_test_agent_001'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        app = make_test_app()
        client = TestClient(app)

        with patch(
            'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_agent

            response = client.post(
                '/mcp/tools/list',
                json={},
                headers={
                    'Authorization': f'Bearer {test_agent_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_001',
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert 'tools' in data
        assert isinstance(data['tools'], list)

        # 渐进式暴露（设计 03 §9）：tools/list 只回 bootstrap 元工具；长尾经 tool.call 调用。
        tool_names = [tool['name'] for tool in data['tools']]
        assert 'hasn.cloud.tool.search' in tool_names
        assert 'hasn.cloud.tool.call' in tool_names
        assert 'hasn.message.send' not in tool_names  # 长尾工具不进清单
        assert 'hasn.contact.list' not in tool_names

    @patch('backend.app.mcp.auth.async_db_session')
    def test_list_tools_ignores_namespace_filter(self, mock_db_session, test_agent_token) -> None:
        """namespace 参数被忽略，tools/list 始终返回 bootstrap 元工具集（03 §9）"""
        mock_agent = MagicMock()
        mock_agent.status = 'active'
        mock_agent.hasn_id = 'a_test_agent_001'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        app = make_test_app()
        client = TestClient(app)

        with patch(
            'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_agent

            response = client.post(
                '/mcp/tools/list',
                json={'namespace': 'hasn.message'},
                headers={
                    'Authorization': f'Bearer {test_agent_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_001',
                },
            )

        assert response.status_code == 200
        data = response.json()
        tools = data['tools']

        # 渐进式暴露（设计 03 §9）：namespace 不收窄；始终返回 bootstrap 元工具集（不含长尾）。
        tool_names = [tool['name'] for tool in tools]
        assert 'hasn.cloud.tool.search' in tool_names
        assert 'hasn.cloud.tool.call' in tool_names
        assert 'hasn.contact.list' not in tool_names

    @patch('backend.app.mcp.auth.async_db_session')
    def test_list_tools_inactive_agent(self, mock_db_session, test_agent_token) -> None:
        """测试非活跃 Agent 无法获取工具列表"""
        mock_agent = MagicMock()
        mock_agent.status = 'inactive'
        mock_agent.hasn_id = 'a_test_agent_001'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        app = make_test_app()
        client = TestClient(app)

        with patch(
            'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_agent

            response = client.post(
                '/mcp/tools/list',
                json={},
                headers={
                    'Authorization': f'Bearer {test_agent_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_001',
                },
            )

        assert response.status_code == 403
        assert 'Agent is inactive' in response.json()['msg']


class TestMcpToolsCall:
    """测试工具调用接口"""

    @patch('backend.app.mcp.auth.async_db_session')
    def test_call_tool_success(
        self,
        mock_db_session,
        test_agent_token,
    ) -> None:
        """测试成功调用工具"""
        mock_agent = MagicMock()
        mock_agent.status = 'active'
        mock_agent.hasn_id = 'a_test_agent_001'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        app = make_test_app()
        client = TestClient(app)

        # contact.list 走 hasn_contacts_dao.list_contacts + _resolve_peer（DAO 真实查询，零 service 层）。
        contact_row = MagicMock()
        contact_row.peer_id = 'h_contact_001'
        contact_row.peer_type = 'human'
        contact_row.relation_type = 'friend'
        contact_row.trust_level = 2
        contact_row.status = 'connected'

        with (
            patch(
                'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
                new_callable=AsyncMock,
            ) as mock_get,
            patch(
                'backend.app.mcp.tools.contact.async_db_session',
            ) as mock_contact_db_session,
            patch(
                'backend.app.mcp.tools.contact.hasn_contacts_dao.list_contacts',
                new_callable=AsyncMock,
            ) as mock_list_contacts,
            patch(
                'backend.app.mcp.tools.contact._resolve_peer',
                new_callable=AsyncMock,
            ) as mock_resolve_peer,
        ):
            mock_get.return_value = mock_agent
            mock_contact_db_session.return_value.__aenter__.return_value = mock_db
            mock_list_contacts.return_value = [contact_row]
            mock_resolve_peer.return_value = ('联系人甲', 'HX001')

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.contact.list',
                    'arguments': {'limit': 10},
                },
                headers={
                    'Authorization': f'Bearer {test_agent_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_001',
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert 'result' in data
        assert len(data['result']['contacts']) == 1
        assert data['result']['contacts'][0]['contact_hasn_id'] == 'h_contact_001'

    @patch('backend.app.mcp.auth.async_db_session')
    def test_call_tool_not_found(self, mock_db_session, test_agent_token) -> None:
        """测试调用不存在的工具"""
        mock_agent = MagicMock()
        mock_agent.status = 'active'
        mock_agent.hasn_id = 'a_test_agent_001'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        app = make_test_app()
        client = TestClient(app)

        with patch(
            'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_agent

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.nonexistent.tool',
                    'arguments': {},
                },
                headers={
                    'Authorization': f'Bearer {test_agent_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_001',
                },
            )

        assert response.status_code == 404
        message = response.json()['msg']
        assert 'Tool not found' in message
        # P2: unknown tool carries the stable MCP_9209 code, not a silent miss.
        assert 'MCP_9209' in message

    @patch('backend.app.mcp.auth.async_db_session')
    def test_call_tool_permission_denied(self, mock_db_session, test_agent_readonly_token) -> None:
        """测试 owner 把某能力设 deny → 调用被拒（D1/D3 新模型，默认全开，deny 才拒）。"""
        mock_agent = MagicMock()
        mock_agent.status = 'active'
        mock_agent.hasn_id = 'a_test_agent_002'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        deny_policy = {
            'scopes': [],
            'post_needs_review': False,
            'default_mode': 'allow',
            # 按工具 canonical 名 deny（当前 MessageSendTool 仍用旧 scope message:write，
            # 重命名为 message:send 在 P4；按工具名 deny 不依赖该重命名）。
            'capability_modes': {'hasn.message.send': 'deny'},
        }

        app = make_test_app()
        client = TestClient(app)

        with (
            patch(
                'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
                new_callable=AsyncMock,
            ) as mock_get,
            patch(
                'backend.app.mcp.auth.get_agent_scopes_cached',
                new=AsyncMock(return_value=deny_policy),
            ),
        ):
            mock_get.return_value = mock_agent

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.message.send',
                    'arguments': {
                        'to_hasn_id': 'h_recipient_001',
                        'content': 'Test message',
                    },
                },
                headers={
                    'Authorization': f'Bearer {test_agent_readonly_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_002',
                },
            )

        assert response.status_code == 403
        assert 'denied' in response.json()['msg'].lower()

    @patch('backend.app.mcp.auth.async_db_session')
    def test_call_tool_invalid_arguments(self, mock_db_session, test_agent_token) -> None:
        """测试无效的工具参数"""
        mock_agent = MagicMock()
        mock_agent.status = 'active'
        mock_agent.hasn_id = 'a_test_agent_001'

        mock_db = AsyncMock()
        mock_db_session.return_value.__aenter__.return_value = mock_db

        app = make_test_app()
        client = TestClient(app)

        with patch(
            'backend.app.mcp.auth.hasn_agents_dao.get_by_hasn_id',
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_agent

            response = client.post(
                '/mcp/tools/call',
                json={
                    'tool_name': 'hasn.message.send',
                    'arguments': {},  # 缺少 to_hasn_id 和 content
                },
                headers={
                    'Authorization': f'Bearer {test_agent_token}',
                    'X-HASN-Agent-ID': 'a_test_agent_001',
                },
            )

        assert response.status_code == 400
        assert 'Missing required' in response.json()['msg']


class TestMcpToolRegistry:
    """测试工具注册表"""

    def test_tool_registration(self) -> None:
        """测试工具注册"""
        from backend.app.mcp.tools.base import BaseTool
        from backend.app.mcp.tools.registry import ToolRegistry

        class TestTool(BaseTool):
            @property
            def name(self) -> str:
                return 'test.tool'

            @property
            def description(self) -> str:
                return 'Test tool'

            @property
            def input_schema(self) -> dict:
                return {'type': 'object', 'properties': {}}

            async def execute(
                self, agent_context: AgentContext, arguments: dict[str, Any]
            ) -> dict[str, Any]:
                return {'result': 'ok'}

        registry = ToolRegistry()
        tool = TestTool()
        registry.register(tool)

        assert registry.get_tool('test.tool') is not None
        assert len(registry.get_all_tools()) == 1

    def test_namespace_filtering(self) -> None:
        """测试命名空间过滤"""
        from backend.app.mcp.tools.base import BaseTool
        from backend.app.mcp.tools.registry import ToolRegistry

        class Tool1(BaseTool):
            @property
            def name(self) -> str:
                return 'ns1.tool1'

            @property
            def description(self) -> str:
                return 'Tool 1'

            @property
            def input_schema(self) -> dict:
                return {'type': 'object'}

            async def execute(
                self, agent_context: AgentContext, arguments: dict[str, Any]
            ) -> dict[str, Any]:
                return {}

        class Tool2(BaseTool):
            @property
            def name(self) -> str:
                return 'ns2.tool2'

            @property
            def description(self) -> str:
                return 'Tool 2'

            @property
            def input_schema(self) -> dict:
                return {'type': 'object'}

            async def execute(
                self, agent_context: AgentContext, arguments: dict[str, Any]
            ) -> dict[str, Any]:
                return {}

        registry = ToolRegistry()
        registry.register(Tool1())
        registry.register(Tool2())

        ns1_tools = registry.get_tools_by_namespace('ns1')
        assert len(ns1_tools) == 1
        assert ns1_tools[0].name == 'ns1.tool1'

        ns2_tools = registry.get_tools_by_namespace('ns2')
        assert len(ns2_tools) == 1
        assert ns2_tools[0].name == 'ns2.tool2'
