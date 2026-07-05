"""统一 scope 词表（冒号命名）回归测试。

实施102 S0：Agent JWT `scopes` claim / `DEFAULT_AGENT_SCOPES` / `normalize_scope`
均已退役——授权唯一真相是 `hasn_agent_scopes.{default_mode, capability_modes}`
三态（消费时活取）。本文件因此只保留与 JWT scopes 无关的词表规范断言：

- session:ask 平台级 scope 有展示元数据（webui 权限页不漏词，零漂移守卫）。
- builtin AI-Native manifest 的 required_scopes 全为冒号（domain:action）。
"""

from __future__ import annotations


def test_session_ask_scope_registered_and_documented() -> None:
    """实施93 SA-P3：session:ask 平台级 scope 有展示元数据（零漂移守卫）。"""
    from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG

    # 展示元数据齐备（catalog 中文 label/domain/description），webui 权限页不漏词。
    meta = PLATFORM_SCOPE_CATALOG.get('session:ask')
    assert meta is not None, 'session:ask 缺 PLATFORM_SCOPE_CATALOG 展示元数据'
    assert meta['domain'] == 'session'
    assert meta['label_zh']


def test_builtin_manifests_required_scopes_all_colon() -> None:
    from backend.app.hasn.service.ai_native_builtin_manifests import (
        COMMUNITY_AI_NATIVE_MANIFEST,
        KNOWLEDGE_AI_NATIVE_MANIFEST,
    )

    for manifest in (COMMUNITY_AI_NATIVE_MANIFEST, KNOWLEDGE_AI_NATIVE_MANIFEST):
        for section in ('capabilities', 'tools'):
            for entry in manifest.get(section, []):
                for scope in entry.get('required_scopes', []):
                    assert '.' not in scope, f'{manifest["app_id"]} 含点号 scope: {scope}'
                    assert ':' in scope, f'{manifest["app_id"]} 非冒号 scope: {scope}'


def test_media_generate_key_unified_and_image_generate_dead_key_removed() -> None:
    """实施102 S2·U-L3：本地媒体工具 scope 统一到 media:generate，死键 image:generate 已删。

    图片与语音生成统一档 media:generate（分组 media、默认 ask、花 owner 配额）；视频独立档
    video:generate 仍在。旧 image:generate 是无对应 cloud 工具的展示死键，删除后不得再出现。
    """
    from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG
    from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta

    assert 'image:generate' not in PLATFORM_SCOPE_CATALOG, 'image:generate 死键未删'
    assert 'image:generate' not in SCOPE_CATALOG, 'image:generate 仍聚合进全局 catalog'

    assert 'media:generate' in PLATFORM_SCOPE_CATALOG, 'media:generate 未登记'
    meta = scope_meta('media:generate')
    assert meta['domain'] == 'media'
    assert meta['default_mode'] == 'ask'  # 花配额 → 默认每次询问
    assert meta['label'] == '图片与语音生成'
    # 视频独立授权档仍在（单价远高）。
    assert 'video:generate' in SCOPE_CATALOG


def test_update_agent_scopes_rejects_unknown_capability_key() -> None:
    """实施102 S2·B7：PUT capability_modes 键校验——未知键 422 unknown_capability_key。

    白名单 = 全局 SCOPE_CATALOG（platform ∪ 各应用 ∪ 本地工具 scope）。纯校验逻辑，不触库。
    """
    from backend.app.hasn.service.agent_scopes_service import agent_scopes_service
    from backend.common.exception import errors
    from backend.common.response.response_code import StandardResponseCode

    # 已知键（media:generate / film:write / video:generate）应全部放行。
    agent_scopes_service._assert_known_capability_keys(
        {'media:generate': 'allow', 'video:generate': 'ask', 'film:write': 'deny'}
    )
    # 空/None 放行。
    agent_scopes_service._assert_known_capability_keys(None)
    agent_scopes_service._assert_known_capability_keys({})

    # 未知键 → 422 unknown_capability_key。
    import pytest

    with pytest.raises(errors.RequestError) as exc_info:
        agent_scopes_service._assert_known_capability_keys({'bogus:scope': 'allow'})
    assert exc_info.value.code == StandardResponseCode.HTTP_422
    assert 'unknown_capability_key' in exc_info.value.msg
    assert 'bogus:scope' in exc_info.value.msg


