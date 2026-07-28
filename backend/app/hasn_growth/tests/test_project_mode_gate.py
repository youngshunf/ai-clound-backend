"""获客项目化模式服务端门禁。"""

from __future__ import annotations

import pytest

from backend.app.hasn_growth.service.project_mode_gate import assert_project_scope_enabled
from backend.common.exception import errors
from backend.core.conf import Settings, settings


def test_growth_project_flags_default_to_false() -> None:
    """所有项目化、PII、落地页和外发开关必须默认关闭。"""
    names = (
        'GROWTH_PROJECT_V4_ENABLED',
        'GROWTH_PROJECT_V4_ENTERPRISE_ENABLED',
        'GROWTH_PII_NEW_WRITE_ENABLED',
        'GROWTH_PII_SHADOW_READ_ENABLED',
        'GROWTH_PROJECT_DUAL_WRITE_ENABLED',
        'GROWTH_PROJECT_READ_CUTOVER_ENABLED',
        'GROWTH_PUBLISH_LANDING_ENABLED',
        'GROWTH_EXTERNAL_SEND_ENABLED',
    )

    assert all(Settings.model_fields[name].default is False for name in names)


def test_personal_project_mode_is_not_blocked_by_enterprise_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """个人模式不依赖尚未解决的企业 ID 映射。"""
    monkeypatch.setattr(settings, 'GROWTH_PROJECT_V4_ENTERPRISE_ENABLED', False)

    assert_project_scope_enabled('personal')


def test_enterprise_project_mode_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """企业 ID 没有权威映射时，后端以稳定 409 错误冻结企业项目模式。"""
    monkeypatch.setattr(settings, 'GROWTH_PROJECT_V4_ENTERPRISE_ENABLED', False)

    with pytest.raises(errors.ConflictError) as exc_info:
        assert_project_scope_enabled('enterprise')

    assert exc_info.value.code == 409
    assert exc_info.value.data == {'error_code': 'GROWTH_ENTERPRISE_PROJECT_MODE_DISABLED'}


def test_enterprise_project_mode_can_only_be_opened_by_server_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """正式身份映射落地后只能由服务端开关解除门禁。"""
    monkeypatch.setattr(settings, 'GROWTH_PROJECT_V4_ENTERPRISE_ENABLED', True)

    assert_project_scope_enabled('enterprise')


def test_unknown_owner_scope_is_rejected() -> None:
    """未知归属范围不能回落为个人模式。"""
    with pytest.raises(errors.RequestError) as exc_info:
        assert_project_scope_enabled('unknown')

    assert exc_info.value.data == {'error_code': 'GROWTH_OWNER_SCOPE_INVALID'}
