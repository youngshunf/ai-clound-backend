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


def test_catalog_mode_equals_factory_default_when_no_override() -> None:
    """出厂默认成为唯一真相：无 override 时每条能力 mode = 其 per-capability 出厂默认。

    catalog 每条出参 default_mode（出厂态）+ mode（生效态）；无 override 时二者相等。
    message:send（平台社交工具，不花钱）出厂 allow。
    """
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx())
    for source in catalog['sources']:
        for cap in source['capabilities']:
            assert 'default_mode' in cap, f'{cap["key"]} 应出参 default_mode'
            assert cap['mode'] == cap['default_mode'], (
                f'{cap["key"]} 无 override 时 mode 应等于出厂默认 {cap["default_mode"]}'
            )
    platform = next(s for s in catalog['sources'] if s['source'] == 'platform')
    send = next(c for c in platform['capabilities'] if c['key'] == 'message:send')
    assert send['default_mode'] == 'allow'
    assert send['mode'] == 'allow'


def test_catalog_capability_override_reflected() -> None:
    catalog = mcp_server.tool_directory.build_scope_catalog(
        _ctx(default_mode='allow', capability_modes={'message:send': 'deny'})
    )
    platform = next(s for s in catalog['sources'] if s['source'] == 'platform')
    send = next(c for c in platform['capabilities'] if c['key'] == 'message:send')
    assert send['mode'] == 'deny'
    # 其它能力无 override → 回落各自出厂默认（不再随全局 default_mode）。
    others = [c for c in platform['capabilities'] if c['key'] != 'message:send']
    assert all(c['mode'] == c['default_mode'] for c in others)


def test_catalog_global_default_mode_does_not_override_factory() -> None:
    """缺陷 3 修复：全局 default_mode 不再驱动 per-capability 静息态——出厂默认才是唯一真相。

    本地 CapabilityModeMirror 解析只认显式 override + 工具出厂默认、不消费云端全局 default_mode；
    catalog 必须同构（否则权限页显示与本地执行分裂——「全是允许但每次仍审批」即此 bug）。故即便
    default_mode='ask'，未被 owner 显式覆盖的 message:send（出厂 allow）仍呈现 allow。
    """
    catalog = mcp_server.tool_directory.build_scope_catalog(_ctx(default_mode='ask'))
    # 顶层 default_mode 仍透传（信封兼容），但不再驱动 per-capability mode。
    assert catalog['default_mode'] == 'ask'
    platform = next(s for s in catalog['sources'] if s['source'] == 'platform')
    send = next(c for c in platform['capabilities'] if c['key'] == 'message:send')
    assert send['mode'] == 'allow', '全局 default_mode=ask 不应把出厂 allow 的能力变 ask'


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

    # 视频生成走独立 video:generate 档（单价高于图片，owner 可单独管控）。
    video = scope_meta('video:generate')  # backend/app/mcp/platform_scopes.py
    assert video['domain'] == 'video'
    assert video['label'] == '生成视频'
    # 出厂 Ask（花钱、= hasn.video.generate 本地工具出厂态），与本地执行同构。
    assert video['default_mode'] == 'ask'
