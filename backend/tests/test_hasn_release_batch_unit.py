"""桌面端发布批次纯函数测试。"""

import pytest

from backend.app.hasn_release.schema.release import REQUIRED_DESKTOP_PLATFORMS
from backend.app.hasn_release.service.release_service import (
    _can_join_published_batch,
    _completed_platforms,
    _ensure_commit_lock_transition,
    _next_patch_version,
    _normalize_release_notes,
    _should_generate_release_notes,
)
from backend.common.exception import errors
from backend.core.conf import Settings


def test_next_patch_version_uses_highest_allocated_version() -> None:
    assert _next_patch_version(['0.3.0', '0.3.2', '0.3.1']) == '0.3.3'
    assert _next_patch_version([]) == '0.0.1'


def test_commit_lock_allows_moving_forward_before_tag_ready() -> None:
    """tag 未 ready 前允许把锁定点前移到「写入版本号」的那个提交。"""
    _ensure_commit_lock_transition(
        tag_status='pending',
        locked_commit='a' * 40,
        confirmed_commit='b' * 40,
    )


def test_commit_lock_freezes_after_tag_ready() -> None:
    """tag 已 ready 说明有平台按它构建过，此时换基线会做出同版本号不同内容的包。"""
    with pytest.raises(errors.RequestError) as excinfo:
        _ensure_commit_lock_transition(
            tag_status='ready',
            locked_commit='a' * 40,
            confirmed_commit='b' * 40,
        )
    assert 'a' * 40 in str(excinfo.value.msg)
    assert 'b' * 40 in str(excinfo.value.msg)


def test_commit_lock_ready_accepts_same_commit_case_insensitively() -> None:
    """后续平台重复确认同一个 commit 是幂等的，大小写不该造成误拒。"""
    _ensure_commit_lock_transition(
        tag_status='ready',
        locked_commit='A' * 40,
        confirmed_commit='a' * 40,
    )


def test_normalize_release_notes_preserves_markdown_and_caps_500_chars() -> None:
    raw = f'```markdown\n- **新增**：支持批量处理\n- **优化**：{"改进桌面端体验。" * 100}\n```'
    notes = _normalize_release_notes(raw)
    assert '```' not in notes
    assert notes.startswith('- **新增**：支持批量处理\n- **优化**：')
    assert len(notes) <= 500
    assert notes.endswith('。')


def test_normalize_release_notes_closes_truncated_clause() -> None:
    raw = '- **安全**：上线出站消息三层拦截与敏感信息扫描，阻止高风险内容发送。'
    notes = _normalize_release_notes(raw, max_chars=30)
    assert notes == '- **安全**：上线出站消息三层拦截与敏感信息扫描。'


def test_ready_release_notes_are_not_generated_again() -> None:
    assert _should_generate_release_notes('ready') is False
    assert _should_generate_release_notes('pending') is True
    assert _should_generate_release_notes('failed') is True


def test_completed_platforms_requires_installer_and_updater() -> None:
    completed = _completed_platforms(
        ['darwin-aarch64', 'darwin-x86_64', 'windows-x86_64'],
        {
            ('darwin-aarch64', 'installer'),
            ('darwin-aarch64', 'updater'),
            ('darwin-x86_64', 'installer'),
            ('windows-x86_64', 'installer'),
            ('windows-x86_64', 'updater'),
        },
    )
    assert completed == ['darwin-aarch64', 'windows-x86_64']


def test_published_batch_is_reused_until_all_platforms_complete() -> None:
    """源码前进不影响补平台；所有要求平台完成后才允许自动创建下一批。"""
    common = {
        'published_version': '0.3.3',
        'required_platforms': ['darwin-aarch64', 'windows-x86_64'],
    }
    assert _can_join_published_batch(
        requested_version='',
        completed_platforms=['windows-x86_64'],
        **common,
    ) is True
    assert _can_join_published_batch(
        requested_version='',
        completed_platforms=['darwin-aarch64', 'windows-x86_64'],
        **common,
    ) is False


def test_explicit_version_reuses_matching_published_tag() -> None:
    """显式指定当前发布版本时始终复用其冻结 tag，不受平台完成度影响。"""
    common = {
        'published_version': '0.3.3',
        'required_platforms': ['darwin-aarch64', 'windows-x86_64'],
        'completed_platforms': ['darwin-aarch64', 'windows-x86_64'],
    }
    assert _can_join_published_batch(requested_version='0.3.3', **common) is True
    assert _can_join_published_batch(requested_version='0.3.4', **common) is False


def test_desktop_release_batch_requires_all_supported_platforms() -> None:
    assert REQUIRED_DESKTOP_PLATFORMS == (
        'darwin-aarch64',
        'darwin-x86_64',
        'windows-x86_64',
        'linux-x86_64',
    )


def test_release_github_repo_default_points_at_the_current_repo_name() -> None:
    """发布仓名默认值必须跟着仓库改名走，否则 confirm-tag 会去错仓查 tag。

    2026-08-12 实测：默认值停在改名前的 `youngshunf/hasn-node`，新的同名仓一被创建，
    GitHub 的改名重定向当场失效并改指新仓，confirm-tag 于是在空仓里查不到 release tag，
    四个平台全部卡在 400『远端 release tag 尚不存在』。断言的是**类默认值**而非
    `settings.RELEASE_GITHUB_REPO`，后者会被本机 .env 覆盖、测不出源码里的漂移。
    """
    assert Settings.model_fields['RELEASE_GITHUB_REPO'].default == 'youngshunf/hasn-node-demo'
