"""支付订单核心 Service — 创建订单、回调处理、状态查询"""

import secrets
import time

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.core.callback import dispatch_pay_success
from backend.app.billing.core.config import (
    ORDER_EXPIRE_MINUTES,
    PAY_ORDER_NOTIFY_URL,
)
from backend.app.billing.core.fulfillment import build_offering_ref, dispatch_fulfillment, reverse_fulfillment
from backend.app.billing.crud.crud_credit_package import credit_package_dao
from backend.app.billing.crud.crud_pay_channel import pay_channel_dao
from backend.app.billing.crud.crud_pay_contract import pay_contract_dao
from backend.app.billing.crud.crud_pay_merchant import pay_merchant_dao
from backend.app.billing.crud.crud_pay_notify_log import pay_notify_log_dao
from backend.app.billing.crud.crud_pay_order import pay_order_dao
from backend.app.billing.crud.crud_pay_refund import pay_refund_dao
from backend.app.billing.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.schema.pay_order import (
    CreatePayOrderParam,
    CreatePayOrderResponse,
    PayOrderStatusResponse,
    RefundOrderResponse,
)
from backend.app.billing.service.channel.base import PayClient
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data
from backend.core.conf import settings
from backend.utils.timezone import timezone


def _generate_order_no() -> str:
    ts = int(time.time() * 1000)
    rand = secrets.randbelow(10000)
    return f'HX{ts}{rand:04d}'


def _generate_contract_no() -> str:
    ts = int(time.time() * 1000)
    rand = secrets.randbelow(10000)
    return f'CT{ts}{rand:04d}'


# 客户端缓存（按 channel_id）
_client_cache: dict[int, PayClient] = {}


def _build_client(channel, merchant_config: dict | None = None) -> PayClient:
    """根据渠道编码构建 PayClient

    优先使用传入的 merchant_config，回退 channel.config（兼容旧数据）
    """
    config = merchant_config or channel.config or {}
    if not config:
        raise errors.ServerError(msg=f'渠道 {channel.code} 未配置支付密钥')
    code = channel.code
    # 合并渠道特有配置
    if channel.extra_config:
        config = {**config, **channel.extra_config}
    notify_url = f'{PAY_ORDER_NOTIFY_URL}/{channel.id}'

    if code == 'wx_papay':
        from backend.app.billing.service.channel.wechat_papay import WechatPapayClient

        return WechatPapayClient(config, notify_url)
    if code.startswith('wx'):
        from backend.app.billing.service.channel.wechat_native import WechatNativeClient

        return WechatNativeClient(config, notify_url)
    if code == 'alipay_qr':
        # 支付宝当面付（扫码）：出可扫二维码，应用内呈现（桌面端）
        from backend.app.billing.service.channel.alipay_qr import AlipayQrClient

        return AlipayQrClient(config, notify_url)
    if code.startswith('alipay'):
        from backend.app.billing.service.channel.alipay_pc import AlipayPcClient

        return AlipayPcClient(config, notify_url)
    raise errors.ServerError(msg=f'不支持的渠道: {code}')


def get_pay_client(channel, merchant_config: dict | None = None, force_new: bool = False) -> PayClient:
    """获取支付客户端（带缓存）"""
    if not force_new and channel.id in _client_cache:
        return _client_cache[channel.id]
    client = _build_client(channel, merchant_config)
    _client_cache[channel.id] = client
    return client


def clear_client_cache(channel_id: int | None = None) -> None:
    if channel_id:
        _client_cache.pop(channel_id, None)
    else:
        _client_cache.clear()


