"""获客 PII 脱敏工具（设计 07 §10.2：分身默认不接触明文联系方式）。

读类工具默认返回脱敏 PII（`138****0000` / `z***@example.com`）；仅持 `growth:pii` 升级 scope
或 owner 审批通过的 manual_assist 素材包才回明文。脱敏在 service 出参层统一施加（API 传 reveal_pii）。
"""

from __future__ import annotations


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


# 联系方式字段集合：reveal=False 时统一脱敏。
_CONTACT_FIELDS = ('email', 'phone', 'wechat')


def mask_contact_fields(data: dict, *, reveal: bool) -> dict:
    """对 dict 中的联系方式字段脱敏（reveal=True 直接返回原 dict 浅拷贝）。

    不可变模式：返回新 dict，不改入参。
    """
    out = dict(data)
    if reveal:
        return out
    if 'email' in out:
        out['email'] = mask_email(out.get('email'))
    if 'phone' in out:
        out['phone'] = mask_phone(out.get('phone'))
    if 'wechat' in out:
        out['wechat'] = mask_wechat(out.get('wechat'))
    # im_refs 可能含明文句柄，脱敏时整体抹掉（句柄发送由系统侧解析，不过 LLM）
    if not reveal and out.get('im_refs'):
        out['im_refs'] = {'_masked': True}
    return out
