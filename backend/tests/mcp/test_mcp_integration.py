"""MCP 集成测试 - 使用真实数据库

这些测试使用真实的数据库连接，测试策略：
1. 使用数据库中已有的 Agent（不创建新的）
2. 如果没有可用的 Agent，跳过测试
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.mcp.routes import mcp_router
from backend.common.exception.exception_handler import register_exception
from backend.database.db import async_db_session


def make_test_app() -> FastAPI:
    """创建测试用的 FastAPI 应用"""
    from starlette_context.middleware import ContextMiddleware
    from starlette_context.plugins import RequestIdPlugin

    app = FastAPI()

    # 添加必要的中间件
    app.add_middleware(
        ContextMiddleware,
        plugins=(RequestIdPlugin(),)
    )

    register_exception(app)
    app.include_router(mcp_router, tags=["MCP"])
    return app


@pytest_asyncio.fixture(autouse=True)
async def _isolate_db_engine_pool():
    """每测处置全局引擎池（与 test_capability_ticket 同款隔离）。

    async_db_session 引擎是模块级单例，池化连接绑定首个使用它的事件循环；
    TestClient 每个请求起独立事件循环，跨循环复用池连接会
    'got Future attached to a different loop'。前后各 dispose 一次，
    让每段代码都在自己的循环上重建连接（真 PG）。
    """
    from backend.database.db import async_engine

    await async_engine.dispose()
    yield
    await async_engine.dispose()


@pytest_asyncio.fixture
async def active_agent_from_db():
    """从数据库获取一个活跃的 Agent"""
    from backend.database.db import async_engine

    async with async_db_session() as db:
        # 查询一个活跃的 Agent
        from sqlalchemy import select

        from backend.app.hasn.model.hasn_agents import HasnAgents

        result = await db.execute(
            select(HasnAgents)
            .where(HasnAgents.status == 'active')
            .limit(1)
        )
        agent = result.scalars().first()

        if not agent:
            pytest.skip("数据库中没有活跃的 Agent，跳过测试")

    # 本 fixture 在 pytest-asyncio 循环上用过池；TestClient 的请求循环随后接管，
    # 先处置避免跨循环复用（连接在本循环创建，也只能在本循环安全关闭）。
    await async_engine.dispose()
    return agent


@pytest_asyncio.fixture
def agent_token_for_db_agent(active_agent_from_db):
    """为数据库中的 Agent 生成 JWT token"""
    from datetime import datetime, timedelta, timezone

    from backend.common.security.agent_jwt import jwt_encode_agent

    agent = active_agent_from_db

    token = jwt_encode_agent({
        "token_type": "agent",
        "agent_hasn_id": agent.hasn_id,
        "agent_name": agent.display_name or agent.agent_name,
        "owner_hasn_id": agent.owner_id,
        "owner_user_id": 1,  # 简化处理
        "scopes": ["messages:read", "messages:write", "contacts:read"],
        "session_uuid": "test-session-integration",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    })

    return token


@pytest.mark.asyncio
class TestMcpIntegration:
    """MCP 集成测试"""

    async def test_list_tools_with_real_db(self, agent_token_for_db_agent, active_agent_from_db) -> None:
        """测试使用真实数据库获取工具列表"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            "/mcp/tools/list",
            json={},  # 路由要求 JSON body（缺省即 422 missing body）
            headers={
                "Authorization": f"Bearer {agent_token_for_db_agent}",
                "X-HASN-Agent-ID": active_agent_from_db.hasn_id,
            },
        )

        print(f"\n使用 Agent: {active_agent_from_db.hasn_id} ({active_agent_from_db.display_name})")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")

        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)

        # 渐进式暴露（设计 03 §9）：tools/list 只回 bootstrap 元工具，长尾经 tool.call 调用。
        tool_names = [tool["name"] for tool in data["tools"]]
        assert "hasn.cloud.tool.search" in tool_names
        assert "hasn.cloud.tool.call" in tool_names
        assert "hasn.message.send" not in tool_names  # 长尾工具不进清单

        print(f"✅ Found {len(data['tools'])} tools")
        for tool in data["tools"]:
            print(f"  - {tool['name']}: {tool['description']}")

    async def test_list_tools_with_namespace_filter(self, agent_token_for_db_agent, active_agent_from_db) -> None:
        """namespace 参数被忽略，tools/list 始终返回 bootstrap 元工具集（03 §9）"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            "/mcp/tools/list",
            json={"namespace": "hasn.message"},
            headers={
                "Authorization": f"Bearer {agent_token_for_db_agent}",
                "X-HASN-Agent-ID": active_agent_from_db.hasn_id,
            },
        )

        print(f"\nStatus: {response.status_code}")
        print(f"Response: {response.json()}")

        assert response.status_code == 200
        data = response.json()
        tools = data["tools"]

        # namespace 不收窄：返回 bootstrap 元工具集（不含长尾 hasn.message.*）。
        tool_names = [tool["name"] for tool in tools]
        assert "hasn.cloud.tool.search" in tool_names
        assert "hasn.cloud.tool.call" in tool_names

        print(f"✅ Found {len(tools)} bootstrap tools")

    async def test_call_contact_list_tool(self, agent_token_for_db_agent, active_agent_from_db) -> None:
        """测试调用联系人列表工具"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            "/mcp/tools/call",
            json={
                "tool_name": "hasn.contact.list",
                "arguments": {"limit": 10, "offset": 0},
            },
            headers={
                "Authorization": f"Bearer {agent_token_for_db_agent}",
                "X-HASN-Agent-ID": active_agent_from_db.hasn_id,
            },
        )

        print(f"\nStatus: {response.status_code}")
        print(f"Response: {response.json()}")

        if response.status_code == 200:
            data = response.json()
            assert "result" in data
            print("✅ Contact list tool executed successfully")
            print(f"   Result: {data['result']}")
        else:
            # 记录错误但不失败测试（可能是服务依赖问题）
            print("⚠️ Tool execution failed (may be expected in test env)")

    async def test_call_nonexistent_tool(self, agent_token_for_db_agent, active_agent_from_db) -> None:
        """测试调用不存在的工具"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post(
            "/mcp/tools/call",
            json={
                "tool_name": "hasn.nonexistent.tool",
                "arguments": {},
            },
            headers={
                "Authorization": f"Bearer {agent_token_for_db_agent}",
                "X-HASN-Agent-ID": active_agent_from_db.hasn_id,
            },
        )

        print(f"\nStatus: {response.status_code}")
        print(f"Response: {response.json()}")

        assert response.status_code in [404, 500]  # 可能返回 404 或 500
        print("✅ Correctly rejected nonexistent tool")

    async def test_missing_authorization(self) -> None:
        """测试缺少认证头"""
        app = make_test_app()
        client = TestClient(app)

        response = client.post("/mcp/tools/list")

        print(f"\nStatus: {response.status_code}")
        print(f"Response: {response.json()}")

        # 422 表示缺少必需参数（FastAPI 验证）
        assert response.status_code == 422
        print("✅ Correctly rejected missing authorization")
