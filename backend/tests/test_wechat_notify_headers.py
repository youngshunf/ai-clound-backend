"""微信支付回调必须把验签头**完整**交给 wechatpayv3（2026-08-21 真实 1 分钱支付定位）。

事故形状：`notify.py` 手工挑了 4 个头（Timestamp / Nonce / Signature / Serial），
漏了 `Wechatpay-Signature-Type`。而 `wechatpayv3/core.py::_verify_signature` 的**第一件事**是

    signature_type = headers.get(signature_type_mark, '')
    if signature_type != 'WECHATPAY2-SHA256-RSA2048':
        raise Exception(f'wechatpayv3 does not support this algorithm: {signature_type}')

于是**每一个**微信回调都在验签之前就抛异常 —— 微信侧看到 500 就按 15s/15s/30s/3m/10m…
的节奏一直重试，而用户那边订单永远停在「等待支付」。

**为什么本地测试当时全绿**：三个回调 handler 里的头是硬编码字典，没有任何测试断言过
「交给库的那份头长什么样」；库那侧的前置条件也没人在我们这边复述过。两侧各自自洽，
中间那一格是空的。

这批断言把那一格填上：直接拿**微信真实发来的头集合**构造请求，断言交给库的字典能满足
库自己的前置条件。改回「手挑 N 个头」的写法即红。
"""

from __future__ import annotations

import pytest

from starlette.requests import Request

from backend.app.billing.api.v1.open.notify import _wechat_headers

#: 微信支付 v3 回调真实携带的头（2026-08-21 生产实测，值已脱敏但键名照原样）。
_WECHAT_CALLBACK_HEADERS = {
    b'host': b'api.huanxing.dcfuture.cn',
    b'content-type': b'application/json',
    b'user-agent': b'Mozilla/4.0',
    b'wechatpay-nonce': b'ZlLQmFqnCvHuKcQK',
    b'wechatpay-signature': b'GgIrN9pl0S0kkZ2j9xQ==',
    b'wechatpay-timestamp': b'1787315249',
    b'wechatpay-serial': b'66F50EDB2C2167D2A4B7602ABD27D04F291F0A0B',
    b'wechatpay-signature-type': b'WECHATPAY2-SHA256-RSA2048',
}

#: wechatpayv3 认得的三套头命名（原生 / django / fastapi），见其 `_verify_signature`。
_SIGNATURE_TYPE_MARKS = (
    'Wechatpay-Signature-Type',
    'HTTP_WECHATPAY_SIGNATURE_TYPE',
    'wechatpay-signature-type',
)


def _make_request(headers: dict[bytes, bytes]) -> Request:
    return Request({
        'type': 'http',
        'method': 'POST',
        'path': '/api/v1/pay/open/notify/2',
        'headers': list(headers.items()),
    })


def test_signature_type_reaches_the_library() -> None:
    """库的前置条件必须被满足：签名类型头要在，且值恰好是它认的那个常量。

    这是本次事故的直接判据——少这一个头，验签**根本不会开始**。
    """
    headers = _wechat_headers(_make_request(_WECHAT_CALLBACK_HEADERS))

    resolved = next((headers[m] for m in _SIGNATURE_TYPE_MARKS if m in headers), None)
    assert resolved is not None, (
        f'交给 wechatpayv3 的头里没有签名类型（它认的键名: {_SIGNATURE_TYPE_MARKS}）。'
        f'实际交过去的键: {sorted(headers)}'
    )
    assert resolved == 'WECHATPAY2-SHA256-RSA2048'


def test_all_four_signature_inputs_still_reach_the_library() -> None:
    """补齐签名类型的同时，原本那四个头一个都不能丢。"""
    headers = _wechat_headers(_make_request(_WECHAT_CALLBACK_HEADERS))

    for name in ('wechatpay-timestamp', 'wechatpay-nonce', 'wechatpay-signature', 'wechatpay-serial'):
        assert headers.get(name), f'验签必需的头 {name} 没有交给库'


def test_headers_are_passed_through_not_hand_picked() -> None:
    """整份透传，不是手挑固定几个。

    判据是「请求里有的头，交出去的字典里也得有」——手挑写法必然丢掉未列举的那些，
    而下一个被微信新增的头会不会正好是必需的，我们事先并不知道。
    这条断言让「又漏一个头」在测试期就红，而不是等生产上用户付了钱才发现。
    """
    request = _make_request(_WECHAT_CALLBACK_HEADERS)
    headers = _wechat_headers(request)

    missing = [k.decode() for k in _WECHAT_CALLBACK_HEADERS if k.decode() not in headers]
    assert not missing, f'这些请求头没有交给库: {missing}'


@pytest.mark.parametrize('absent', ['wechatpay-signature-type'])
def test_guard_is_falsifiable(absent: str) -> None:
    """守卫自检：头真缺席时上面那条断言必须红，而不是恒真。"""
    stripped = {k: v for k, v in _WECHAT_CALLBACK_HEADERS.items() if k.decode() != absent}
    headers = _wechat_headers(_make_request(stripped))

    assert not any(m in headers for m in _SIGNATURE_TYPE_MARKS), (
        '构造的「缺头」场景里签名类型仍然存在，说明这条自检没有真的把它拿掉'
    )
