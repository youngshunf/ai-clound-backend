"""获客 PII 脱敏工具（设计 07 §10.2：分身不接触明文联系方式）。

读类工具与 Owner 普通列表/详情都返回脱敏 PII（`138****0000` / `z***@example.com`）。
`growth:pii` 历史 scope 已退役，manual_assist 素材包也不含联系方式；Owner 明文只走单渠道 reveal。
"""

from __future__ import annotations

import re

from typing import Any

_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE_RE = re.compile(r'(?:\+\d{8,15}|\b1[3-9]\d{9}\b)')


def mask_name(value: str | None) -> str | None:
    """姓名只保留首字符，长度不足时仍补一个掩码。"""
    if not value:
        return value
    return value[0] + '*' * max(1, len(value) - 1)


def mask_email(value: str | None) -> str | None:
    if not value or '@' not in value:
        return value
    local, domain = value.split('@', 1)
    return f'{local[:1]}***@{domain}'


def mask_phone(value: str | None) -> str | None:
    if not value or len(value) < 8:
        return '****' if value else value
    return f'{value[:4]}****{value[-4:]}'


def mask_wechat(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 2:
        return value[:1] + '*'
    return f'{value[:2]}***{value[-1:]}'


def redact_pii_value(value: Any) -> Any:
    """递归清理自由文本/JSON 中可识别的邮箱和手机号副本。"""
    if isinstance(value, str):
        return _PHONE_RE.sub('[已脱敏电话]', _EMAIL_RE.sub('[已脱敏邮箱]', value))
    if isinstance(value, dict):
        return {key: redact_pii_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_pii_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_pii_value(item) for item in value)
    return value


def mask_contact_fields(data: dict, *, reveal: bool) -> dict:
    """对 dict 中的联系方式字段脱敏（reveal=True 直接返回原 dict 浅拷贝）。

    不可变模式：返回新 dict，不改入参。
    """
    out = dict(data)
    if reveal:
        return out
    field_maskers = {
        'contact_name': mask_name,
        'email': mask_email,
        'email_normalized': mask_email,
        'phone': mask_phone,
        'phone_normalized': mask_phone,
        'wechat': mask_wechat,
    }
    for field, masker in field_maskers.items():
        if field in out:
            out[field] = masker(out.get(field))
    if 'address' in out:
        out['address'] = None
    for field in (
        'profile_json',
        'meta_data',
        'metadata',
        'note',
        'content',
        'error_message',
        'raw_excerpt',
    ):
        if field in out:
            out[field] = redact_pii_value(out.get(field))
    # im_refs 可能含明文句柄，脱敏时整体抹掉（句柄发送由系统侧解析，不经过 LLM）。
    if not reveal and out.get('im_refs'):
        out['im_refs'] = {'_masked': True}
    return out