class PayOrderService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> PayOrder:
        order = await pay_order_dao.get(db, pk)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        return order

    @staticmethod
    async def get_by_order_no(*, db: AsyncSession, order_no: str) -> PayOrder:
        order = await pay_order_dao.get_by_order_no(db, order_no)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        return order

    @staticmethod
    async def get_list(
        db: AsyncSession,
        user_id: int | None = None,
        status: int | None = None,
    ) -> dict[str, Any]:
        select_stmt = await pay_order_dao.get_select(user_id=user_id, status=status)
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_status(*, db: AsyncSession, order_no: str) -> PayOrderStatusResponse:
        order = await pay_order_dao.get_by_order_no(db, order_no)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        return PayOrderStatusResponse(
            order_no=order.order_no,
            status=order.status,
            pay_amount=order.pay_amount,
            success_time=order.success_time,
        )

    @staticmethod
    async def _resolve_channel(db: AsyncSession, channel_code: str):
        """解析渠道 + 商户密钥配置。渠道不可用 → RequestError。"""
        channel = await pay_channel_dao.get_by_code(db, channel_code)
        if not channel or channel.status != 1:
            raise errors.RequestError(msg=f'支付渠道 {channel_code} 不可用')
        merchant_config = None
        if channel.merchant_id:
            merchant = await pay_merchant_dao.get(db, channel.merchant_id)
            if merchant and merchant.status == 1:
                merchant_config = merchant.config
        return channel, merchant_config

    @staticmethod
    def _invoke_channel_create(
        channel,
        merchant_config: dict | None,
        *,
        order_no: str,
        amount: int,
        subject: str,
        body: str,
        user_ip: str | None,
        contract_no: str = '',
    ) -> tuple[str | None, str | None]:
        """调渠道 SDK 下单，返回 (qr_code_url, pay_url)。SDK 失败 → ServerError。"""
        try:
            client = get_pay_client(channel, merchant_config=merchant_config)
            pay_result = client.create_order(
                order_no=order_no,
                amount=amount,
                subject=subject,
                body=body,
                user_ip=user_ip,
                contract_no=contract_no,
            )
            return pay_result.get('qr_code_url'), pay_result.get('pay_url')
        except Exception as e:
            log.error(f'SDK 下单失败: channel={channel.code}, error={e}', exc_info=True)
            raise errors.ServerError(msg=f'支付渠道调用失败: {e}')

    @staticmethod
    async def create_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None = None,
        app_code: str = 'huanxing',
    ) -> CreatePayOrderResponse:
        """创建支付订单。据 `order_type` 分支：订阅 / 积分包 / 应用购买（真实支付，零 mock）。"""
        if obj.order_type == 'credit_pack':
            return await PayOrderService._create_credit_pack_order(
                db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
            )
        if obj.order_type == 'app_purchase':
            return await PayOrderService._create_app_purchase_order(
                db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
            )
        if obj.order_type == 'lead_pack':
            return await PayOrderService._create_lead_pack_order(
                db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
            )
        if obj.order_type == 'app_seat':
            return await PayOrderService._create_app_seat_order(
                db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
            )
        return await PayOrderService._create_subscribe_order(
            db=db, user_id=user_id, obj=obj, user_ip=user_ip, app_code=app_code
        )

    @staticmethod
    async def _create_subscribe_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        if obj.billing_cycle not in ('monthly', 'yearly'):
            raise errors.RequestError(msg=f'无效的计费周期: {obj.billing_cycle}')
        tier_id = obj.tier
        if not tier_id:
            raise errors.RequestError(msg='订阅订单缺少套餐标识')

        # 从数据库读取套餐配置（按 app_code 区分应用）
        tier_config = await subscription_tier_dao.get_by_tier_name(
            db, tier_id, app_code=app_code, enabled=True
        )
        if not tier_config:
            raise errors.RequestError(msg=f'无效的套餐: {tier_id}（app={app_code}）')

        # MK-5：价格以商品目录 plan 为权威（admin 改价即时生效于新单）；plan 缺档回落 legacy 价。
        from backend.app.billing.service import offering_pricing

        monthly_key, yearly_key = offering_pricing.tier_plan_keys(tier_id)
        plan_key = yearly_key if obj.billing_cycle == 'yearly' else monthly_key
        plan_amount = await offering_pricing.plan_price(db, offering_pricing.OFFERING_LLM_TIER, plan_key)
        if plan_amount is not None:
            pay_amount = int(float(plan_amount) * 100)
        elif obj.billing_cycle == 'yearly' and tier_config.yearly_price:
            pay_amount = int(float(tier_config.yearly_price) * 100)
        else:
            pay_amount = int(float(tier_config.monthly_price) * 100)

        if pay_amount <= 0:
            raise errors.RequestError(msg='免费套餐无需支付')

        tier_name = tier_config.display_name
        cycle_name = '月付' if obj.billing_cycle == 'monthly' else '年付'
        subject = f'唤星AI-{tier_name}会员-{cycle_name}'
        body = f'{tier_name}（{cycle_name}）订阅'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'subscribe',
            'subject': subject,
            'body': body,
            'target_tier': tier_id,
            'billing_cycle': obj.billing_cycle,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {'app_code': app_code},
            # MK-3：商品目录引用快照（发货按 kind 分发）；plan_key 对齐 seed（年付档为 <tier>_yearly）。
            'offering_ref': build_offering_ref(
                'subscribe',
                offering_key='llm:tier',
                plan_key=tier_id if obj.billing_cycle == 'monthly' else f'{tier_id}_yearly',
            ),
        }
        await pay_order_dao.create(db, order_dict)

        contract_no = None
        if obj.auto_renew:
            contract_no = _generate_contract_no()
            contract_dict = {
                'user_id': user_id,
                'channel_code': channel.code,
                'contract_no': contract_no,
                'tier': tier_id,
                'billing_cycle': obj.billing_cycle,
                'deduct_amount': pay_amount,
                'status': 0,
            }
            await pay_contract_dao.create(db, contract_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel,
            merchant_config,
            order_no=order_no,
            amount=pay_amount,
            subject=subject,
            body=body,
            user_ip=user_ip,
            contract_no=contract_no or '',
        )

        return CreatePayOrderResponse(
            order_no=order_no,
            pay_amount=pay_amount,
            channel_code=channel.code,
            qr_code_url=qr_code_url,
            pay_url=pay_url,
            contract_no=contract_no,
            expire_time=expire_time,
        )

    @staticmethod
    async def _create_credit_pack_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        """积分包真实支付下单。积分数写入 `extra_data.credit_amount`，到账由
        `handle_credit_pack_paid` 回调读取并发放（购买积分 `is_purchased=True` 永不过期）。
        """
        package_id = obj.package_id
        if package_id is None:
            raise errors.RequestError(msg='积分包订单缺少积分包标识')
        package = await credit_package_dao.get(db, package_id)
        if not package or not package.enabled or package.app_code != app_code:
            raise errors.RequestError(msg=f'无效的积分包: {obj.package_id}（app={app_code}）')

        # MK-5：价格以商品目录 plan 为权威（plan_key = 积分包名）；plan 缺档回落 legacy 价。
        from backend.app.billing.service import offering_pricing

        plan_amount = await offering_pricing.plan_price(
            db, offering_pricing.OFFERING_CREDITS_TOPUP, package.package_name
        )
        pay_amount = int(float(plan_amount if plan_amount is not None else package.price) * 100)
        if pay_amount <= 0:
            raise errors.RequestError(msg='免费积分包无需支付')

        total_credits = package.credits + package.bonus_credits
        subject = f'唤星AI-积分包-{package.package_name}'
        body = f'{package.package_name}（{package.credits}+{package.bonus_credits} 积分）'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'credit_pack',
            'subject': subject,
            'body': body,
            'target_tier': None,
            'billing_cycle': None,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {
                'app_code': app_code,
                'package_id': package.id,
                'credit_amount': float(total_credits),
            },
            # MK-3：商品目录引用快照；plan_key=积分包名（对齐 seed cp.package_name）。
            'offering_ref': build_offering_ref(
                'credit_pack', offering_key='credits:topup', plan_key=package.package_name
            ),
        }
        await pay_order_dao.create(db, order_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel,
            merchant_config,
            order_no=order_no,
            amount=pay_amount,
            subject=subject,
            body=body,
            user_ip=user_ip,
        )

        return CreatePayOrderResponse(
            order_no=order_no,
            pay_amount=pay_amount,
            channel_code=channel.code,
            qr_code_url=qr_code_url,
            pay_url=pay_url,
            contract_no=None,
            expire_time=expire_time,
        )

    @staticmethod
    async def _create_app_purchase_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        """AI-Native 应用购买真实下单（设计 §5.4）。

        从 `hasn_app_catalog` 取价（须 published + access_type=purchase + 有价）；app_id 写入
        `extra_data.app_id`，到账由 `handle_app_purchase_paid` 回调读取并写 owner 维度权益。
        已有有效权益的不重复下单（幂等前置，避免重复扣费）。
        """
        # 延迟导入避免 pay → hasn 顶层循环依赖（与 user_tier dao 同为业务定价来源）。
        from backend.app.hasn_core.app_platform import app_catalog_service

        app_id = obj.app_id
        if not app_id:
            raise errors.RequestError(msg='应用购买订单缺少应用标识')
        catalog = await app_catalog_service.get_published_catalog(db, app_id=app_id)
        if catalog is None:
            raise errors.RequestError(msg=f'应用不存在或已下架: {obj.app_id}')
        if (catalog.access_type or 'free') != 'purchase':
            raise errors.RequestError(msg='该应用不是购买型，无需下单')
        if catalog.price_amount is None or float(catalog.price_amount) <= 0:
            raise errors.RequestError(msg='该应用未配置购买价格')
        # P1-4：个人只能购买 purchasable_by ∈ {owner, both} 的应用（纯企业应用个人买了也用不了）。
        app_catalog_service.check_purchasable_by(catalog, buyer='owner')

        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        if owner_hasn_id and await app_catalog_service.get_active_entitlement(
            db, app_id=catalog.app_id, subject_type='owner', subject_id=owner_hasn_id
        ):
            raise errors.RequestError(msg='已拥有该应用，无需重复购买')

        pay_amount = int(float(catalog.price_amount) * 100)
        subject = f'唤星AI-应用购买-{catalog.name}'
        body = f'购买应用「{catalog.name}」'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'app_purchase',
            'subject': subject,
            'body': body,
            'target_tier': None,
            # catalog.billing_cycle（once/month/year）→ 回调据此算权益到期。
            'billing_cycle': catalog.billing_cycle,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {'app_code': app_code, 'app_id': catalog.app_id},
            # MK-3：商品目录引用快照；offering_key=app:<id>（对齐 seed catalog 收编）。
            'offering_ref': build_offering_ref(
                'app_purchase', offering_key=f'app:{catalog.app_id}', plan_key='standard'
            ),
        }
        await pay_order_dao.create(db, order_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel,
            merchant_config,
            order_no=order_no,
            amount=pay_amount,
            subject=subject,
            body=body,
            user_ip=user_ip,
        )

        return CreatePayOrderResponse(
            order_no=order_no,
            pay_amount=pay_amount,
            channel_code=channel.code,
            qr_code_url=qr_code_url,
            pay_url=pay_url,
            contract_no=None,
            expire_time=expire_time,
        )

    @staticmethod
    async def _create_app_seat_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        """企业席位购买真实下单（doc04 §6.4）。

        价 = ``catalog.price_amount × seats``；下单主体 enterprise，到账由 ``handle_app_seat_paid``
        回调读 ``extra_data``（app_id/enterprise_id/seats）累加 ``seats_total``（S2 两步累加，非覆盖）。
        RBAC（仅企业 owner/admin）由上游 app-scope 端点把守，本方法只做定价 + 下单。
        """
        from backend.app.hasn_core.app_platform import app_catalog_service

        app_id = obj.app_id
        enterprise_id = obj.enterprise_id
        seats = obj.seats
        if not app_id:
            raise errors.RequestError(msg='企业席位订单缺少应用标识')
        if enterprise_id is None:
            raise errors.RequestError(msg='企业席位订单缺少企业标识')
        if seats is None or seats <= 0:
            raise errors.RequestError(msg='企业席位订单的席位数必须大于零')
        catalog = await app_catalog_service.get_published_catalog(db, app_id=app_id)
        if catalog is None:
            raise errors.RequestError(msg=f'应用不存在或已下架: {obj.app_id}')
        if (catalog.access_type or 'free') != 'purchase':
            raise errors.RequestError(msg='该应用不是购买型，无需下单')
        if catalog.price_amount is None or float(catalog.price_amount) <= 0:
            raise errors.RequestError(msg='该应用未配置购买价格')
        # P1-4：企业只能购买 purchasable_by ∈ {enterprise, both} 的应用。
        app_catalog_service.check_purchasable_by(catalog, buyer='enterprise')

        pay_amount = int(float(catalog.price_amount) * 100) * seats
        subject = f'唤星AI-企业席位-{catalog.name}×{seats}'
        body = f'企业购买应用「{catalog.name}」{seats} 席'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'app_seat',
            'subject': subject,
            'body': body,
            'target_tier': None,
            'billing_cycle': catalog.billing_cycle,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {
                'app_code': app_code,
                'app_id': catalog.app_id,
                'enterprise_id': enterprise_id,
                'seats': seats,
            },
            # MK-3：商品目录引用快照；offering_key=seat:<id>（席位 feature 前缀族）。
            'offering_ref': build_offering_ref('app_seat', offering_key=f'seat:{catalog.app_id}', plan_key='standard'),
        }
        await pay_order_dao.create(db, order_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel,
            merchant_config,
            order_no=order_no,
            amount=pay_amount,
            subject=subject,
            body=body,
            user_ip=user_ip,
        )

        return CreatePayOrderResponse(
            order_no=order_no,
            pay_amount=pay_amount,
            channel_code=channel.code,
            qr_code_url=qr_code_url,
            pay_url=pay_url,
            contract_no=None,
            expire_time=expire_time,
        )

    @staticmethod
    async def _create_lead_pack_order(
        *,
        db: AsyncSession,
        user_id: int,
        obj: CreatePayOrderParam,
        user_ip: str | None,
        app_code: str,
    ) -> CreatePayOrderResponse:
        """线索购买真实下单（doc93 §4.2 线索付费）。

        线索是**独立支付商品**，按条计价（`GROWTH_LEAD_UNIT_PRICE_FEN` 分/条·运营改环境变量不动代码），
        **不走 new-api 积分**（doc93 line 165 铁律）。lead_count 写入 `extra_data`，到账由
        `handle_lead_pack_paid` 回调读取并增加用户可领取线索额度（purchased_balance）。
        定价纯由 config 决定，**不耦合 hasn_growth**（额度发放经回调注册机制解耦）。
        """
        count = int(obj.lead_count or 0)
        if count <= 0:
            raise errors.RequestError(msg='购买线索条数必须大于 0')
        unit_fen = int(settings.GROWTH_LEAD_UNIT_PRICE_FEN)
        if unit_fen <= 0:
            raise errors.RequestError(msg='线索单价未配置（GROWTH_LEAD_UNIT_PRICE_FEN）')
        pay_amount = unit_fen * count

        subject = f'唤星AI-线索购买-{count}条'
        body = f'购买线索 {count} 条（获客公共池）'

        channel, merchant_config = await PayOrderService._resolve_channel(db, obj.channel_code)

        order_no = _generate_order_no()
        now = timezone.now()
        expire_time = now + timedelta(minutes=ORDER_EXPIRE_MINUTES)

        order_dict = {
            'order_no': order_no,
            'user_id': user_id,
            'channel_id': channel.id,
            'channel_code': channel.code,
            'order_type': 'lead_pack',
            'subject': subject,
            'body': body,
            'target_tier': None,
            'billing_cycle': None,
            'amount': pay_amount,
            'pay_amount': pay_amount,
            'expire_time': expire_time,
            'user_ip': user_ip,
            'extra_data': {'app_code': app_code, 'lead_count': count, 'unit_price_fen': unit_fen},
            # MK-3：商品目录引用快照；线索包无 offering 行（发货只认 kind=lead_pack）。
            'offering_ref': build_offering_ref('lead_pack'),
        }
        await pay_order_dao.create(db, order_dict)

        qr_code_url, pay_url = PayOrderService._invoke_channel_create(
            channel,
            merchant_config,
            order_no=order_no,
            amount=pay_amount,
            subject=subject,
            body=body,
            user_ip=user_ip,
        )

        return CreatePayOrderResponse(
            order_no=order_no,
            pay_amount=pay_amount,
            channel_code=channel.code,
            qr_code_url=qr_code_url,
            pay_url=pay_url,
            contract_no=None,
            expire_time=expire_time,
        )

    @staticmethod
    def _invoke_channel_refund(
        channel,
        merchant_config: dict | None,
        *,
        order_no: str,
        refund_no: str,
        refund_amount: int,
        total_amount: int,
        reason: str,
    ) -> dict[str, Any]:
        """调渠道 SDK 退款（外部边界）。SDK 失败 → ServerError（refund_order 事务回滚，不落退款记录）。"""
        try:
            client = get_pay_client(channel, merchant_config=merchant_config)
            return client.refund(
                order_no=order_no,
                refund_no=refund_no,
                refund_amount=refund_amount,
                total_amount=total_amount,
                reason=reason,
            )
        except Exception as e:
            log.error(f'SDK 退款失败: channel={channel.code}, error={e}', exc_info=True)
            raise errors.ServerError(msg=f'支付渠道退款失败: {e}')

    @staticmethod
    async def refund_order(
        *,
        db: AsyncSession,
        order_no: str,
        reason: str = '',
        refund_amount: int | None = None,
        operator_id: int | None = None,
    ) -> RefundOrderResponse:
        """管理端发起退款（MK-9 退款编排层·统一商业化内核唯一退款入口）。

        编排步骤（全程在调用方的**单一事务** ``db`` 内，与订单状态翻转原子提交）：
          ① 行锁取订单 → 幂等短路（已 status=2 直接返回既有退款记录）+ 校验「已支付」；
          ② **先按 offering.kind 回收权益/额度**（``reverse_fulfillment`` fail-closed——回收失败即抛，
             绝不进入退款：不退钱不回收 > 退钱不回收）；
          ③ 调渠道 SDK 退款（外部边界，退款单号确定性 ``RF{order_no}`` → 渠道/DB 双层幂等，
             重试复用同一 out_refund_no 由渠道去重，规避重复退款）；
          ④ 落 ``pay_refund``（status=1 成功）+ 订单 ``status=2 已退款`` + ``refund_amount``。

        :raises NotFoundError: 订单不存在。
        :raises RequestError: 订单非「已支付」态 / 金额非法 / kind 未注册回收处理器（拒绝退款）。
        :raises ServerError: 渠道退款 SDK 失败。
        """
        order = await pay_order_dao.get_by_order_no_for_update(db, order_no)
        if not order:
            raise errors.NotFoundError(msg=f'订单 {order_no} 不存在')

        # 退款单号确定性派生（一订单一全额退款）——渠道与 DB 双层幂等的锚。
        refund_no = f'RF{order.order_no}'

        # 幂等：已退款直接返回既有退款记录（行锁串行化并发退款请求）。
        if order.status == 2:
            existing = await pay_refund_dao.get_by_refund_no(db, refund_no)
            return RefundOrderResponse(
                order_no=order.order_no,
                refund_no=refund_no,
                refund_amount=int(order.refund_amount or (existing.refund_amount if existing else order.pay_amount)),
                status=int(existing.status if existing else 1),
                already_refunded=True,
            )

        if order.status != 1:
            raise errors.RequestError(msg='只有已支付订单可退款（当前订单状态不允许退款）')

        amount = int(refund_amount) if refund_amount is not None else int(order.pay_amount)
        if amount <= 0 or amount > int(order.pay_amount):
            raise errors.RequestError(msg=f'退款金额非法: {amount} 分（订单实付 {order.pay_amount} 分）')

        # ② 先回收权益/额度（fail-closed·同事务）——回收失败即抛，绝不退钱不回收。
        await reverse_fulfillment(db, order)

        # ③ 渠道退款（外部边界）。
        if not order.channel_code:
            raise errors.RequestError(msg='订单缺少支付渠道，无法退款')
        channel, merchant_config = await PayOrderService._resolve_channel(db, order.channel_code)
        refund_reason = reason or '管理端退款'
        result = PayOrderService._invoke_channel_refund(
            channel,
            merchant_config,
            order_no=order.order_no,
            refund_no=refund_no,
            refund_amount=amount,
            total_amount=int(order.pay_amount),
            reason=refund_reason,
        )
        channel_refund_no = (result or {}).get('channel_refund_no') or (result or {}).get('refund_id')

        # ④ 落退款记录 + 订单状态→已退款（同事务原子提交）。
        now = timezone.now()
        await pay_refund_dao.create(
            db,
            {
                'refund_no': refund_no,
                'order_no': order.order_no,
                'user_id': order.user_id,
                'refund_amount': amount,
                'channel_code': order.channel_code,
                'reason': refund_reason,
                'channel_refund_no': channel_refund_no,
                'status': 1,
                'success_time': now,
            },
        )
        await pay_order_dao.mark_refunded(db, order_no=order.order_no, refund_amount=amount)
        log.info(
            f'[Refund] 退款完成: order_no={order.order_no}, refund_no={refund_no}, '
            f'amount={amount}分, kind={(order.offering_ref or {}).get("kind")}, operator={operator_id}'
        )
        return RefundOrderResponse(
            order_no=order.order_no,
            refund_no=refund_no,
            refund_amount=amount,
            status=1,
            already_refunded=False,
        )

    @staticmethod
    async def get_refund_list(*, db: AsyncSession, order_no: str | None = None) -> dict[str, Any]:
        """分页查询退款记录（管理端）。"""
        select_stmt = await pay_refund_dao.get_select(order_no=order_no)
        return await paging_data(db, select_stmt)

    @staticmethod
    async def confirm_refund_notify(
        *,
        db: AsyncSession,
        refund_no: str,
        refund_status: str,
        channel_refund_no: str | None = None,
    ) -> bool:
        """渠道退款异步回调确认（幂等·仅确认，不重复回收权益）。

        退款编排在 ``refund_order`` 发起时已同步：回收权益 → 调渠道 → 落 ``pay_refund``(status=1) →
        订单 status=2。本回调用于渠道**异步**退款（如微信退款先返 PROCESSING 后推 REFUND.SUCCESS）
        的**最终态确认**，只更新退款记录/订单状态，**绝不再跑 ``reverse_fulfillment``**（避免重复回收）。

        :param refund_status: 渠道退款态——``SUCCESS`` 成功 / ``PROCESSING`` 处理中 / 其它（``CLOSED`` /
                              ``ABNORMAL``）视为失败。
        :return: 是否有状态变更（幂等重放返回 False）。
        """
        refund = await pay_refund_dao.get_by_refund_no(db, refund_no)
        if not refund:
            # 收到未知退款单号的回调——不是本内核发起的退款（正常不该发生），记日志忽略。
            log.warning(f'[Refund] 退款回调未匹配到退款记录: refund_no={refund_no}, status={refund_status}')
            return False

        status_upper = (refund_status or '').upper()
        if status_upper == 'SUCCESS':
            target_status = 1
        elif status_upper == 'PROCESSING':
            # 处理中——保持现状，等待终态回调。
            return False
        else:
            target_status = 2  # CLOSED / ABNORMAL 等 → 退款失败

        # 幂等：状态未变化直接返回（渠道回调可能重复投递）。
        if int(refund.status) == target_status:
            return False

        now = timezone.now()
        await pay_refund_dao.update_status(
            db,
            refund_no,
            status=target_status,
            channel_refund_no=channel_refund_no or refund.channel_refund_no,
            success_time=now if target_status == 1 else None,
        )

        if target_status == 1:
            # 成功终态——补确保订单已置退款态（发起时通常已置，兜底幂等）。
            order = await pay_order_dao.get_by_order_no_for_update(db, refund.order_no)
            if order and order.status != 2:
                await pay_order_dao.mark_refunded(db, order_no=refund.order_no, refund_amount=int(refund.refund_amount))
            log.info(f'[Refund] 退款回调确认成功: refund_no={refund_no}, order_no={refund.order_no}')
        else:
            # 失败终态——发起时已乐观回收权益+置退款态，此处渠道最终判失败 → 需人工对账（钱未退但权益已收回）。
            log.critical(
                f'[Refund] 渠道退款最终失败，需人工对账: refund_no={refund_no}, '
                f'order_no={refund.order_no}, channel_status={refund_status}（权益已在发起时回收）'
            )
        return True

    @staticmethod
    async def cancel_order(*, db: AsyncSession, order_no: str, user_id: int) -> None:
        order = await pay_order_dao.get_by_order_no(db, order_no)
        if not order:
            raise errors.NotFoundError(msg='订单不存在')
        if order.user_id != user_id:
            raise errors.ForbiddenError(msg='无权操作此订单')
        if order.status != 0:
            raise errors.RequestError(msg='订单状态不允许取消')
        await pay_order_dao.update_status(db, order_no, status=3)

    @staticmethod
    async def handle_pay_notify(
        *,
        db: AsyncSession,
        order_no: str,
        channel_order_no: str,
        pay_amount: int,
        channel_code: str,
        channel_user_id: str | None = None,
        raw_data: str | None = None,
    ) -> bool:
        await pay_notify_log_dao.create(
            db,
            {
                'notify_type': 'pay',
                'order_no': order_no,
                'channel_code': channel_code,
                'notify_data': raw_data,
                'status': 0,
            },
        )

        order = await pay_order_dao.get_by_order_no_for_update(db, order_no)
        if not order:
            raise errors.NotFoundError(msg=f'订单 {order_no} 不存在')

        if order.status == 1:
            return False

        if order.pay_amount != pay_amount:
            raise errors.RequestError(msg=f'金额不一致: 订单 {order.pay_amount} vs 回调 {pay_amount}')

        now = timezone.now()
        await pay_order_dao.update_status(
            db,
            order_no=order_no,
            status=1,
            channel_order_no=channel_order_no,
            channel_user_id=channel_user_id,
            success_time=now,
        )

        # MK-3：优先按商品目录 offering_ref.kind 分发履约（内核唯一发货入口）；
        #        无 offering_ref 的存量订单回落 order_type 旧分发（向后兼容，逐步收敛）。
        if not await dispatch_fulfillment(order):
            await dispatch_pay_success(order.order_type, order)
        return True

    @staticmethod
    async def expire_timeout_orders(*, db: AsyncSession) -> int:
        return await pay_order_dao.expire_timeout_orders(db)


pay_order_service: PayOrderService = PayOrderService()
