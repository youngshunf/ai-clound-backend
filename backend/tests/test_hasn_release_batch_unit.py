"""桌面端发布批次纯函数测试。"""

from backend.app.hasn_release.service.release_service import (
    _completed_platforms,
    _next_patch_version,
    _normalize_release_notes,
)


def test_next_patch_version_uses_highest_allocated_version() -> None:
    assert _next_patch_version(['0.3.0', '0.3.2', '0.3.1']) == '0.3.3'
    assert _next_patch_version([]) == '0.0.1'


def test_normalize_release_notes_removes_fence_and_caps_200_chars() -> None:
    raw = f'```markdown\n{"改进桌面端体验。" * 40}\n```'
    notes = _normalize_release_notes(raw)
    assert '```' not in notes
    assert len(notes) == 200


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