def test_scopes_kind_registered_as_owner_directed_sync_kind() -> None:
    """实施102 S4·U-L4：scopes 是 owner 定向 WSPUSH kind（bump_owner 认得，非全局握手 kind）。"""
    from backend.app.hasn.service.sync_invalidate_service import KINDS, KIND_SCOPES, OWNER_KINDS

    assert KIND_SCOPES == 'scopes'
    assert KIND_SCOPES in OWNER_KINDS, 'scopes 应为 owner 定向 kind'
    assert KIND_SCOPES not in KINDS, 'scopes 是 per-owner 指纹，不应进全局握手快照'


# ------------------------------------------------------------------------------
# i18n 双语守卫（福仔「一次到位·全双语」）：权限页分组名/能力名/描述在源头（cloud
# scope catalog）就带中英，前端据语言设置取、英文缺失才诚实回退中文——绝不由前端
# 手维护中文名（曾因前端漏映射而露英文 domain 键）。以下守卫钉死源头双语齐备。
# ------------------------------------------------------------------------------


def test_every_scope_has_bilingual_display_metadata() -> None:
    """SCOPE_CATALOG 每条 scope 声明都自带非空 label_zh + label_en（源头产出英文）。

    scope_meta 出参英文字段永不为空、永不露 scope key（英文缺失回退中文）；有中文描述
    的 scope 其英文描述回退也必非空。
    """
    from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta

    for key, meta in SCOPE_CATALOG.items():
        assert meta.get('label_zh'), f'{key} 缺 label_zh'
        assert meta.get('label_en'), f'{key} 缺 label_en（全双语要求源头产出英文，不许留空露 key）'
        display = scope_meta(key)
        assert display['label_en'] and display['label_en'] != key, f'{key} label_en 露 key/为空'
        assert display['label'] and display['label'] != key, f'{key} label 露 key/为空'
        if meta.get('description'):
            assert display['description_en'], f'{key} 有中文描述但英文回退为空'


def test_domain_labels_cover_every_catalog_domain() -> None:
    """DOMAIN_LABELS 覆盖 SCOPE_CATALOG 里出现的每个 domain（漏登记 → 权限页露英文键）。

    这是「权限页分组名露英文」根 bug 的守卫：只要某 scope 的 domain 未在 DOMAIN_LABELS
    登记，domain_label 就回退 domain 键（英文），前端渲染即露英文。新增 domain 必须登记。
    """
    from backend.app.mcp.scopes import DOMAIN_LABELS, SCOPE_CATALOG

    domains = {meta.get('domain', '') for meta in SCOPE_CATALOG.values()}
    domains.discard('')
    missing = sorted(d for d in domains if d not in DOMAIN_LABELS)
    assert not missing, f'DOMAIN_LABELS 未登记这些 domain（权限页会露英文键）: {missing}'


def test_domain_and_source_labels_all_bilingual() -> None:
    """DOMAIN_LABELS / SOURCE_LABELS 每条都齐备中英（zh + en 皆非空）。"""
    from backend.app.mcp.scopes import DOMAIN_LABELS, SOURCE_LABELS

    for domain, entry in DOMAIN_LABELS.items():
        assert entry.get('zh'), f'domain {domain} 缺中文名'
        assert entry.get('en'), f'domain {domain} 缺英文名'
    for source, entry in SOURCE_LABELS.items():
        assert entry.get('zh'), f'source {source} 缺中文名'
        assert entry.get('en'), f'source {source} 缺英文名'


