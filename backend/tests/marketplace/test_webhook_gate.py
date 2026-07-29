"""github webhook 技能源变更闸门单测（纯函数，零依赖）。

Webhook 只负责提示本地 AstraHub 发布，不再触发服务器仓库扫描。
"""

from __future__ import annotations

from backend.app.marketplace.api.v1.webhook import (
    bundle_source_changes,
    has_skill_source_changes,
    source_release_required,
)


def _push(*paths: str) -> list[dict]:
    return [{'modified': list(paths), 'added': [], 'removed': []}]


def test_common_skills_yaml_change_triggers_skill_sync() -> None:
    assert has_skill_source_changes(_push('common-skills.yaml')) is True


def test_huanxing_skill_dir_change_triggers() -> None:
    assert has_skill_source_changes(_push('huanxing-skills/search/newsnow/SKILL.md')) is True


def test_bundle_yaml_change_triggers_skill_sync() -> None:
    # 技能包（实施/91 B3.2）走 skills webhook 同步入 marketplace_template(skill_pack)。
    assert has_skill_source_changes(_push('bundles/backend-dev/bundle.yaml')) is True


def test_bundles_added_path_triggers() -> None:
    assert has_skill_source_changes([{'modified': [], 'added': ['bundles/research/bundle.yaml'], 'removed': []}]) is True


def test_common_bundles_yaml_change_triggers_skill_sync() -> None:
    # 改 common-bundles.yaml（仅公共包集合变化）也须触发同步以重打 is_common。
    assert has_skill_source_changes(_push('common-bundles.yaml')) is True


def test_gitmodules_change_triggers() -> None:
    assert has_skill_source_changes(_push('.gitmodules')) is True


def test_clawhub_cache_change_does_not_trigger() -> None:
    assert has_skill_source_changes(_push('clawhub/mnetfairy/ai-insurance-advisor/SKILL.md')) is False


def test_unrelated_change_does_not_trigger() -> None:
    assert has_skill_source_changes(_push('README.md', 'docs/x.md')) is False


def test_skill_source_push_requires_local_release() -> None:
    assert source_release_required(_push('huanxing-skills/search/newsnow/SKILL.md')) is True
    assert source_release_required(_push('github/baoyu-skills')) is True
    assert source_release_required(_push('common-skills.yaml')) is True
    assert source_release_required(_push('bundles/research/bundle.yaml')) is True
    assert source_release_required(_push('common-bundles.yaml')) is True
    assert source_release_required(_push('templates/agent/assistant/SOUL.md')) is True
    assert source_release_required(
        _push('workflow-templates/fin-research/workflow-template.yaml')
    ) is True


def test_no_change_uses_retired_server_repository_sync() -> None:
    assert bundle_source_changes(_push('bundles/research/bundle.yaml')) == set()
    assert bundle_source_changes(_push('common-bundles.yaml')) == set()
    assert bundle_source_changes(_push('huanxing-skills/search/newsnow/SKILL.md')) == set()
