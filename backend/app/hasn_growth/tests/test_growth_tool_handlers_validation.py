"""获客云端工具入参校验的纯单元测试。"""

from __future__ import annotations

import pytest

from backend.app.hasn_growth.service.growth_tool_handlers import _required_text
from backend.common.exception import errors


def test_required_text_rejects_missing_or_blank_values() -> None:
    """工具 handler 直接调用时也不得把缺失必填字段透传到服务层。"""
    assert _required_text({'content': '有效正文'}, 'content') == '有效正文'

    with pytest.raises(errors.RequestError, match='channel'):
        _required_text({}, 'channel')

    with pytest.raises(errors.RequestError, match='name'):
        _required_text({'name': '   '}, 'name')
