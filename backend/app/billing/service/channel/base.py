"""PayClient 抽象基类 — 所有支付渠道 SDK 封装的统一接口"""

from abc import ABC, abstractmethod
from typing import Any


class PayClient(ABC):
    """支付渠道客户端抽象基类"""

    def __init__(self, config: dict, notify_url: str):
        self.config = config
        self.notify_url = config.get('notify_url', notify_url)

    @abstractmethod
    def create_order(
        self,
        order_no: str,
        amount: int,
        subject: str,
        body: str = '',
        user_ip: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """创建支付订单
        
        :param order_no: 商户订单号
        :param amount: 金额（分）
        :param subject: 标题
        :param body: 描述
        :return: {"qr_code_url": ..., "pay_url": ..., ...}
        """

    @abstractmethod
    def query_order(self, order_no: str) -> dict[str, Any]:
        """查询订单状态"""

    @abstractmethod
    def close_order(self, order_no: str) -> bool:
        """关闭/撤销订单"""

    @abstractmethod
    def refund(
        self,
        order_no: str,
        refund_no: str,
        refund_amount: int,
        total_amount: int,
        reason: str = '',
        **kwargs: Any,
    ) -> dict[str, Any]:
        """退款"""

    @abstractmethod
    def verify_callback(self, headers: dict, body: str | dict) -> dict[str, Any]:
        """验签并解析回调数据
        
        :param headers: 请求头
        :param body: 请求体（微信为 str, 支付宝为 dict）
        :return: 解析后的回调数据
        """