def test_scope_catalog_response_schema_declares_every_emitted_field() -> None:
    """build_scope_catalog 产出的每个键都必须在 Pydantic 响应 schema 里声明——否则被剥离。

    ⭐根 bug 守卫（2026-07-03）：service 层 `ScopeCatalogResponse.model_validate(catalog)`
    把 build_scope_catalog 的 dict 灌进 Pydantic 模型，Pydantic 默认 `extra='ignore'`
    **静默丢弃未声明字段**。曾发生：给 build_scope_catalog + scope_meta 加了双语
    `label_en`/`domain_label`/`domain_label_en`/`description_en`，却漏给 ScopeCapability /
    ScopeSource schema 补字段 → HTTP 响应把双语全剥离 → 权限页露英文 domain 键、语言切换
    无效（直调 service 有、经 HTTP 没有，极难排查）。此守卫断言 catalog 每层产出的键都被
    schema 声明，从此漏声明即红（不必打真实 HTTP 也能抓到这类序列化边界漂移）。
    """
    from backend.app.hasn.schema.agent_scopes import ScopeCapability, ScopeSource
    from backend.app.mcp.auth import AgentContext
    from backend.app.mcp.server import mcp_server

    ctx = AgentContext(
        hasn_id='a_guard_probe',
        owner_id=0,
        agent_status='active',
        metadata={},
        default_mode='allow',
        capability_modes={},
    )
    catalog = mcp_server.tool_directory.build_scope_catalog(ctx)

    source_fields = set(ScopeSource.model_fields)
    cap_fields = set(ScopeCapability.model_fields)
    for src in catalog['sources']:
        extra_src = set(src) - source_fields
        assert not extra_src, f'ScopeSource schema 未声明 build_scope_catalog 产出的键（会被剥离）: {extra_src}'
        for cap in src['capabilities']:
            extra_cap = set(cap) - cap_fields
            assert not extra_cap, f'ScopeCapability schema 未声明 build_scope_catalog 产出的键（会被剥离）: {extra_cap}'


def test_app_display_domains_regroup_deck_designsystem_into_app_source() -> None:
    """展示分组重分类（福仔 2026-07-03）：deck/designsystem 归「AI-Native 应用」组、不在平台组。

    deck/designsystem 工具经 TOOLMIG 注册为 platform 工具，但语义是独立 AI-Native 应用 →
    build_scope_catalog 按 APP_DISPLAY_DOMAINS 把它们的能力挪进 'app' 来源组。task/plan/
    marketplace 等平台底座留在 'platform' 组。守住这个分类，避免再回归到「应用混进平台工具」。
    """
    from backend.app.mcp.auth import AgentContext
    from backend.app.mcp.scopes import APP_DISPLAY_DOMAINS
    from backend.app.mcp.server import mcp_server

    assert {'deck', 'designsystem'} <= APP_DISPLAY_DOMAINS, 'deck/designsystem 应在 APP_DISPLAY_DOMAINS'

    ctx = AgentContext(
        hasn_id='a_regroup_probe',
        owner_id=0,
        agent_status='active',
        metadata={},
        default_mode='allow',
        capability_modes={},
    )
    catalog = mcp_server.tool_directory.build_scope_catalog(ctx)
    by_source = {s['source']: {c['domain'] for c in s['capabilities']} for s in catalog['sources']}
    platform_domains = by_source.get('platform', set())
    app_domains = by_source.get('app', set())

    # deck/designsystem 已挪出平台组、进入应用组。
    assert 'deck' in app_domains and 'deck' not in platform_domains, 'deck 应在 app 组、不在 platform'
    assert 'designsystem' in app_domains and 'designsystem' not in platform_domains, (
        'designsystem 应在 app 组、不在 platform'
    )
    # 平台底座域仍留在平台组（不被误挪）。
    assert 'task' in platform_domains, 'task 应留在平台工具组'
    assert 'plan' in platform_domains, 'plan 应留在平台工具组'
