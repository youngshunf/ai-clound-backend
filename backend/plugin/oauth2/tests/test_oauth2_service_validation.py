"""OAuth2 身份字段校验的纯单元测试。"""

from __future__ import annotations

import pytest

from backend.common.exception import errors
from backend.plugin.oauth2.service.oauth2_service import _require_oauth_text


def test_require_oauth_text_normalizes_numeric_identifier() -> None:
    """第三方数字 ID 要转为稳定文本，空白值必须显式拒绝。"""
    assert _require_oauth_text(12345, field_name='第三方账号 ID') == '12345'

    with pytest.raises(errors.RequestError, match='缺失'):
        _require_oauth_text(None, field_name='第三方账号 ID')

    with pytest.raises(errors.RequestError, match='缺失'):
        _require_oauth_text('   ', field_name='用户名')
