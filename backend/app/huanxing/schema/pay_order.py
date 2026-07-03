from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

# ========== 创建订单（用户端） ==========


class CreatePayOrderParam(SchemaBase):
    """创建支付订单参数（用户端）"""
    tier: str = Field(description='目标套餐 free/pro/advanced/flagship')
    billing_cycle: str = Field('monthly', description='计费周期 monthly/yearly')
    channel_code: str = Field(description='支付渠道编码 wx_native/alipay_qr/alipay_pc')
    auto_renew: bool = Field(True, description='是否开通自动续费')


class CreatePayOrderResponse(SchemaBase):
    """创建订单响应"""
    order_no: str = Field(description='商户订单号')
    pay_amount: int = Field(description='实付金额（分）')
    channel_code: str = Field(description='渠道编码')
    # 微信 Native
    qr_code_url: str | None = Field(None, description='二维码内容（微信）')
    # 支付宝
    pay_url: str | None = Field(None, description='支付跳转 URL（支付宝）')
    # 签约
    contract_no: str | None = Field(None, description='签约协议号')
    expire_time: datetime = Field(description='订单过期时间')


# ========== 查询订单状态 ==========

class PayOrderStatusResponse(SchemaBase):
    """订单状态响应"""
    order_no: str
    status: int = Field(description='0=待支付 1=已支付 2=已退款 3=已关闭 4=已过期')
    pay_amount: int = Field(description='实付金额（分）')
    success_time: datetime | None = None


# ========== 订单列表 ==========

class GetPayOrderDetail(SchemaBase):
    """支付订单详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str
    user_id: int
    app_id: int
    channel_id: int | None = None
    channel_code: str | None = None
    order_type: str
    subject: str
    body: str | None = None
    target_tier: str | None = None
    billing_cycle: str | None = None
    amount: int
    discount_amount: int
    pay_amount: int
    refund_amount: int
    status: int
    user_ip: str | None = None
    channel_order_no: str | None = None
    channel_user_id: str | None = None
    expire_time: datetime
    success_time: datetime | None = None
    extra_data: dict | None = None
    created_time: datetime
    updated_time: datetime | None = None


# ========== 退款 ==========

class RefundOrderParam(SchemaBase):
    """退款参数"""
    reason: str | None = Field(None, description='退款原因')
    refund_amount: int | None = Field(None, description='退款金额（分），不传则全额退款')
