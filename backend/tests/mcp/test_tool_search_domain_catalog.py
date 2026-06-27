"""云端 tool.search 数据驱动「可搜索域目录」测试。

验证：每个 AI-Native 应用 manifest 声明 `domain_summary`（{namespace: 一句话}），
`ToolSearchTool` 描述自动汇聚成「可搜索的应用域」目录——新增应用即自动多一行，
无需手改 tool_search.py（零硬编码、自动注册）。零 mock：直接读真实内置 manifest 注册表。
"""
from __future__ import annotations

from backend.app.hasn_core.app_platform import ai_native_app_registry
from backend.app.mcp.tools.tool_search import (
    _build_search_description,
    _searchable_app_domains,
)


def test_all_builtin_manifests_declare_domain_summary() -> None:
    # 数据驱动契约：每个内置 AI-Native 应用都必须声明 domain_summary（dict 形态，非空）。
    missing = []
    for manifest in ai_native_app_registry.list_builtin_apps():
        summary = manifest.get('domain_summary')
        if not isinstance(summary, dict) or not summary:
            missing.append(manifest.get('app_id'))
    assert not missing, f'AI-Native 应用缺 domain_summary 声明: {missing}'


def test_searchable_domains_aggregates_namespaces() -> None:
    pairs = _searchable_app_domains()
    keys = {ns for ns, _ in pairs}
    # 单应用单域。
    assert 'deck' in keys
    assert 'community' in keys
    assert 'finance' in keys
    # hasn_task 一个 manifest 同时贡献 task + workflow 两域。
    assert 'task' in keys
    assert 'workflow' in keys
    # 每条都带非空中文说明，且 namespace 唯一去重。
    assert all(ns and label for ns, label in pairs)
    assert len(keys) == len(pairs)


def test_description_lists_searchable_domains() -> None:
    # 不传 cloud_namespaces（保守回落）：列全部域、不标注，例子用云端可达工具。
    desc = _build_search_description()
    assert '可搜索的应用域' in desc
    # 福仔示例：deck → 演示文稿相关。
    assert '- deck：演示文稿' in desc
    # 多域应用（hasn_task）的 workflow 域也单独成行。
    assert '- workflow：' in desc
    # 三种 query 用法仍在描述里（取 schema / 关键词搜 / 来源分类）。
    assert 'tool:<工具名>' in desc
    assert 'sources' in desc
    # 不传可达集时不应标注「仅本地分身」（不臆造可达性）。
    assert '仅本地分身' not in desc
    # 示例工具必须是云端真实可达的（不再用 deck 这类纯本地工具误导）。
    assert 'tool:hasn.deck.create' not in desc


def test_description_marks_local_only_domains() -> None:
    """传入云端可达 namespace 集合后：纯本地域标「仅本地分身」、云端域不标、附说明。"""
    # 模拟云端面：community/plan 可达（plan 经 TOOLMIG 迁成 platform 工具），deck/task/workflow 不可达。
    cloud_ns = {'hasn.community', 'hasn.plan'}
    desc = _build_search_description(cloud_ns)
    assert '- community：' in desc
    assert '仅本地分身' not in desc.split('- community：', 1)[1].split('\n', 1)[0]  # community 行不带标注
    # plan 在云端可达（platform 工具）→ 不标注。
    plan_line = next(line for line in desc.splitlines() if line.startswith('- plan：'))
    assert '仅本地分身' not in plan_line
    # deck/task/workflow 云端够不到 → 各自标注「仅本地分身」。
    for ns in ('deck', 'task', 'workflow'):
        line = next(line for line in desc.splitlines() if line.startswith(f'- {ns}：'))
        assert '（仅本地分身）' in line, f'{ns} 应标注仅本地分身'
    # 有本地域时附整体说明，告知云端分身转用本地分身。
    assert '本地运行时工具，云端分身不可调用' in desc
