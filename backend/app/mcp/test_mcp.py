"""
云端 MCP Server 测试脚本

测试 MCP Server 的基本功能
"""
import asyncio
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.server import mcp_server


async def test_mcp_server() -> None:
    """测试 MCP Server"""
    print("=" * 60)
    print("云端 MCP Server 测试")
    print("=" * 60)

    # 测试工具注册
    print("\n1. 测试工具注册")
    print("-" * 60)
    all_tools = mcp_server.tool_registry.get_all_tools()
    print(f"已注册工具数量: {len(all_tools)}")
    for tool in all_tools:
        print(f"  - {tool.name}: {tool.description}")
        print(f"    所需权限: {tool.required_scopes}")

    # 测试工具获取
    print("\n2. 测试工具获取")
    print("-" * 60)
    message_tool = mcp_server.tool_registry.get_tool("hasn.message.send")
    if message_tool:
        print(f"✓ 成功获取工具: {message_tool.name}")
        print(f"  输入 Schema: {message_tool.input_schema}")
    else:
        print("✗ 工具获取失败")

    # 测试权限检查
    print("\n3. 测试权限检查")
    print("-" * 60)

    # 创建测试 AgentContext
    test_context = AgentContext(
        hasn_id="test_agent_001",
        owner_id=1,
        scopes=["message:write", "message:read", "contact:read"],
        agent_status="active",
        metadata={}
    )

    print(f"测试 Agent: {test_context.hasn_id}")
    print(f"权限范围: {test_context.scopes}")

    for tool in all_tools:
        has_permission = mcp_server._check_tool_permission(test_context, tool)
        status = "✓" if has_permission else "✗"
        print(f"  {status} {tool.name}: {has_permission}")

    # 测试命名空间过滤
    print("\n4. 测试命名空间过滤")
    print("-" * 60)
    message_tools = mcp_server.tool_registry.get_tools_by_namespace("hasn.message")
    print(f"hasn.message 命名空间工具数量: {len(message_tools)}")
    for tool in message_tools:
        print(f"  - {tool.name}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
