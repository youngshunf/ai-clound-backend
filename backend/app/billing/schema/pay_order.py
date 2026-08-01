from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import ConfigDict, Field, model_validator

from backend.app.billing.core.fulfillment import ORDER_TYPE_OFFERING
from backend.common.schema import SchemaBase

# ========== 创建订单（用户端） ==========


class CreatePayOrderParam(SchemaBase):
    """创建支付订单参数（用户端）

    **新商品一律走通用路 `order_type='offering'`**（G2 · 实施/95）：只带 `offering_key` +
    `plan_key` + `quantity`，价格、周期、配额一律由服务端从商品目录（`billing_offering` /
    `billing_plan`）读出，`kind` 也取自目录。于是「上一个加购/买断型商品」退化为**加库行 +
    注册一个 feature_key**，不必再改支付主干、不必再加一个特例分支。

    存量五个 `order_type` 是**历史可辨识联合**，保持不动（兼容既有客户端），各自必填字段——
    - `subscribe`：必填 `tier`（套餐归一枚举 free/pro/advanced/flagship），可选 `billing_cycle`；
    - `credit_pack`：必填 `package_id`，从商品目录取价下单（到账回调 `handle_credit_pack_paid` 发积分）；
    - `app_purchase`：必填 `app_id`，从 `hasn_app_catalog` 取价下单（到账回调 `handle_app_purchase_paid` 写权益）；
    - `lead_pack`：必填 `lead_count`，按条计价（`GROWTH_LEAD_UNIT_PRICE_FEN`）下单（到账回调
      `handle_lead_pack_paid` 增加可领取线索额度）。线索是独立支付商品，**不走 new-api 积分**（doc93 §4.2）；
    - `app_seat`：必填 `app_id` + `enterprise_id` + `seats`。

    **本参数里没有任何价格字段，这是刻意的**：金额只能由服务端按目录算，客户端传什么都不作数。
    """
    order_type: str = Field(
        'subscribe',
        description='订单类型 offering（通用路·新商品用）| subscribe | credit_pack '
        '| app_purchase | lead_pack | app_seat',
    )
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
    # 通用商品下单时必填（G2 · 实施/95）：只指认「买哪个商品的哪个档」，价格由服务端从目录读
    offering_key: str | None = Field(
        None, description='order_type=offering 时必填，商品业务键（billing_offering.key，如 cloud:node）'
    )
    plan_key: str | None = Field(
        None, description='order_type=offering 时必填，档位键（billing_plan.plan_key，如 monthly/yearly/lifetime）'
    )
    quantity: int = Field(
        1, description='order_type=offering 时的购买份数（正整数，缺省 1；金额与配额均按份数倍乘）'
    )

    @model_validator(mode='after')
    def _validate_cross_fields(self) -> CreatePayOrderParam:
        """按 `order_type` 分派到各自的必填校验（每类一个小函数，见下方注册表）。

        原先是一长串 `elif`，加一类商品就长一截；改成注册表后，通用路的加入没有让这个方法更复杂。
        各分支的报错文案逐字不变（存量客户端按文案做过提示）。
        """
        validate = _ORDER_TYPE_VALIDATORS.get(self.order_type)
        if validate is None:
            raise ValueError(
                f'不支持的 order_type: {self.order_type}'
                '（仅 offering | subscribe | credit_pack | app_purchase | lead_pack | app_seat）'
            )
        validate(self)
        return self


# ── 各 order_type 的必填校验（`CreatePayOrderParam._validate_cross_fields` 的分派目标） ──


def _validate_offering(param: CreatePayOrderParam) -> None:
    """通用路只校验「三件套齐不齐」。

    商品是否存在 / 是否上架 / 档位是否有价，一律由服务端查目录后给明确 4xx——
    schema 不该也不能知道目录内容。
    """
    if not param.offering_key:
        raise ValueError('order_type=offering 时必须提供 offering_key')
    if not param.plan_key:
        raise ValueError('order_type=offering 时必须提供 plan_key')
    if param.quantity <= 0:
        raise ValueError(f'order_type=offering 时购买份数必须为正整数（收到 {param.quantity}）')


def _validate_subscribe(param: CreatePayOrderParam) -> None:
    if not param.tier:
        raise ValueError('order_type=subscribe 时必须提供 tier')


def _validate_credit_pack(param: CreatePayOrderParam) -> None:
    if not param.package_id:
        raise ValueError('order_type=credit_pack 时必须提供 package_id')


def _validate_app_purchase(param: CreatePayOrderParam) -> None:
    if not param.app_id:
        raise ValueError('order_type=app_purchase 时必须提供 app_id')


def _validate_lead_pack(param: CreatePayOrderParam) -> None:
    if not param.lead_count or param.lead_count <= 0:
        raise ValueError('order_type=lead_pack 时必须提供 lead_count（>0）')


def _validate_app_seat(param: CreatePayOrderParam) -> None:
    if not param.app_id:
        raise ValueError('order_type=app_seat 时必须提供 app_id')
    if param.enterprise_id is None:
        raise ValueError('order_type=app_seat 时必须提供 enterprise_id')
    if not param.seats or param.seats <= 0:
        raise ValueError('order_type=app_seat 时必须提供 seats（>0）')


_ORDER_TYPE_VALIDATORS: dict[str, Callable[[CreatePayOrderParam], None]] = {
    ORDER_TYPE_OFFERING: _validate_offering,
    'subscribe': _validate_subscribe,
    'credit_pack': _validate_credit_pack,
    'app_purchase': _validate_app_purchase,
    'lead_pack': _validate_lead_pack,
    'app_seat': _validate_app_seat,
}


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
    """订单状态响应。

    **支付成功 ≠ 额度到账**（doc94 §2.2）：云端事务只登记履约命令，真正的发放由 outbox
    worker 投给 NewAPI。所以「付款状态」和「履约状态」必须是两个独立字段——
    只回 `status=1` 会让 UI 在额度还没到账时就显示「已完成」，用户马上去用却被 403 挡住。
    """
    order_no: str
    status: int = Field(description='0=待支付 1=已支付 2=已退款 3=已关闭 4=已过期')
    pay_amount: int = Field(description='实付金额（分）')
    success_time: datetime | None = None
    fulfillment_status: str | None = Field(
        None,
        description='履约状态 not_required/pending/processing/succeeded/retrying/dead/reversed；'
        '只有 succeeded 才是「额度已到账」',
    )
    fulfilled_at: datetime | None = Field(None, description='额度真正到账的时刻')


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
    # 支付与履约分列（doc94 §2.2）：列表页也要能区分「已付款」与「额度已到账」，
    # 与 PayOrderStatusResponse 同一组字段，避免订单页把「发放中」误显示成「已完成」。
    fulfillment_status: str | None = Field(
        None,
        description='履约状态 not_required/pending/processing/succeeded/retrying/dead/reversed；'
        '只有 succeeded 才是「额度已到账」',
    )
    fulfilled_at: datetime | None = Field(None, description='额度真正到账的时刻')
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
