"""HASN 核心迁移检查

检查当前代码是否已经收敛到 Node / Owner Binding / Agent Presence 模型。
这是一个轻量级结构测试，不依赖外部服务。
"""

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


async def run_tests():
    results = []

    def ok(name) -> None:
        results.append(("✅", name))
        print(f"  ✅ {name}")

    def fail(name, e) -> None:
        results.append(("❌", name, str(e)))
        print(f"  ❌ {name}: {e}")

    print("\n🔐 模块1: hasn_auth / 凭据模型")
    print("─" * 50)

    try:
        from backend.app.hasn.service.hasn_auth import (
            _generate_agent_key,
            _generate_node_key,
            _generate_owner_key,
            issue_node_jwt,
            verify_node_key,
            verify_owner_api_key,
            verify_owner_proof,
        )
        ok("认证服务导入成功")
    except Exception as e:
        fail("认证服务导入", e)
        return results

    try:
        agent_key, _ = _generate_agent_key()
        node_key, _ = _generate_node_key()
        owner_key, _ = _generate_owner_key()
        assert agent_key.startswith("hasn_ak_")
        assert node_key.startswith("hasn_nk_")
        assert owner_key.startswith("hasn_ok_")
        ok("Agent/Node/Owner 三类凭据前缀正确")
    except Exception as e:
        fail("凭据前缀检查", e)

    try:
        assert callable(issue_node_jwt)
        assert callable(verify_node_key)
        assert callable(verify_owner_api_key)
        assert callable(verify_owner_proof)
        ok("Node JWT / Node Key / Owner Proof 接口存在")
    except Exception as e:
        fail("认证接口检查", e)

    print("\n📡 模块2: 节点会话 / 新控制平面")
    print("─" * 50)

    try:
        from backend.app.hasn_im.adapters.routing.redis_presence_store import (
            ENTITY_NODE_KEY,
            NODE_CONN_KEY,
            OFFLINE_PREFIX,
            OFFLINE_TTL,
            USER_NODES_PREFIX,
        )
        from backend.app.hasn_im.application.node_session_service import (
            NodeSessionService,
            node_session_service,
        )
        ok("节点会话服务导入成功")
    except Exception as e:
        fail("节点会话服务导入", e)
        return results

    try:
        assert isinstance(node_session_service, NodeSessionService)
        for method in [
            "register_node",
            "unregister_node",
            "add_owner",
            "remove_owner",
            "renew_owner",
            "list_owners",
            "add_agent_presence",
            "remove_agent_presence",
            "push_message_to",
            "get_offline_messages",
        ]:
            assert hasattr(node_session_service, method), f"缺少方法: {method}"
        ok("节点会话新控制平面方法完整")
    except Exception as e:
        fail("节点会话方法检查", e)

    try:
        assert NODE_CONN_KEY == "hasn:node_conn"
        assert ENTITY_NODE_KEY == "hasn:entity_node"
        assert USER_NODES_PREFIX == "hasn:user_nodes"
        assert OFFLINE_PREFIX == "hasn:offline"
        assert OFFLINE_TTL == 604800
        ok("Redis 键前缀与 TTL 已对齐新模型")
    except Exception as e:
        fail("Redis 键检查", e)

    print("\n🧱 模块3: 数据模型")
    print("─" * 50)

    try:
        from backend.app.hasn.model import HasnAgents, HasnNodeBindings, HasnNodes, HasnOwnerApiKeys
        assert hasattr(HasnNodes, "node_id")
        assert hasattr(HasnNodes, "node_key_hash")
        assert hasattr(HasnNodeBindings, "binding_id")
        assert hasattr(HasnNodeBindings, "owner_id")
        assert hasattr(HasnNodeBindings, "expires_at")
        assert hasattr(HasnOwnerApiKeys, "key_id")
        assert hasattr(HasnOwnerApiKeys, "key_hash")
        assert hasattr(HasnAgents, "owner_id")
        ok("HasnNodes / HasnNodeBindings / HasnOwnerApiKeys 模型存在")
    except Exception as e:
        fail("新模型检查", e)

    print("\n🔌 模块4: API 路由")
    print("─" * 50)

    try:
        from backend.app.hasn_im.api.ws_node import router as ws_router_api
        ws_paths = [r.path for r in ws_router_api.routes]
        assert "/ws/node" in ws_paths
        assert "/ws/client" not in ws_paths
        ok(f"WebSocket 端点正确: {ws_paths}")
    except Exception as e:
        fail("WS 路由检查", e)

    try:
        from backend.app.hasn.api.v1.app.hasn_auth_api import router as auth_api_router
        routes = {r.path: list(r.methods) for r in auth_api_router.routes if hasattr(r, "methods")}
        expected = ["/auth/register", "/auth/register-node", "/auth/node-token", "/me", "/me/nodes", "/me/agents"]
        for ep in expected:
            assert ep in routes, f"缺少端点: {ep}"
        assert "/auth/register-client" not in routes
        assert "/auth/client-token" not in routes
        assert "/me/clients" not in routes
        ok("auth API 已切换到 node 命名")
    except Exception as e:
        fail("auth API 路由检查", e)

    try:
        from backend.app.hasn.api.v1.node_control import router as node_control_router
        node_routes = {r.path: list(r.methods) for r in node_control_router.routes if hasattr(r, "methods")}
        expected = ["/node/owners", "/node/owners/{owner_id}/renew", "/node/owners/{owner_id}"]
        for ep in expected:
            assert ep in node_routes, f"缺少端点: {ep}"
        ok("Node 控制平面路由存在")
    except Exception as e:
        fail("Node 控制平面路由检查", e)

    print("\n🧪 模块5: 迁移文件")
    print("─" * 50)

    try:
        migration_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app/hasn/migration/v6_nodes_bindings_owner_keys.sql",
        )
        assert os.path.exists(migration_path)
        content = pathlib.Path(migration_path).read_text(encoding="utf-8")
        assert "hasn_nodes" in content
        assert "hasn_owner_api_keys" in content
        ok("v6 数据迁移脚本存在且包含新表回填")
    except Exception as e:
        fail("迁移脚本检查", e)

    print("\n" + "═" * 60)
    total = len(results)
    passed = sum(1 for r in results if r[0] == "✅")
    failed = sum(1 for r in results if r[0] == "❌")
    print(f"📊 测试汇总: {passed}/{total} 通过, {failed} 失败")

    if failed > 0:
        print("\n失败项:")
        for r in results:
            if r[0] == "❌":
                print(f"  ❌ {r[1]}: {r[2]}")

    print("═" * 60)
    return results


if __name__ == "__main__":
    asyncio.run(run_tests())
