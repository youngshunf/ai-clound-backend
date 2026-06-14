"""支付宝当面付（扫码）— PayClient 实现

复用 `AlipayPcClient` 的全部客户端构造、验签、查单、关单、退款逻辑，
仅 `create_order` 改用 `alipay.trade.precreate`（当面付预下单）返回可扫二维码 `qr_code`，
落到 `CreatePayOrderResponse.qr_code_url`，桌面端即可与微信一致地内渲二维码扫码支付。

与 PC 网页支付（`alipay_pc`）的唯一差异：
- `alipay_pc` 走 `api_alipay_trade_page_pay` → 返回 PC 网页跳转 URL（`pay_url`，非二维码）；
- `alipay_qr` 走 `api_alipay_trade_precreate` → 返回可扫二维码内容（`qr_code` → `qr_code_url`）。

回调验签 / 查单 / 取消复用支付宝既有逻辑（precreate 与 page_pay 同验签），
故只继承覆写 `create_order` 一个方法。
"""

from typing import Any

from backend.app.billing.service.channel.alipay_pc import AlipayPcClient
from backend.common.log import log


class AlipayQrClient(AlipayPcClient):
    """支付宝当面付（扫码）客户端 — 出可扫二维码，应用内呈现。"""

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
        result = self.client.api_alipay_trade_precreate(
            out_trade_no=order_no,
            total_amount=amount_yuan,
            subject=subject,
            body=body,
        )
        # precreate 是真实 API 调用，返回解析后的 dict：
        # 成功 {'code': '10000', 'qr_code': 'https://qr.alipay.com/...', ...}
        if result.get('code') == '10000':
            return {'qr_code_url': result.get('qr_code'), 'pay_url': None}
        log.error(f'支付宝当面付下单失败: order_no={order_no}, result={result}')
        raise Exception(
            f'支付宝当面付下单失败: {result.get("sub_msg") or result.get("msg")}'
        )
