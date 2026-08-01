"""桌面端发布批次纯函数测试。"""

from backend.app.hasn_release.schema.release import REQUIRED_DESKTOP_PLATFORMS
from backend.app.hasn_release.service.release_service import (
    _completed_platforms,
    _next_patch_version,
    _normalize_release_notes,
    _should_generate_release_notes,
)


def test_next_patch_version_uses_highest_allocated_version() -> None:
    assert _next_patch_version(['0.3.0', '0.3.2', '0.3.1']) == '0.3.3'
    assert _next_patch_version([]) == '0.0.1'


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


def test_desktop_release_batch_requires_all_supported_platforms() -> None:
    assert REQUIRED_DESKTOP_PLATFORMS == (
        'darwin-aarch64',
        'darwin-x86_64',
        'windows-x86_64',
        'linux-x86_64',
    )
