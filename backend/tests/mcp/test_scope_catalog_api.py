"""P5 — 工具/scope 目录构建单测（build_scope_catalog）。

验证 D2：catalog 聚合全部已注册可见工具的 required_scopes，按来源分组（platform/app/external），
每条带三态 mode（resolve_capability_mode）+ scopes.py 展示元数据；external 结构保留但为空（Q5）。
不依赖 DB：直接喂 AgentContext + 已注册的 builtin 工具（零 mock，用真实注册表与真实策略解析）。
"""

from __future__ import annotations

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.server import mcp_server


def _ctx(*, default_mode: str = 'allow', capability_modes: dict | None = None) -> AgentContext:
    return AgentContext(
        hasn_id='a_catalog_test',
        owner_id=0,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id='h_catalog_test',
        session_uuid='catalog:a_catalog_test',
        default_mode=default_mode,
        capability_modes=capability_modes or {},
    )


def test_catalog_groups_by_source_and_reserves_external_empty() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx())

    assert catalog['default_mode'] == 'allow'
    by_source = {s['source']: s for s in catalog['sources']}
    # 三个来源分组都在（Q5：external 结构保留）
    assert set(by_source) == {'platform', 'app', 'external'}
    # external 本轮无承接 → 能力为空，但分组与中文标签仍在
    assert by_source['external']['capabilities'] == []
    assert by_source['external']['label']

    # platform 至少含 message:send（MessageSendTool 已注册）
    platform_keys = {c['key'] for c in by_source['platform']['capabilities']}
    assert 'message:send' in platform_keys


def test_catalog_default_all_allow() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx())
    for source in catalog['sources']:
        for cap in source['capabilities']:
            assert cap['mode'] == 'allow', f'{cap["key"]} 默认应 allow（默认全开）'


def test_catalog_capability_override_reflected() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(
        _ctx(default_mode='allow', capability_modes={'message:send': 'deny'})
    )
    platform = next(s for s in catalog['sources'] if s['source'] == 'platform')
    send = next(c for c in platform['capabilities'] if c['key'] == 'message:send')
    assert send['mode'] == 'deny'
    # 其它能力仍随 default_mode=allow
    others = [c for c in platform['capabilities'] if c['key'] != 'message:send']
    assert all(c['mode'] == 'allow' for c in others)


def test_catalog_default_mode_ask_propagates() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx(default_mode='ask'))
    assert catalog['default_mode'] == 'ask'
    platform = next(s for s in catalog['sources'] if s['source'] == 'platform')
    assert all(c['mode'] == 'ask' for c in platform['capabilities'])


def test_catalog_entries_carry_display_metadata() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx())
    platform = next(s for s in catalog['sources'] if s['source'] == 'platform')
    send = next(c for c in platform['capabilities'] if c['key'] == 'message:send')
    # scopes.py 元数据：中文 label / domain / 描述 / 覆盖工具
    assert send['label'] == '发送消息'
    assert send['domain'] == 'message'
    assert send['description']
    assert any(t.endswith('message.send') for t in send['tools'])


def test_app_scope_labels_come_from_per_app_modules() -> None:
    """v3-3：catalog 取 label 用的 `scope_meta` 把 app 域元数据解析到各应用目录声明。

    `build_scope_catalog`（本文件上方测试覆盖其分组/三态）对每个 scope_key 调
    `scope_meta(scope_key)` 取中文 label/domain（line 127）；`scope_meta` 读聚合的
    SCOPE_CATALOG = platform_scopes.py ∪ 各 app scopes.py。验证 app 域 label 来自
    各应用目录、platform 域来自 platform_scopes——新增/删除应用只动该应用目录。
    """
    from backend.app.mcp.scopes import scope_meta

    deck = scope_meta('deck:manage')  # backend/app/deck/scopes.py
    assert deck['label'] == '管理演示文稿'
    assert deck['domain'] == 'deck'

    pub = scope_meta('publish:write')  # backend/app/publish/scopes.py
    assert pub['label'] == '发布与管理网页'
    assert pub['domain'] == 'publish'

    task = scope_meta('workflow:manage')  # backend/app/hasn_task/scopes.py（workflow 归 task 域）
    assert task['domain'] == 'task'

    msg = scope_meta('message:send')  # backend/app/mcp/platform_scopes.py
    assert msg['domain'] == 'message'
    assert msg['label'] == '发送消息'
