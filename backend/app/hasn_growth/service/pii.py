"""获客 PII 脱敏工具（设计 07 §10.2：分身不接触明文联系方式）。

读类工具与 Owner 普通列表/详情都返回脱敏 PII（`138****0000` / `z***@example.com`）。
`growth:pii` 历史 scope 已退役，manual_assist 素材包也不含联系方式；Owner 明文只走单渠道 reveal。
"""

from __future__ import annotations

import re

from typing import Any

_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE_CANDIDATE_RE = re.compile(r'(?<!\d)\+?\d[\d\s().-]{6,}\d(?!\d)')
_SENSITIVE_PII_KEYS = frozenset({
    'address',
    'contactname',
    'contactnameciphertext',
    'contacttitle',
    'customername',
    'email',
    'emailnormalized',
    'imhandle',
    'mobile',
    'phone',
    'phonenumber',
    'phonenormalized',
    'tel',
    'telephone',
    'titleciphertext',
    'valueciphertext',
    'wechat',
    'whatsapp',
})


def normalize_pii_key(value: Any) -> str:
    """把 snake_case、camelCase 和连字符字段统一为无分隔小写键。"""
    return re.sub(r'[^a-z0-9]', '', str(value).strip().casefold())


def is_sensitive_pii_key(value: Any) -> bool:
    """判断字段名是否承载联系人身份或联系渠道。"""
    return normalize_pii_key(value) in _SENSITIVE_PII_KEYS


def _is_phone_candidate(value: str) -> bool:
    digits = ''.join(character for character in value if character.isdigit())
    if not 8 <= len(digits) <= 15:
        return False
    if value.lstrip().startswith('+'):
        return True
    if len(digits) == 11 and digits.startswith('1') and digits[1] in '3456789':
        return True
    return len(digits) == 10 and '(' in value and ')' in value


def is_numeric_phone(value: Any) -> bool:
    """识别 JSON 数值形式的中国大陆手机号，避免绕过字符串正则。"""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and len(str(value)) == 11
        and str(value).startswith('1')
        and str(value)[1] in '3456789'
    )


def _redact_contact_text(value: str) -> str:
    redacted = _EMAIL_RE.sub('[已脱敏邮箱]', value)
    return _PHONE_CANDIDATE_RE.sub(
        lambda match: '[已脱敏电话]' if _is_phone_candidate(match.group(0)) else match.group(0),
        redacted,
    )


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
    """递归清理自由文本、敏感字段别名和数值手机号副本。"""
    if isinstance(value, str):
        return _redact_contact_text(value)
    if is_numeric_phone(value):
        return None
    if isinstance(value, dict):
        return {
            key: None if is_sensitive_pii_key(key) and item is not None else redact_pii_value(item)
            for key, item in value.items()
        }
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
        'tags',
    ):
        if field in out:
            out[field] = redact_pii_value(out.get(field))
    # im_refs 可能含明文句柄，脱敏时整体抹掉（句柄发送由系统侧解析，不经过 LLM）。
    if not reveal and out.get('im_refs'):
        out['im_refs'] = {'_masked': True}
    return out
