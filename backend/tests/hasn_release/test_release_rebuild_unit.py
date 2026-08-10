"""指定版本原位覆盖发布的纯逻辑契约测试。"""

from backend.app.hasn_release.schema.release import PrepareReleaseRequest
from backend.app.hasn_release.service import release_service as release_service_module


def test_prepare_request_exposes_explicit_replace_switch() -> None:
    """同版本重打必须由显式开关授权，不能放宽普通发布。"""
    request = PrepareReleaseRequest(
        channel='stable',
        source_commit='a' * 40,
        requested_version='0.3.2',
        replace_existing=True,
    )

    assert hasattr(request, 'replace_existing'), 'prepare schema 缺少显式覆盖开关'
    assert request.replace_existing is True


def test_next_rebuild_tag_is_monotonic_and_does_not_move_original_tag() -> None:
    """每次新源码生成新 rebuild tag，旧 tag 永不移动。"""
    assert hasattr(release_service_module, '_next_rebuild_tag'), '缺少 rebuild tag 分配器'
    next_tag = release_service_module._next_rebuild_tag

    assert next_tag('0.3.2', 'v0.3.2') == 'v0.3.2-rebuild.1'
    assert next_tag('0.3.2', 'v0.3.2-rebuild.1') == 'v0.3.2-rebuild.2'
    assert next_tag('0.3.2', 'v0.3.2-rebuild.19') == 'v0.3.2-rebuild.20'


def test_rebuild_tag_detection_is_exact_for_version() -> None:
    """只把目标版本自己的 rebuild tag 当作重打批次。"""
    assert hasattr(release_service_module, '_is_rebuild_tag'), '缺少 rebuild tag 判定器'
    is_rebuild = release_service_module._is_rebuild_tag

    assert is_rebuild('0.3.2', 'v0.3.2-rebuild.1') is True
    assert is_rebuild('0.3.2', 'v0.3.3-rebuild.1') is False
    assert is_rebuild('0.3.2', 'v0.3.2') is False


def test_replace_resume_only_joins_same_version_active_batch() -> None:
    """断点续跑可加入同版本草稿，但不能越过其它活动版本。"""
    can_join = release_service_module._can_join_active_replace

    assert can_join(requested_version='0.3.2', active_version='0.3.2') is True
    assert can_join(requested_version='0.3.1', active_version='0.3.2') is False
    assert can_join(requested_version='', active_version='0.3.2') is False
