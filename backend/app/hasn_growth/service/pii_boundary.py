"""获客数据离开业务权威表前的 PII 纵深边界。

通知、审计、任务、产物元数据和缓存不承担联系人明文权威存储职责，因此只允许
稳定 ID、受控枚举、计数以及显式命名的脱敏字段。联系人明文只能留在私有密文表，
Owner 单渠道 reveal 响应不得经过本边界后的持久化通道。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from backend.app.hasn_growth.service.pii import (
    is_numeric_phone,
    is_sensitive_pii_key,
    normalize_pii_key,
    redact_pii_value,
)

_MASKED_KEYS = frozenset({
    'maskedemail',
    'maskedphone',
    'maskedvalue',
    'maskedwechat',
})


class GrowthPiiBoundaryError(ValueError):
    """跨域载荷包含联系人明文或禁止字段。"""


def _normalized_key(value: Any) -> str:
    return normalize_pii_key(value)


def _assert_safe(value: Any, *, path: tuple[str, ...]) -> None:
    if isinstance(value, str):
        if redact_pii_value(value) != value:
            location = '.'.join(path) or '<root>'
            raise GrowthPiiBoundaryError(f'获客跨域载荷包含明文 PII：{location}')
        return
    if is_numeric_phone(value):
        location = '.'.join(path) or '<root>'
        raise GrowthPiiBoundaryError(f'获客跨域载荷包含数值手机号：{location}')
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = _normalized_key(raw_key)
            if is_sensitive_pii_key(key) and item is not None:
                location = '.'.join((*path, key))
                raise GrowthPiiBoundaryError(f'获客跨域载荷包含禁止字段：{location}')
            if key in _MASKED_KEYS:
                _assert_safe(item, path=(*path, key))
                continue
            _assert_safe(item, path=(*path, key))
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for index, item in enumerate(value):
            _assert_safe(item, path=(*path, str(index)))


def assert_growth_pii_payload_safe(payload: Any) -> None:
    """拒绝跨域载荷中的联系人明文及敏感字段名。"""
    _assert_safe(payload, path=())
