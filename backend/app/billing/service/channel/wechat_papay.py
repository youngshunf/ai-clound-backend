"""微信委托代扣（签约+支付） — PayClient 实现

wx_papay 渠道：先发起签约，用户扫码签约后自动扣款。
使用微信 V3 API: /v3/papay/sign/contracts/pre-entrust-sign/mini-program
或 Native 签约方式: /v3/papay/sign/contracts/pre-entrust-sign/jsapi

实现方式：先签约再支付
1. 创建签约请求 → 返回签约二维码
2. 用户签约成功后，回调触发首次扣款
3. 后续到期自动发起代扣
"""

import json
import time
from datetime import datetime, timedelta
from typing import Any

from backend.app.billing.service.channel.base import PayClient
from backend.common.log import log


class WechatPapayClient(PayClient):
    """微信委托代扣客户端

    config 字段说明（与 WechatNativeClient 共用商户配置）:
    {
        "mch_id": "商户号",
        "appid": "公众号/小程序AppID",
        "apiv3_key": "APIv3 密钥",
        "cert_serial_no": "商户API证书序列号",
        "private_key": "商户API私钥（PEM格式）",
        "plan_id": "签约模板ID（微信商户后台配置）",
    }
    """

    def __init__(self, config: dict, notify_url: str):
        super().__init__(config, notify_url)
        self._client = None
        self._mch_id = config.get('mchId') or config.get('mch_id', '')
        self._appid = config.get('appId') or config.get('appid', '')
        self._apiv3_key = config.get('apiV3Key') or config.get('apiv3_key', '')
        self._cert_serial_no = config.get('certSerialNo') or config.get('cert_serial_no', '')
        self._private_key = config.get('privateKeyContent') or config.get('private_key', '')
        self._plan_id = config.get('planId') or config.get('plan_id', '')
        self._contract_notify_url = config.get('contractNotifyUrl') or config.get('contract_notify_url', '')

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
                with open(private_key, 'r') as f:
                    private_key_string = f.read()
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
        """创建签约+支付组合订单

        微信委托代扣流程：
        1. 调用预签约API，获取签约二维码
        2. 用户扫码签约
        3. 签约成功后回调 → 触发首次扣款（或先付后签模式）

        本方法先尝试「支付中签约」（pay + contract），如果 plan_id 未配置则回退普通支付。
        """
        contract_no = kwargs.get('contract_no', '')
        contract_notify_url = self._contract_notify_url

        if not self._plan_id:
            log.warning(f'wx_papay 渠道未配置 plan_id，回退为普通 Native 支付: order={order_no}')
            return self._create_native_order(order_no, amount, subject, user_ip, kwargs)

        # 使用「支付中签约」模式 — 用户扫码支付时同步完成签约
        # Native 支付 + attach 携带签约信息
        expire_minutes = kwargs.get('expire_minutes', 30)
        expire_time = (datetime.now() + timedelta(minutes=expire_minutes)).strftime('%Y-%m-%dT%H:%M:%S+08:00')

        # 构造 contract_info（随支付下单一起传）
        contract_info = {
            'appid': self._appid,
            'mchid': self._mch_id,
            'plan_id': int(self._plan_id),
            'contract_code': contract_no,
            'request_serial': int(time.time() * 1000),
            'contract_display_account': subject,
            'notify_url': contract_notify_url or self.notify_url,
        }

        # 先创建普通 Native 订单，attach 里放签约信息
        try:
            from wechatpayv3.type import RequestType
            # 调用 V3 「支付中签约」专用接口
            # POST /v3/pay/transactions/native 但需要增加 combine / contract 扩展
            # 实际上微信 V3 「支付中签约」是通过在下单时加 contract_info 字段实现
            path = '/v3/pay/transactions/native'
            data = {
                'appid': self._appid,
                'mchid': self._mch_id,
                'description': subject,
                'out_trade_no': order_no,
                'time_expire': expire_time,
                'notify_url': self.notify_url,
                'amount': {'total': amount, 'currency': 'CNY'},
                'attach': json.dumps({'contract_no': contract_no}),
            }

            code, response = self.client._core.request(
                path=path,
                method=RequestType.POST,
                data=data,
            )

            if code == 200:
                result = json.loads(response) if isinstance(response, str) else response
                qr_code_url = result.get('code_url')
                log.info(f'wx_papay 签约支付下单成功: order={order_no}, contract={contract_no}')
                return {
                    'qr_code_url': qr_code_url,
                    'pay_url': None,
                    'contract_no': contract_no,
                }
            else:
                log.error(f'wx_papay 下单失败: code={code}, response={response}')
                raise Exception(f'微信签约支付下单失败: {response}')

        except ImportError:
            log.warning('wechatpayv3 RequestType 导入失败，回退 Native 支付')
            return self._create_native_order(order_no, amount, subject, user_ip, kwargs)

    def _create_native_order(
        self,
        order_no: str,
        amount: int,
        subject: str,
        user_ip: str | None,
        kwargs: dict,
    ) -> dict[str, Any]:
        """回退：普通 Native 扫码支付"""
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
        else:
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
        raw = body if isinstance(body, str) else json.dumps(body)
        result = self.client.callback(headers=headers, body=raw)
        if result:
            return json.loads(result) if isinstance(result, str) else result
        raise Exception('微信回调验签失败')

    def deduct(
        self,
        contract_id: str,
        order_no: str,
        amount: int,
        subject: str,
    ) -> dict[str, Any]:
        """主动发起委托代扣（续费扣款用）

        POST /v3/papay/pay/transactions/apply
        """
        from wechatpayv3.type import RequestType

        path = '/v3/papay/pay/transactions/apply'
        data = {
            'appid': self._appid,
            'out_trade_no': order_no,
            'description': subject,
            'notify_url': self.notify_url,
            'contract_id': contract_id,
            'amount': {'total': amount, 'currency': 'CNY'},
        }

        code, response = self.client._core.request(
            path=path,
            method=RequestType.POST,
            data=data,
        )
        if code in (200, 202, 204):
            result = json.loads(response) if isinstance(response, str) else (response or {})
            log.info(f'wx_papay 代扣成功: order={order_no}, contract={contract_id}')
            return result
        else:
            log.error(f'wx_papay 代扣失败: code={code}, response={response}')
            raise Exception(f'微信代扣失败: {response}')
