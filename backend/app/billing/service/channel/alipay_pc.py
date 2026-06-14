"""支付宝 PC 网页支付 — PayClient 实现

支持两种模式：
  mode=1  公钥模式（AliPay）
  mode=2  证书模式（DCAliPay）

config JSONB 字段说明:
{
    "appId": "支付宝开放平台应用ID",
    "serverUrl": "网关地址",
    "signType": "RSA2",
    "mode": 1,                     // 1=公钥模式, 2=证书模式
    "privateKey": "应用私钥",
    "alipayPublicKey": "支付宝公钥字符串(mode=1)",
    "appCertContent": "商户公钥应用证书(mode=2)",
    "alipayPublicCertContent": "支付宝公钥证书(mode=2)",
    "rootCertContent": "支付宝根证书(mode=2)",
    "encryptType": "",             // '' 或 'AES'
    "encryptKey": "接口内容加密密钥(AES)",
    "returnUrl": "同步跳转地址",
}
"""

import json
import textwrap
from typing import Any

from backend.app.billing.service.channel.base import PayClient
from backend.common.log import log


def _wrap_pem(key_string: str, key_type: str = 'PRIVATE') -> str:
    """把裸 base64 字符串包装为完整 PEM 格式

    支付宝开放平台生成的密钥是 PKCS#8 格式，对应 PEM 头为:
      私钥: -----BEGIN PRIVATE KEY-----      (不是 RSA PRIVATE KEY)
      公钥: -----BEGIN PUBLIC KEY-----

    python-alipay-sdk 底层的 RSA.importKey() 要求完整 PEM 格式，
    不包装就会报 'RSA key format is not supported'；
    用错 PKCS#1 头(RSA PRIVATE KEY) 包 PKCS#8 内容会导致验签失败(invalid-signature)。

    :param key_string: 裸 base64 或已带 PEM 头尾的密钥
    :param key_type:   'PRIVATE' / 'PUBLIC'
    """
    if not key_string or not key_string.strip():
        return key_string

    s = key_string.strip()

    # 已经是 PEM 格式，直接返回
    if s.startswith('-----BEGIN'):
        return s

    # 清理可能的换行/空格
    raw = s.replace('\n', '').replace('\r', '').replace(' ', '')

    # 每 64 字符换一行
    body = '\n'.join(textwrap.wrap(raw, 64))

    if key_type == 'PRIVATE':
        # 支付宝开放平台私钥 = PKCS#8 格式
        return f'-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----'
    else:
        return f'-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----'


