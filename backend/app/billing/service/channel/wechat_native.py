"""微信 Native 支付 — PayClient 实现"""

import json
import pathlib

from datetime import datetime, timedelta
from typing import Any

from backend.app.billing.service.channel.base import PayClient
from backend.common.log import log


class WechatNativeClient(PayClient):
    """微信 Native 扫码支付客户端

    config JSONB 字段说明:
    {
        "mch_id": "商户号",
        "appid": "公众号/小程序AppID",
        "apiv3_key": "APIv3 密钥（32字节）",
        "cert_serial_no": "商户API证书序列号",
        "private_key": "商户API私钥（PEM格式文本）",
        "notify_url": "支付回调地址（可覆盖默认）"
    }
    """

    def __init__(self, config: dict, notify_url: str) -> None:
        super().__init__(config, notify_url)
        self._client = None
        # 兼容前端两种字段命名风格（camelCase 和 snake_case）
        self._mch_id = config.get('mchId') or config.get('mch_id', '')
        self._appid = config.get('appId') or config.get('appid', '')
        self._apiv3_key = config.get('apiV3Key') or config.get('apiv3_key', '')
        self._cert_serial_no = config.get('certSerialNo') or config.get('cert_serial_no', '')
        self._private_key = config.get('privateKeyContent') or config.get('private_key', '')

    @property
    def client(self):
        if self._client is None:
            from wechatpayv3 import WeChatPay, WeChatPayType
            private_key = self._private_key
            if not private_key:
                raise Exception('微信支付私钥未配置')
            if private_key.startswith('-----BEGIN'):
                private_key_string = private_key
            else:
                private_key_string = pathlib.Path(private_key).read_text()
            self._client = WeChatPay(
                wechatpay_type=WeChatPayType.NATIVE,
                mchid=self._mch_id,
                private_key=private_key_string,
                cert_serial_no=self._cert_serial_no,
                apiv3_key=self._apiv3_key,
                appid=self._appid,
                notify_url=self.notify_url,
            )
        return self._client

    def create_order(
        self,
        order_no: str,
        amount: int,
        subject: str,
        body: str = '',
        user_ip: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        expire_minutes = kwargs.get('expire_minutes', 30)
        expire_time = (datetime.now() + timedelta(minutes=expire_minutes)).strftime('%Y-%m-%dT%H:%M:%S+08:00')
        code, response = self.client.pay(
            description=subject,
            out_trade_no=order_no,
            amount={'total': amount, 'currency': 'CNY'},
            time_expire=expire_time,
        )
        if code == 200:
            data = json.loads(response) if isinstance(response, str) else response
            return {'qr_code_url': data.get('code_url'), 'pay_url': None}
        log.error(f'微信下单失败: code={code}, response={response}')
        raise Exception(f'微信支付下单失败: {response}')

    def query_order(self, order_no: str) -> dict[str, Any]:
        code, response = self.client.query(out_trade_no=order_no)
        if code == 200:
            return json.loads(response) if isinstance(response, str) else response
        raise Exception(f'微信查单失败: {response}')

    def close_order(self, order_no: str) -> bool:
        code, _ = self.client.close(out_trade_no=order_no)
        return code in (200, 204)

    def refund(
        self,
        order_no: str,
        refund_no: str,
        refund_amount: int,
        total_amount: int,
        reason: str = '',
        **kwargs: Any,
    ) -> dict[str, Any]:
        code, response = self.client.refund(
            out_trade_no=order_no,
            out_refund_no=refund_no,
            amount={'refund': refund_amount, 'total': total_amount, 'currency': 'CNY'},
            reason=reason,
            notify_url=kwargs.get('refund_notify_url'),
        )
        if code == 200:
            return json.loads(response) if isinstance(response, str) else response
        raise Exception(f'微信退款失败: {response}')

    def verify_callback(self, headers: dict, body: str | dict) -> dict[str, Any]:
        """验签 + 解密微信回调"""
        raw = body if isinstance(body, str) else json.dumps(body)
        result = self.client.callback(headers=headers, body=raw)
        if result:
            return json.loads(result) if isinstance(result, str) else result
        raise Exception('微信回调验签失败')
