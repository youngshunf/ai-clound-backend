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
