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
