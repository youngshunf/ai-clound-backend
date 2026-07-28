"""获客跨域载荷 PII 边界测试。"""

from __future__ import annotations

import pytest

from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_boundary import (
    GrowthPiiBoundaryError,
    assert_growth_pii_payload_safe,
)


def test_safe_payload_accepts_ids_counts_enums_and_masked_values() -> None:
    assert_growth_pii_payload_safe({
        'target': {'kind': 'customer', 'id': '123'},
        'new_count': 2,
        'channel': 'email',
        'masked_value': 's***@example.com',
        'deep_link': '/growth/customers/123',
        'created_time': '2026-07-29 03:15:03',
    })


@pytest.mark.parametrize(
    'payload',
    [
        {'email': 'sales@example.com'},
        {'phone': '13800138000'},
        {'customer_name': '王小明'},
        {'contact_name': '李四'},
        {'body': '请联系 sales@example.com'},
        {'content': '请回拨 13800138000'},
        {'content': '请回拨13800138000'},
        {'content': '电话138-0013-8000联系'},
        {'profile': {'phone': 13800138000}},
        {'profile': {'contactName': '李四'}},
        {'profile': {'mobile': '+1 (415) 555-2671'}},
        {'nested': {'address': '北京市朝阳区某路 1 号'}},
    ],
)
def test_cross_domain_payload_rejects_plaintext_or_sensitive_fields(payload: dict) -> None:
    with pytest.raises(GrowthPiiBoundaryError):
        assert_growth_pii_payload_safe(payload)


def test_masked_sensitive_values_are_allowed_only_in_explicit_masked_fields() -> None:
    assert_growth_pii_payload_safe({'masked_email': 's***@example.com'})

    with pytest.raises(GrowthPiiBoundaryError):
        assert_growth_pii_payload_safe({'email': 's***@example.com'})


def test_redaction_removes_nested_aliases_numeric_phones_and_tags() -> None:
    redacted = redact_pii_value({
        'profile': {
            'contactName': '李四',
            'mobile': 13800138000,
            'nested': [{'telephone': '+1 (415) 555-2671'}],
        },
        'tags': ['重点客户', '电话138-0013-8000'],
    })

    assert '李四' not in str(redacted)
    assert '13800138000' not in str(redacted)
    assert '415' not in str(redacted)
    assert '138-0013-8000' not in str(redacted)


def test_growth_runtime_logs_do_not_interpolate_exception_messages() -> None:
    """异常消息可能携带请求内容，生产日志只能记录异常类名或受控错误码。"""
    from pathlib import Path

    service_dir = Path(__file__).parents[1] / 'service'
    offenders = []
    for path in service_dir.glob('*.py'):
        source = path.read_text(encoding='utf-8')
        if 'exc!r' in source or "result.get('message')" in source:
            offenders.append(path.name)
    assert offenders == []
