"""HASN 公民块 prepend 进模板 soul_md 的同步逻辑测试（零 mock，纯函数 + 真实读盘）。

验证 github_app_sync 把共享 templates/_platform/HASN.md prepend 到每个模板的 SOUL.md：
- 共享块在 → `HASN 块 + 人格`，HASN 在前；
- 共享块缺失 → 人格原样（不阻断 sync）；
- 人格缺失 → 至少返回 HASN 块；
- 幂等：纯人格反复合成不产生双重前缀。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.marketplace.service.github_app_sync_service import GitHubAppSyncService

if TYPE_CHECKING:
    from pathlib import Path

_HASN = '# 我在唤星（HASN）网络中\n\n我是 {{owner_nickname}} 的 AI 分身。\n\n---'
_PERSONA = '# SOUL.md — 我是{{display_name}}\n\n## 基本身份\n- 名字：{{display_name}}'


def test_compose_prepends_hasn_block_before_persona() -> None:
    composed = GitHubAppSyncService._compose_soul_md(_HASN, _PERSONA)
    assert composed is not None
    # HASN 块在前、人格在后
    assert composed.index('我在唤星') < composed.index('基本身份')
    assert '{{owner_nickname}}' in composed  # 占位符保留，留给 register 渲染
    assert '{{display_name}}' in composed


def test_compose_missing_hasn_block_returns_persona_unchanged() -> None:
    assert GitHubAppSyncService._compose_soul_md(None, _PERSONA) == _PERSONA
    assert GitHubAppSyncService._compose_soul_md('   ', _PERSONA) == _PERSONA


def test_compose_missing_persona_returns_hasn_block() -> None:
    composed = GitHubAppSyncService._compose_soul_md(_HASN, None)
    assert composed is not None
    assert '我在唤星' in composed
    # 既无共享块又无人格 → None（纯身份创建退路）
    assert GitHubAppSyncService._compose_soul_md(None, None) is None


def test_compose_is_idempotent_no_double_prefix() -> None:
    once = GitHubAppSyncService._compose_soul_md(_HASN, _PERSONA)
    # sync 永远从纯人格 hub 文件重读，不会把合成结果再喂进来；但即便如此，
    # 重复合成纯人格也只产生一个 HASN 前缀。
    twice = GitHubAppSyncService._compose_soul_md(_HASN, _PERSONA)
    assert once == twice
    assert once is not None
    assert once.count('我在唤星（HASN）网络中') == 1


def test_compose_reads_shared_platform_file_from_disk(tmp_path: Path) -> None:
    # 真实读盘：模拟 hub 目录布局 templates/_platform/HASN.md
    platform_dir = tmp_path / 'templates' / '_platform'
    platform_dir.mkdir(parents=True)
    (platform_dir / 'HASN.md').write_text(_HASN, encoding='utf-8')

    hasn_block = GitHubAppSyncService._read_optional_text(
        tmp_path / 'templates' / '_platform' / 'HASN.md'
    )
    assert hasn_block is not None
    composed = GitHubAppSyncService._compose_soul_md(hasn_block, _PERSONA)
    assert composed is not None
    assert composed.startswith('# 我在唤星（HASN）网络中')

    # 缺文件 → None，合成退回纯人格
    missing = GitHubAppSyncService._read_optional_text(
        tmp_path / 'templates' / '_platform' / 'MISSING.md'
    )
    assert missing is None
    assert GitHubAppSyncService._compose_soul_md(missing, _PERSONA) == _PERSONA