class AlipayPcClient(PayClient):
    """支付宝 PC 网页支付客户端 — 公钥模式 + 证书模式"""

    def __init__(self, config: dict, notify_url: str):
        super().__init__(config, notify_url)
        self.return_url = config.get('return_url') or config.get('returnUrl', '')
        self._client = None

        # 兼容前端两种字段命名风格（camelCase 和 snake_case）
        self._app_id = config.get('appId') or config.get('app_id', '')
        self._sign_type = config.get('signType') or config.get('sign_type', 'RSA2')
        self._server_url = config.get('serverUrl') or config.get('server_url', '')
        self._is_debug = 'sandbox' in self._server_url if self._server_url else config.get('debug', False)

        # 密钥 — 自动包装 PEM 头尾
        raw_private = config.get('privateKey') or config.get('app_private_key', '')
        raw_public = config.get('alipayPublicKey') or config.get('alipay_public_key', '')
        self._private_key = _wrap_pem(raw_private, 'PRIVATE')
        self._public_key = _wrap_pem(raw_public, 'PUBLIC')

        # 公钥类型：1=公钥模式, 2=证书模式
        self._mode = config.get('mode', 1)

        # 证书模式参数（证书本身是完整 PEM，不需要 wrap）
        self._app_cert_content = config.get('appCertContent') or config.get('app_cert_content', '')
        self._alipay_public_cert_content = config.get('alipayPublicCertContent') or config.get('alipay_public_cert_content', '')
        self._root_cert_content = config.get('rootCertContent') or config.get('root_cert_content', '')

        # 接口内容加密
        self._encrypt_type = config.get('encryptType') or config.get('encrypt_type', '')
        self._encrypt_key = config.get('encryptKey') or config.get('encrypt_key', '')

    @staticmethod
    def _patch_ordered_data(alipay_client):
        """修复 python-alipay-sdk 的 _ordered_data 中 json.dumps 默认
        ensure_ascii=True 导致中文被转义为 \\uXXXX 的问题。

        支付宝网关验签使用 UTF-8 原文，而 SDK 用转义后的字符串签名，
        两边签名原文不一致 → invalid-signature。
        """
        def _ordered_data_fixed(data):
            for k, v in data.items():
                if isinstance(v, dict):
                    data[k] = json.dumps(v, separators=(',', ':'), ensure_ascii=False)
            return sorted(data.items())

        alipay_client._ordered_data = _ordered_data_fixed

    @property
    def client(self):
        if self._client is None:
            if not self._private_key:
                raise Exception('支付宝应用私钥未配置')

            if self._mode == 2:
                # 证书模式 — 使用 DCAliPay
                if not self._app_cert_content:
                    raise Exception('证书模式需配置：商户公钥应用证书')
                if not self._alipay_public_cert_content:
                    raise Exception('证书模式需配置：支付宝公钥证书')
                if not self._root_cert_content:
                    raise Exception('证书模式需配置：支付宝根证书')

                from alipay import DCAliPay
                self._client = DCAliPay(
                    appid=self._app_id,
                    app_notify_url=self.notify_url,
                    app_private_key_string=self._private_key,
                    app_public_key_cert_string=self._app_cert_content,
                    alipay_public_key_cert_string=self._alipay_public_cert_content,
                    alipay_root_cert_string=self._root_cert_content,
                    sign_type=self._sign_type,
                    debug=self._is_debug,
                )
                self._patch_ordered_data(self._client)
            else:
                # 公钥模式 — 使用 AliPay
                if not self._public_key:
                    raise Exception('公钥模式需配置：支付宝公钥')

                from alipay import AliPay
                self._client = AliPay(
                    appid=self._app_id,
                    app_notify_url=self.notify_url,
                    app_private_key_string=self._private_key,
                    alipay_public_key_string=self._public_key,
                    sign_type=self._sign_type,
                    debug=self._is_debug,
                )
                self._patch_ordered_data(self._client)
        return self._client

    @property
    def gateway(self) -> str:
        if self._server_url:
            return self._server_url
        if self._is_debug:
            return 'https://openapi-sandbox.dl.alipaydev.com/gateway.do'
        return 'https://openapi.alipay.com/gateway.do'

    def create_order(
        self,
        order_no: str,
        amount: int,
        subject: str,
        body: str = '',
        user_ip: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # 支付宝金额单位是元
        amount_yuan = f'{amount / 100:.2f}'
        order_string = self.client.api_alipay_trade_page_pay(
            out_trade_no=order_no,
            total_amount=amount_yuan,
            subject=subject,
            body=body,
            return_url=kwargs.get('return_url') or self.return_url,
        )
        pay_url = f'{self.gateway}?{order_string}'
        return {'qr_code_url': None, 'pay_url': pay_url}

    def query_order(self, order_no: str) -> dict[str, Any]:
        return self.client.api_alipay_trade_query(out_trade_no=order_no)

    def close_order(self, order_no: str) -> bool:
        result = self.client.api_alipay_trade_close(out_trade_no=order_no)
        return result.get('code') == '10000'

    def refund(
        self,
        order_no: str,
        refund_no: str,
        refund_amount: int,
        total_amount: int,
        reason: str = '',
        **kwargs: Any,
    ) -> dict[str, Any]:
        amount_yuan = f'{refund_amount / 100:.2f}'
        result = self.client.api_alipay_trade_refund(
            out_trade_no=order_no,
            refund_amount=amount_yuan,
            refund_reason=reason,
            out_request_no=refund_no,
        )
        if result.get('code') == '10000':
            return result
        raise Exception(f'支付宝退款失败: {result.get("sub_msg", result.get("msg"))}')

    def verify_callback(self, headers: dict, body: str | dict) -> dict[str, Any]:
        """验签支付宝回调"""
        if isinstance(body, str):
            data = json.loads(body)
        else:
            data = dict(body)
        verify_data = dict(data)
        signature = verify_data.pop('sign', '')
        verify_data.pop('sign_type', None)
        if self.client.verify(verify_data, signature):
            return data
        raise Exception('支付宝回调验签失败')
