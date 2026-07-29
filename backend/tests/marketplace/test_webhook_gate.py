"""github webhook 技能源变更闸门单测（纯函数，零依赖）。

公共技能加载机制（doc12 §3.2）要求：改 `common-skills.yaml`（仅成员变化、不动任何技能目录）
也必须触发一次 GitHub skills 同步，否则公共技能集合的增删无法落到云端 is_common 标记。
本测试锁住该闸门契约，避免与 hub `scripts/trigger_webhook.py:matches` 漂移。
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
    assert source_release_required(_push('bundles/research/bundle.yaml')) is False


def test_bundle_changes_remain_on_repository_sync_temporarily() -> None:
    assert bundle_source_changes(_push('bundles/research/bundle.yaml')) == {
        'bundles/research/bundle.yaml'
    }
    assert bundle_source_changes(_push('common-bundles.yaml')) == {'common-bundles.yaml'}
    assert bundle_source_changes(_push('huanxing-skills/search/newsnow/SKILL.md')) == set()
