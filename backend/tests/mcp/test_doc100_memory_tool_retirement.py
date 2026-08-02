"""doc100：云端记忆工具与 owner 贡献流必须彻底退役。"""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TOOL_ROOT = BACKEND_ROOT / 'app' / 'mcp' / 'tools'


def test_cloud_tool_directory_has_no_memory_namespace() -> None:
    """云端工具目录不得再声明或教学 `hasn.memory.*`。"""
    assert not (TOOL_ROOT / 'memory.py').exists()
    leaked = {
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in TOOL_ROOT.glob('*.py')
        if 'hasn.memory.' in path.read_text(encoding='utf-8')
    }
    assert leaked == set(), f'云端工具目录仍含本地记忆工具名：{sorted(leaked)}'


def test_cloud_server_does_not_register_memory_tools() -> None:
    """云端 MCP 目录只保留与记忆无关的工具。"""
    from backend.app.mcp.server import HasnCloudMcpServer

    names = {tool.name for tool in HasnCloudMcpServer().tool_registry.get_all_tools()}
    assert not any(name.startswith('hasn.memory.') for name in names)
    assert 'hasn.owner.memory.contribute' not in names


def test_semantic_fact_service_has_no_direct_cloud_write_entry() -> None:
    """云端事实写入只能来自节点上行或合并闸，service 不得残留直写入口。"""
    from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service

    source = (BACKEND_ROOT / 'app/hasn_memory/service/semantic_fact_service.py').read_text(encoding='utf-8')
    assert not hasattr(semantic_fact_service, 'save_fact')
    assert 'async def save_fact(' not in source


def test_owner_memory_contribution_contract_is_removed() -> None:
    """贡献表、贡献 DTO 与旧 HTTP 入口都不再存在。"""
    from backend.app.hasn.api.v1.agent.hasn_agent_profile import router as agent_router
    from backend.app.hasn_memory.api.v1.app.owner_memory import router as app_router
    from backend.app.hasn_memory.model import HasnOwnerMemory
    from backend.common.model import MappedBase

    assert HasnOwnerMemory.__tablename__ == 'owner_memory'
    assert 'hasn_memory.owner_memory_contribution' not in MappedBase.metadata.tables
    assert {getattr(route, 'path', None) for route in app_router.routes} == {'/memory'}
    assert '/memory/contribute' not in {
        getattr(route, 'path', None) for route in agent_router.routes
    }


def test_retirement_migration_marks_legacy_facts_and_drops_contribution_table() -> None:
    """迁移必须保留旧事实但标为退役来源，并删除贡献表。"""
    migration = (
        BACKEND_ROOT
        / 'sql'
        / 'hasn_memory'
        / 'migrations'
        / '2026-08-02-doc100-retire-cloud-memory-tools.sql'
    ).read_text(encoding='utf-8')
    assert "origin_kind = 'retired'" in migration
    assert "origin_node_id = 'legacy-cloud'" in migration
    assert 'DROP TABLE IF EXISTS hasn_memory.owner_memory_contribution' in migration
