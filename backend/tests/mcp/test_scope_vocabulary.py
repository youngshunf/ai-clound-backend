"""P1 — 统一 scope 词表（点号 → 冒号）回归测试。

断言：
- DEFAULT_AGENT_SCOPES 全为冒号词表（无点号）。
- builtin AI-Native manifest 的 required_scopes 全为冒号。
- 归一兜底：AgentContext.has_scope / require_scopes 把 `message.read` 与
  `message:read` 视为等价（迁移窗口防回归）。
"""

from __future__ import annotations

import pytest

from backend.app.mcp.auth import AgentContext
from backend.common.security.agent_jwt import DEFAULT_AGENT_SCOPES, normalize_scope


def _ctx(scopes: list[str]) -> AgentContext:
    return AgentContext(
        hasn_id='a_test_vocab',
        owner_id=1,
        scopes=scopes,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_test_vocab',
        session_uuid='s_test_vocab',
    )


def test_default_agent_scopes_all_colon() -> None:
    for scope in DEFAULT_AGENT_SCOPES:
        assert '.' not in scope, f'DEFAULT_AGENT_SCOPES 含点号: {scope}'
        assert ':' in scope, f'DEFAULT_AGENT_SCOPES 非 domain:action: {scope}'


def test_session_ask_scope_registered_and_documented() -> None:
    """实施93 SA-P3：session:ask 平台级 scope 出厂铸入 + 有展示元数据（零漂移守卫）。"""
    from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG

    # 出厂 Allow：每个分身的 JWT 默认带 session:ask（工作会话里可向主人提问）。
    assert 'session:ask' in DEFAULT_AGENT_SCOPES
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


def test_normalize_scope_dot_to_colon() -> None:
    assert normalize_scope('message.read') == 'message:read'
    assert normalize_scope('message:read') == 'message:read'
    assert normalize_scope('community.post') == 'community:post'


def test_has_scope_normalizes_dot_and_colon_equivalent() -> None:
    # context 持冒号词表
    ctx = _ctx(['message:read', 'community:read'])
    assert ctx.has_scope('message.read') is True  # 点号查询命中冒号存量
    assert ctx.has_scope('message:read') is True
    assert ctx.has_scope('contact:read') is False

    # context 持点号（旧 JWT 快照），冒号查询也应命中
    legacy = _ctx(['message.read'])
    assert legacy.has_scope('message:read') is True


def test_require_scopes_normalizes_and_raises_on_missing() -> None:
    from fastapi import HTTPException

    ctx = _ctx(['message:read'])
    ctx.require_scopes('message.read')  # 不抛
    with pytest.raises(HTTPException):
        ctx.require_scopes('contact:read')
