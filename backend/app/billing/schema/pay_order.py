from datetime import datetime

from pydantic import ConfigDict, Field, model_validator

from backend.common.schema import SchemaBase

# ========== 创建订单（用户端） ==========


class CreatePayOrderParam(SchemaBase):
    """创建支付订单参数（用户端）

    可辨识联合：`order_type` 决定必填字段——
    - `subscribe`：必填 `tier`（套餐归一枚举 free/pro/advanced/flagship），可选 `billing_cycle`；
    - `credit_pack`：必填 `package_id`，从 `credit_package` 取价下单（到账回调 `handle_credit_pack_paid` 发积分）；
    - `app_purchase`：必填 `app_id`，从 `hasn_app_catalog` 取价下单（到账回调 `handle_app_purchase_paid` 写权益）；
    - `lead_pack`：必填 `lead_count`，按条计价（`GROWTH_LEAD_UNIT_PRICE_FEN`）下单（到账回调
      `handle_lead_pack_paid` 增加可领取线索额度）。线索是独立支付商品，**不走 new-api 积分**（doc93 §4.2）。
    """
    order_type: str = Field('subscribe', description='订单类型 subscribe | credit_pack | app_purchase | lead_pack | app_seat')
    channel_code: str = Field(description='支付渠道编码 wx_native/alipay_qr/alipay_pc')
    # 订阅时必填
    tier: str | None = Field(None, description='order_type=subscribe 时必填，目标套餐 free/pro/advanced/flagship')
    billing_cycle: str = Field('monthly', description='计费周期 monthly/yearly')
    auto_renew: bool = Field(True, description='是否开通自动续费（v1 桌面端恒 false）')
    # 积分包时必填
    package_id: int | None = Field(None, description='order_type=credit_pack 时必填，积分包 ID')
    # AI-Native 应用购买时必填
    app_id: str | None = Field(None, description='order_type=app_purchase/app_seat 时必填，应用目录 app_id')
    # 线索购买时必填（doc93 §4.2 线索付费）
    lead_count: int | None = Field(None, description='order_type=lead_pack 时必填，购买线索条数（按条计价）')
    # 企业席位购买时必填（doc04 §6.4）
    enterprise_id: int | None = Field(None, description='order_type=app_seat 时必填，下单企业 ID')
    seats: int | None = Field(None, description='order_type=app_seat 时必填，购买席位数（>0）')

    @model_validator(mode='after')
    def _validate_cross_fields(self) -> 'CreatePayOrderParam':
        if self.order_type == 'subscribe':
            if not self.tier:
                raise ValueError('order_type=subscribe 时必须提供 tier')
        elif self.order_type == 'credit_pack':
            if not self.package_id:
                raise ValueError('order_type=credit_pack 时必须提供 package_id')
        elif self.order_type == 'app_purchase':
            if not self.app_id:
                raise ValueError('order_type=app_purchase 时必须提供 app_id')
        elif self.order_type == 'lead_pack':
            if not self.lead_count or self.lead_count <= 0:
                raise ValueError('order_type=lead_pack 时必须提供 lead_count（>0）')
        elif self.order_type == 'app_seat':
            if not self.app_id:
                raise ValueError('order_type=app_seat 时必须提供 app_id')
            if self.enterprise_id is None:
                raise ValueError('order_type=app_seat 时必须提供 enterprise_id')
            if not self.seats or self.seats <= 0:
                raise ValueError('order_type=app_seat 时必须提供 seats（>0）')
        else:
            raise ValueError(
                f'不支持的 order_type: {self.order_type}'
                '（仅 subscribe | credit_pack | app_purchase | lead_pack | app_seat）'
            )
        return self


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
    # 统一商业化内核（MK-1）：商品目录引用快照，商业化中心订单页据此串出所属 offering
    offering_ref: dict | None = None
    created_time: datetime
    updated_time: datetime | None = None


# ========== 退款 ==========

class RefundOrderParam(SchemaBase):
    """退款参数"""
    reason: str | None = Field(None, description='退款原因')
    refund_amount: int | None = Field(None, description='退款金额（分），不传则全额退款')


class RefundOrderResponse(SchemaBase):
    """退款响应"""
    order_no: str = Field(description='商户订单号')
    refund_no: str = Field(description='退款单号')
    refund_amount: int = Field(description='退款金额（分）')
    status: int = Field(description='退款状态 0=待处理 1=成功 2=失败')
    already_refunded: bool = Field(False, description='是否为幂等命中（订单此前已退款）')


class GetPayRefundDetail(SchemaBase):
    """退款记录详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    refund_no: str
    order_no: str
    user_id: int
    refund_amount: int
    channel_code: str | None = None
    reason: str | None = None
    channel_refund_no: str | None = None
    status: int = Field(description='0=待处理 1=成功 2=失败')
    success_time: datetime | None = None
    created_time: datetime
    updated_time: datetime | None = None
